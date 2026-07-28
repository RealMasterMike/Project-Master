from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from project_master.integrations.voice.engine import (
    CancellationAck,
    EngineHealth,
    EngineRecovery,
    EngineRenderRequest,
    RenderedAudio,
)
from project_master.integrations.voice.manifests import (
    ESPEAK_NG_PACK_TEMPLATE,
    InstalledEnginePack,
)

_CONTROL = re.compile(
    r"\b(?P<key>voice|pitch|amplitude|word_gap)\s*=\s*(?P<value>[A-Za-z0-9+_.-]+)",
    re.IGNORECASE,
)


class EspeakNgAdapter:
    """Safe subprocess adapter for the distribution-provided eSpeak NG binary."""

    engine_id = "espeak-ng"
    capabilities = frozenset(ESPEAK_NG_PACK_TEMPLATE.capabilities)
    max_chunk_characters = 4_000

    def __init__(
        self,
        executable: str | Path | None = None,
        ffmpeg: str | Path | None = None,
    ) -> None:
        self.executable = str(executable or shutil.which("espeak-ng") or "")
        self.ffmpeg = str(ffmpeg or shutil.which("ffmpeg") or "")
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def health(self, _pack: InstalledEnginePack) -> EngineHealth:
        if not self.executable:
            return EngineHealth(
                available=False,
                status="offline",
                detail="eSpeak NG is not installed.",
            )
        if not self.ffmpeg:
            return EngineHealth(
                available=False,
                status="incompatible",
                detail="ffmpeg is required to normalize the voice output contract.",
            )
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        except (OSError, TimeoutError) as exc:
            return EngineHealth(
                available=False,
                status="error",
                detail=f"eSpeak NG probe failed ({type(exc).__name__}).",
            )
        detail = output.decode("utf-8", errors="replace").splitlines()[0][:500]
        return EngineHealth(
            available=process.returncode == 0,
            status="ready" if process.returncode == 0 else "error",
            detail=detail,
        )

    async def render_chunk(self, request: EngineRenderRequest) -> RenderedAudio:
        if request.reference_artifact_ids:
            raise ValueError("eSpeak NG does not support reference voice cloning.")
        if not self.executable or not self.ffmpeg:
            raise RuntimeError("eSpeak NG and ffmpeg are required.")
        controls = _description_controls(request.designed_voice_description or "")
        voice = controls.get("voice") or _language_voice(request.chunk.language)
        pitch = _bounded_int(controls.get("pitch"), 50, 0, 99)
        amplitude = _bounded_int(controls.get("amplitude"), 100, 0, 200)
        word_gap = _bounded_int(controls.get("word_gap"), 0, 0, 100)
        speed = max(80, min(450, round(175 * request.chunk.speed)))
        text = _apply_pronunciations(request)

        with tempfile.TemporaryDirectory(prefix="project-master-voice-") as raw_temp:
            temp = Path(raw_temp)
            source = temp / "source.wav"
            output = temp / f"output.{request.chunk.output_format}"
            command = [
                self.executable,
                "-v",
                voice,
                "-s",
                str(speed),
                "-p",
                str(pitch),
                "-a",
                str(amplitude),
                "-g",
                str(word_gap),
                "-w",
                str(source),
                text,
            ]
            await self._run(request.job_id, command)
            if not source.is_file() or source.stat().st_size == 0:
                raise RuntimeError("eSpeak NG produced no audio.")
            filters: list[str] = []
            if request.chunk.normalize_loudness:
                filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
            if request.chunk.pause_after_ms:
                filters.append(
                    f"apad=pad_dur={request.chunk.pause_after_ms / 1000:.3f}"
                )
            ffmpeg_command = [
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
                ffmpeg_command.extend(["-af", ",".join(filters)])
            ffmpeg_command.extend(
                [
                    "-ar",
                    str(request.chunk.sample_rate_hz),
                    "-ac",
                    str(request.chunk.channels),
                    str(output),
                ]
            )
            await self._run(request.job_id, ffmpeg_command)
            content = output.read_bytes()
            duration = _wav_duration(output) if output.suffix == ".wav" else _wav_duration(source)
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
            duration_seconds=max(duration + request.chunk.pause_after_ms / 1000, 0.001),
            engine_run_id=(
                "espeak-"
                + hashlib.sha256(
                    f"{request.job_id}:{request.chunk.id}".encode()
                ).hexdigest()[:24]
            ),
        )

    async def cancel(self, job_id: str) -> CancellationAck:
        async with self._lock:
            process = self._processes.get(job_id)
            if process is None or process.returncode is not None:
                return CancellationAck(
                    accepted=True,
                    confirmed=True,
                    detail="No eSpeak NG process is running for this job.",
                )
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        return CancellationAck(
            accepted=True,
            confirmed=True,
            detail="The eSpeak NG process stopped.",
        )

    async def recover(self, job_id: str) -> EngineRecovery:
        async with self._lock:
            process = self._processes.get(job_id)
            if process is None:
                return EngineRecovery(status="not_found")
            if process.returncode is None:
                return EngineRecovery(
                    status="running",
                    detail="The local eSpeak NG subprocess is still running.",
                )
            return EngineRecovery(
                status="failed" if process.returncode else "not_found",
                detail=f"eSpeak NG exited with code {process.returncode}.",
            )

    async def _run(self, job_id: str, command: list[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with self._lock:
            self._processes[job_id] = process
        try:
            _stdout, stderr = await process.communicate()
        finally:
            async with self._lock:
                if self._processes.get(job_id) is process:
                    self._processes.pop(job_id, None)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise RuntimeError(
                f"Voice subprocess exited with code {process.returncode}: {detail}"
            )


def discover_espeak_pack(
    adapter: EspeakNgAdapter | None = None,
) -> InstalledEnginePack | None:
    active = adapter or EspeakNgAdapter()
    if not active.executable or not active.ffmpeg:
        return None
    try:
        completed = subprocess.run(
            [active.executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", completed.stdout)
    version = match.group(1) if match else "system"
    return InstalledEnginePack.from_template(
        ESPEAK_NG_PACK_TEMPLATE,
        installed_version=version,
        assets=(),
    )


def _description_controls(description: str) -> dict[str, str]:
    return {
        match.group("key").casefold(): match.group("value")
        for match in _CONTROL.finditer(description)
    }


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None or not value.isdigit():
        return default
    return min(max(int(value), minimum), maximum)


def _language_voice(language: str) -> str:
    normalized = language.strip().lower()
    aliases = {
        "en": "en-us",
        "en-us": "en-us",
        "en-gb": "en-gb",
        "zh": "cmn",
        "zh-cn": "cmn",
        "fr": "fr-fr",
        "pt": "pt",
    }
    return aliases.get(normalized, normalized)


def _apply_pronunciations(request: EngineRenderRequest) -> str:
    text = request.chunk.text
    for entry in request.chunk.pronunciations:
        if entry.alphabet != "plain":
            continue
        flags = 0 if entry.case_sensitive else re.IGNORECASE
        text = re.sub(re.escape(entry.term), entry.pronunciation, text, flags=flags)
    return text


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()
