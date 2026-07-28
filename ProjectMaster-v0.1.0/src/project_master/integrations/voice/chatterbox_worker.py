"""Isolated Chatterbox worker.

This module intentionally uses only the standard library until a command actually probes or
loads Chatterbox. Project Master's packaged backend invokes it with a dedicated engine Python;
it is not imported into the backend's dependency environment.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO_ID = "ResembleAI/chatterbox"
_MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
_ENGINE_SOURCE_REVISION = "5de7a54aa4e5e2baadb0182dde554908b48b85c2"
_MODEL_PATTERNS = (
    "ve.pt",
    "t3_mtl23ls_v3.safetensors",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "conds.pt",
    "Cangjie5_TC.json",
)
_PKUSEG_FILES = (
    "spacy_ontonotes/features.msgpack",
    "spacy_ontonotes/weights.npz",
)
_ASSET_LOGICAL_NAMES = {
    "ve.pt": "voice_encoder_checkpoint",
    "t3_mtl23ls_v3.safetensors": "t3_weights",
    "s3gen.pt": "generator_checkpoint",
    "grapheme_mtl_merged_expanded_v1.json": "tokenizer",
    "conds.pt": "conditioning_checkpoint",
    "Cangjie5_TC.json": "cangjie_map",
}


def _json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _remove_worker_import_shadow() -> None:
    """Keep the sibling adapter module from shadowing Chatterbox's package.

    The packaged backend launches this file directly, which normally inserts its
    ``voice`` directory at the front of ``sys.path``. That directory also contains
    ``chatterbox.py`` (our adapter), so an unguarded ``import chatterbox`` would load
    the adapter instead of Resemble AI's isolated engine package.
    """

    worker_directory = Path(__file__).resolve().parent
    retained: list[str] = []
    for entry in sys.path:
        try:
            candidate = Path(entry or os.getcwd()).resolve()
        except OSError:
            retained.append(entry)
            continue
        if candidate != worker_directory:
            retained.append(entry)
    sys.path[:] = retained


def _cached_snapshot(*, download: bool) -> str:
    from huggingface_hub import snapshot_download

    return str(
        snapshot_download(
            repo_id=_REPO_ID,
            repo_type="model",
            revision=_MODEL_REVISION,
            allow_patterns=list(_MODEL_PATTERNS),
            local_files_only=not download,
            token=os.getenv("HF_TOKEN"),
        )
    )


def _engine_root() -> Path:
    configured = os.getenv("PROJECT_MASTER_VOICE_ENGINE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    hf_home = Path(os.getenv("HF_HOME", "~/.cache/huggingface")).expanduser().resolve()
    return hf_home.parent


def _pkuseg_home() -> Path:
    configured = os.getenv("PKUSEG_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return _engine_root() / "pkuseg"


def _ensure_pkuseg_cache() -> Path:
    home = _pkuseg_home()
    missing = [item for item in _PKUSEG_FILES if not (home / item).is_file()]
    if missing:
        raise FileNotFoundError(
            "The pinned Chinese segmenter cache is incomplete; run the explicit "
            "Chatterbox prefetch before normal use."
        )
    return home


def _prefetch_pkuseg() -> Path:
    home = _pkuseg_home()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["PKUSEG_HOME"] = str(home)
    from spacy_pkuseg import pkuseg

    pkuseg()
    return _ensure_pkuseg_cache()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_inventory(snapshot: Path, *, version: str) -> Path:
    root = _engine_root()
    assets: list[dict[str, Any]] = []
    for filename in _MODEL_PATTERNS:
        path = snapshot / filename
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        assets.append(
            {
                "logical_name": _ASSET_LOGICAL_NAMES[filename],
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": _sha256(resolved),
                "size_bytes": resolved.stat().st_size,
            }
        )
    payload = {
        "schema_version": 1,
        "engine_source_revision": _ENGINE_SOURCE_REVISION,
        "engine_version": version,
        "model_repo": _REPO_ID,
        "model_revision": _MODEL_REVISION,
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "assets": assets,
    }
    destination = root / "asset-inventory.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def _health() -> int:
    try:
        version = importlib.metadata.version("chatterbox-tts")
        import torch

        snapshot = _cached_snapshot(download=False)
        pkuseg_home = _ensure_pkuseg_cache()
        inventory = _engine_root() / "asset-inventory.json"
        if not inventory.is_file():
            raise FileNotFoundError(
                "The verified Chatterbox asset inventory is missing; run prefetch."
            )
    except Exception as exc:
        _json(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            }
        )
        return 1
    _json(
        {
            "ok": True,
            "version": version,
            "device": _device(),
            "cuda": bool(torch.cuda.is_available()),
            "model_snapshot": snapshot,
            "model_revision": _MODEL_REVISION,
            "engine_source_revision": _ENGINE_SOURCE_REVISION,
            "pkuseg_home": str(pkuseg_home),
            "asset_inventory": str(inventory),
        }
    )
    return 0


def _prefetch() -> int:
    try:
        version = importlib.metadata.version("chatterbox-tts")
        snapshot = Path(_cached_snapshot(download=True))
        pkuseg_home = _prefetch_pkuseg()
        inventory = _write_inventory(snapshot, version=version)
    except Exception as exc:
        _json(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            }
        )
        return 1
    _json(
        {
            "ok": True,
            "version": version,
            "model_snapshot": str(snapshot),
            "model_revision": _MODEL_REVISION,
            "engine_source_revision": _ENGINE_SOURCE_REVISION,
            "pkuseg_home": str(pkuseg_home),
            "asset_inventory": str(inventory),
        }
    )
    return 0


def _load_model() -> Any:
    _remove_worker_import_shadow()
    import chatterbox.models.tokenizers.tokenizer as tokenizer_module
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    snapshot = Path(_cached_snapshot(download=False)).resolve(strict=True)
    _ensure_pkuseg_cache()
    os.environ["PKUSEG_HOME"] = str(_pkuseg_home())

    def cached_hub_file(*_args: Any, filename: str, **_kwargs: Any) -> str:
        candidate = snapshot / Path(filename).name
        if candidate.parent != snapshot or not candidate.is_file():
            raise FileNotFoundError(f"Pinned Chatterbox asset is missing: {filename}")
        return str(candidate)

    with contextlib.redirect_stdout(sys.stderr):
        with patch.object(
            tokenizer_module,
            "hf_hub_download",
            side_effect=cached_hub_file,
        ):
            return ChatterboxMultilingualTTS.from_local(
                snapshot,
                device=_device(),
                t3_model="v3",
            )


def _render(model: Any, request: dict[str, Any]) -> dict[str, Any]:
    import torch

    text = str(request["text"])
    language = str(request["language"]).lower()
    reference = Path(str(request["reference_path"])).resolve(strict=True)
    output = Path(str(request["output_path"])).resolve()
    if not reference.is_file() or reference.suffix.lower() != ".wav":
        raise ValueError("Chatterbox requires a local WAV voice reference.")
    if output.suffix.lower() != ".wav":
        raise ValueError("The Chatterbox worker output must be WAV.")
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(int(request.get("seed", 0)))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(request.get("seed", 0)))
    with contextlib.redirect_stdout(sys.stderr):
        waveform = model.generate(
            text,
            language_id=language,
            audio_prompt_path=str(reference),
            exaggeration=float(request.get("exaggeration", 0.5)),
            cfg_weight=float(request.get("cfg_weight", 0.5)),
            temperature=float(request.get("temperature", 0.8)),
        )
        waveform = waveform.detach().to(device="cpu", dtype=torch.float32)
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        if waveform.ndim != 2 or waveform.shape[0] not in {1, 2}:
            raise ValueError("Chatterbox returned an unsupported waveform shape.")
        samples = (
            waveform.clamp(-1.0, 1.0)
            .transpose(0, 1)
            .mul(32767.0)
            .round()
            .to(torch.int16)
            .contiguous()
            .numpy()
            .astype("<i2", copy=False)
        )
        with wave.open(str(output), "wb") as handle:
            handle.setnchannels(int(waveform.shape[0]))
            handle.setsampwidth(2)
            handle.setframerate(int(model.sr))
            handle.writeframes(samples.tobytes())
    return {
        "ok": True,
        "request_id": str(request["request_id"]),
        "sample_rate_hz": int(model.sr),
        "output_path": str(output),
    }


def _serve() -> int:
    _json({"type": "ready", "protocol": 1})
    model: Any | None = None
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("Worker requests must be JSON objects.")
            if request.get("type") == "shutdown":
                _json({"ok": True, "type": "shutdown"})
                return 0
            if request.get("type") != "render":
                raise ValueError("Unknown worker request type.")
            if model is None:
                model = _load_model()
            _json(_render(model, request))
        except Exception as exc:
            _json(
                {
                    "ok": False,
                    "request_id": (
                        str(request.get("request_id", ""))
                        if isinstance(locals().get("request"), dict)
                        else ""
                    ),
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                }
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--health", action="store_true")
    mode.add_argument("--prefetch", action="store_true")
    mode.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.health:
        return _health()
    if args.prefetch:
        return _prefetch()
    return _serve()


if __name__ == "__main__":
    raise SystemExit(main())
