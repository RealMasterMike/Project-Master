from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from project_master.integrations.voice import chatterbox_worker
from project_master.integrations.voice.engine import (
    CancellationAck,
    EngineHealth,
    EngineRecovery,
    EngineRenderRequest,
    RenderedAudio,
)
from project_master.integrations.voice.manifests import (
    CHATTERBOX_PACK_TEMPLATE,
    InstalledEnginePack,
    ModelAsset,
    ModelAssetFormat,
)

ReferenceResolver = Callable[[str], Path]


def _external_process_environment() -> dict[str, str]:
    """Remove packaged-runtime overrides before launching host executables."""

    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        environment.pop(name, None)
    if os.name != "nt":
        original_library_path = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original_library_path:
            environment["LD_LIBRARY_PATH"] = original_library_path
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    return environment


class ChatterboxWorkerAdapter:
    """Persistent, isolated neural voice-clone worker for a user-managed engine venv."""

    engine_id = "chatterbox"
    capabilities = frozenset(CHATTERBOX_PACK_TEMPLATE.capabilities)
    max_chunk_characters = 500

    def __init__(
        self,
        python_executable: str | Path,
        engine_root: str | Path,
        reference_resolver: ReferenceResolver,
        *,
        worker_script: str | Path | None = None,
        ffmpeg: str | Path | None = None,
        request_timeout_seconds: float = 900,
    ) -> None:
        # Preserve the venv launcher path. Resolving its symlink selects the base
        # interpreter and silently drops the voice engine's site-packages.
        self.python_executable = str(Path(python_executable).expanduser().absolute())
        self.engine_root = Path(engine_root).resolve()
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.model_root = self.engine_root / "models"
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.reference_resolver = reference_resolver
        selected_worker = (
            Path(worker_script).resolve()
            if worker_script is not None
            else resolve_chatterbox_worker_script()
        )
        if selected_worker is None:
            raise FileNotFoundError(
                "The packaged Chatterbox worker resource is unavailable."
            )
        self.worker_script = str(selected_worker)
        self.ffmpeg = str(ffmpeg or shutil.which("ffmpeg") or "")
        self.request_timeout_seconds = request_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._request_lock = asyncio.Lock()
        self._active_job_id: str | None = None

    async def health(self, _pack: InstalledEnginePack) -> EngineHealth:
        if not Path(self.python_executable).is_file():
            return EngineHealth(
                available=False,
                status="offline",
                detail="The isolated Chatterbox Python environment is not installed.",
            )
        try:
            process = await asyncio.create_subprocess_exec(
                self.python_executable,
                self.worker_script,
                "--health",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._environment(offline=True),
            )
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30,
            )
            payload = _last_json(stdout)
        except (OSError, TimeoutError, ValueError) as exc:
            return EngineHealth(
                available=False,
                status="error",
                detail=f"Chatterbox health probe failed ({type(exc).__name__}).",
            )
        if process.returncode != 0 or not payload.get("ok"):
            detail = str(payload.get("error", "Model files are not cached locally."))[:500]
            return EngineHealth(
                available=False,
                status="offline",
                detail=detail,
            )
        return EngineHealth(
            available=True,
            status="ready",
            detail=(
                f"Chatterbox {payload.get('version', 'unknown')} ready on "
                f"{payload.get('device', 'unknown')}."
            ),
        )

    async def render_chunk(self, request: EngineRenderRequest) -> RenderedAudio:
        if len(request.reference_artifact_ids) != 1:
            raise ValueError("Chatterbox renders require exactly one voice reference.")
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is required to normalize Chatterbox output.")
        reference = self.reference_resolver(request.reference_artifact_ids[0]).resolve(
            strict=True
        )
        async with self._request_lock:
            process = await self._ensure_worker()
            self._active_job_id = request.job_id
            try:
                with tempfile.TemporaryDirectory(
                    prefix="project-master-chatterbox-",
                    dir=self.engine_root,
                ) as raw_temp:
                    temp = Path(raw_temp)
                    source = temp / "source.wav"
                    output = temp / f"output.{request.chunk.output_format}"
                    payload = {
                        "type": "render",
                        "request_id": request.chunk.id,
                        "text": request.chunk.text,
                        "language": _language_code(request.chunk.language),
                        "reference_path": str(reference),
                        "output_path": str(source),
                        "seed": request.chunk.seed,
                        "exaggeration": _direction_exaggeration(
                            request.chunk.performance_direction
                        ),
                        "cfg_weight": 0.5,
                        "temperature": 0.8,
                    }
                    await self._send(process, payload)
                    response = await self._receive(process)
                    if not response.get("ok"):
                        raise RuntimeError(
                            f"Chatterbox worker failed: {response.get('error', 'unknown error')}"
                        )
                    if response.get("request_id") != request.chunk.id:
                        raise RuntimeError("Chatterbox worker response ID did not match.")
                    await self._normalize(request, source, output)
                    content = output.read_bytes()
                    duration = _audio_duration(
                        output if output.suffix == ".wav" else source
                    )
            finally:
                self._active_job_id = None
        media_type = {
            "wav": "audio/wav",
            "flac": "audio/flac",
            "mp3": "audio/mpeg",
            "opus": "audio/opus",
            "aac": "audio/aac",
        }[request.chunk.output_format]
        return RenderedAudio(
            content=content,
            format=request.chunk.output_format,
            media_type=media_type,
            sample_rate_hz=request.chunk.sample_rate_hz,
            channels=request.chunk.channels,
            duration_seconds=max(duration, 0.001),
            engine_run_id=f"chatterbox-{request.chunk.id.removeprefix('voice-chunk-')}",
        )

    async def cancel(self, job_id: str) -> CancellationAck:
        process = self._process
        if process is None or process.returncode is not None:
            return CancellationAck(
                accepted=True,
                confirmed=True,
                detail="No Chatterbox worker is running.",
            )
        if self._active_job_id != job_id:
            return CancellationAck(
                accepted=False,
                confirmed=False,
                detail="The running Chatterbox request belongs to a different job.",
            )
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
        self._process = None
        return CancellationAck(
            accepted=True,
            confirmed=True,
            detail="The isolated Chatterbox worker stopped.",
        )

    async def recover(self, job_id: str) -> EngineRecovery:
        process = self._process
        if (
            process is not None
            and process.returncode is None
            and self._active_job_id == job_id
        ):
            return EngineRecovery(
                status="running",
                detail="The Chatterbox worker is still rendering this job.",
            )
        return EngineRecovery(
            status="not_found",
            detail="Chatterbox requests cannot survive a backend process restart.",
        )

    async def _ensure_worker(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        process = await asyncio.create_subprocess_exec(
            self.python_executable,
            self.worker_script,
            "--serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._environment(offline=True),
        )
        try:
            ready = await asyncio.wait_for(process.stdout.readline(), timeout=30)  # type: ignore[union-attr]
            payload = json.loads(ready)
            if payload.get("type") != "ready" or payload.get("protocol") != 1:
                raise RuntimeError("Chatterbox worker returned an invalid handshake.")
        except Exception:
            process.kill()
            await process.wait()
            raise
        self._process = process
        return process

    async def _send(
        self,
        process: asyncio.subprocess.Process,
        payload: dict[str, Any],
    ) -> None:
        if process.stdin is None:
            raise RuntimeError("Chatterbox worker input pipe is unavailable.")
        process.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await process.stdin.drain()

    async def _receive(
        self,
        process: asyncio.subprocess.Process,
    ) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("Chatterbox worker output pipe is unavailable.")
        raw = await asyncio.wait_for(
            process.stdout.readline(),
            timeout=self.request_timeout_seconds,
        )
        if not raw:
            self._process = None
            raise RuntimeError(
                f"Chatterbox worker stopped unexpectedly (exit={process.returncode})."
            )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("Chatterbox worker returned invalid JSON.")
        return value

    async def _normalize(
        self,
        request: EngineRenderRequest,
        source: Path,
        output: Path,
    ) -> None:
        filters: list[str] = []
        if request.chunk.speed != 1.0:
            filters.append(f"atempo={request.chunk.speed:.4f}")
        if request.chunk.normalize_loudness:
            filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
        if request.chunk.pause_after_ms:
            filters.append(
                f"apad=pad_dur={request.chunk.pause_after_ms / 1000:.3f}"
            )
        command = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
        ]
        if filters:
            command.extend(["-af", ",".join(filters)])
        command.extend(
            [
                "-ar",
                str(request.chunk.sample_rate_hz),
                "-ac",
                str(request.chunk.channels),
                str(output),
            ]
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=_external_process_environment(),
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise RuntimeError(f"ffmpeg normalization failed: {detail}")

    def _environment(self, *, offline: bool) -> dict[str, str]:
        environment = _external_process_environment()
        environment.update(
            {
                "HF_HOME": str(self.model_root),
                "HUGGINGFACE_HUB_CACHE": str(self.model_root / "hub"),
                "PKUSEG_HOME": str(self.engine_root / "pkuseg"),
                "PROJECT_MASTER_VOICE_ENGINE_ROOT": str(self.engine_root),
                "PYTHONUNBUFFERED": "1",
            }
        )
        if offline:
            environment["HF_HUB_OFFLINE"] = "1"
            environment["TRANSFORMERS_OFFLINE"] = "1"
        return environment


def chatterbox_python(engine_root: str | Path) -> Path:
    candidates = _chatterbox_python_candidates(Path(engine_root))
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def _chatterbox_python_candidates(root: Path) -> tuple[Path, ...]:
    if os.name == "nt":
        return (
            root / "venv314" / "Scripts" / "python.exe",
            root / "venv" / "Scripts" / "python.exe",
        )
    return (
        root / "venv314" / "bin" / "python",
        root / "venv" / "bin" / "python",
    )


def discover_chatterbox_pack(
    engine_root: str | Path,
    reference_resolver: ReferenceResolver | None = None,
) -> tuple[InstalledEnginePack, ChatterboxWorkerAdapter] | None:
    root = Path(engine_root).resolve()
    probe = (
        "import importlib.metadata as m; "
        "print(m.version('chatterbox-tts'), end='')"
    )
    python: Path | None = None
    version = ""
    for candidate in _chatterbox_python_candidates(root):
        if not candidate.is_file():
            continue
        try:
            result = subprocess.run(
                [str(candidate), "-c", probe],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env=_external_process_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        version = result.stdout.strip()
        if result.returncode == 0 and version:
            python = candidate
            break
    if python is None:
        return None
    worker_script = resolve_chatterbox_worker_script()
    if worker_script is None:
        return None
    inventory = _load_verified_inventory(root)
    if inventory is None:
        return None
    source_revision, assets = inventory
    pack = InstalledEnginePack.from_template(
        CHATTERBOX_PACK_TEMPLATE,
        installed_version=f"{version[:70]}+git.{source_revision[:12]}",
        assets=assets,
        pack_id="chatterbox-local-runtime",
    )
    adapter = ChatterboxWorkerAdapter(
        python,
        root,
        reference_resolver=reference_resolver or _missing_reference,
        worker_script=worker_script,
    )
    return pack, adapter


def resolve_chatterbox_worker_script() -> Path | None:
    """Locate the source worker in development or its explicit PyInstaller data copy."""

    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(
            Path(bundle_root)
            / "project_master_worker_data"
            / "chatterbox_worker.py"
        )
    module_file = getattr(chatterbox_worker, "__file__", None)
    if module_file:
        candidates.append(Path(module_file))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _load_verified_inventory(
    root: Path,
) -> tuple[str, tuple[ModelAsset, ...]] | None:
    inventory_path = root / "asset-inventory.json"
    try:
        if not inventory_path.is_file() or inventory_path.stat().st_size > 1_000_000:
            return None
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("model_repo") != chatterbox_worker._REPO_ID
            or payload.get("model_revision") != chatterbox_worker._MODEL_REVISION
            or payload.get("engine_source_revision")
            != chatterbox_worker._ENGINE_SOURCE_REVISION
            or not isinstance(payload.get("assets"), list)
        ):
            return None
        source_revision = str(payload["engine_source_revision"])
        formats = {
            "t3_weights": ModelAssetFormat.SAFETENSORS,
            "voice_encoder_checkpoint": ModelAssetFormat.PYTORCH_CHECKPOINT,
            "generator_checkpoint": ModelAssetFormat.PYTORCH_CHECKPOINT,
            "conditioning_checkpoint": ModelAssetFormat.PYTORCH_CHECKPOINT,
            "tokenizer": ModelAssetFormat.TOKENIZER_JSON,
            "cangjie_map": ModelAssetFormat.JSON,
        }
        assets: list[ModelAsset] = []
        for item in payload["assets"]:
            if not isinstance(item, dict):
                return None
            logical_name = str(item.get("logical_name", ""))
            asset_format = formats.get(logical_name)
            if asset_format is None:
                return None
            relative_path = str(item.get("relative_path", ""))
            path = root / relative_path
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            if not path.is_file() or path.stat().st_size != item.get("size_bytes"):
                return None
            assets.append(
                ModelAsset(
                    logical_name=logical_name,
                    relative_path=relative_path,
                    format=asset_format,
                    sha256=str(item.get("sha256", "")),
                    size_bytes=int(item.get("size_bytes", 0)),
                )
            )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return source_revision, tuple(assets)


def _missing_reference(artifact_id: str) -> Path:
    raise KeyError(f"Unknown voice reference: {artifact_id}")


def _last_json(content: bytes) -> dict[str, Any]:
    for line in reversed(content.decode("utf-8", errors="replace").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Worker did not return JSON.")


def _language_code(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "en-us": "en",
        "en-gb": "en",
        "zh-cn": "zh",
        "pt-br": "pt",
        "es-mx": "es",
    }
    return aliases.get(normalized, normalized.split("-", 1)[0])


def _direction_exaggeration(value: str) -> float:
    lowered = value.casefold()
    if any(term in lowered for term in {"dramatic", "excited", "intense", "emotional"}):
        return 0.7
    if any(term in lowered for term in {"calm", "subtle", "neutral", "gentle"}):
        return 0.35
    return 0.5


def _audio_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()
