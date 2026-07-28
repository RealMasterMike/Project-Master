from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import wave
from pathlib import Path

import pytest

from project_master.integrations.voice import chatterbox_worker
from project_master.integrations.voice.cache import VoiceChunkPlan
from project_master.integrations.voice.chatterbox import (
    ChatterboxWorkerAdapter,
    _external_process_environment,
    _load_verified_inventory,
    resolve_chatterbox_worker_script,
)
from project_master.integrations.voice.engine import EngineRenderRequest
from project_master.integrations.voice.manifests import (
    CHATTERBOX_PACK_TEMPLATE,
    InstalledEnginePack,
)


def _pack() -> InstalledEnginePack:
    return InstalledEnginePack(
        id="chatterbox-local-runtime",
        template_id=CHATTERBOX_PACK_TEMPLATE.id,
        engine_id=CHATTERBOX_PACK_TEMPLATE.engine_id,
        installed_version="test",
        template_digest=CHATTERBOX_PACK_TEMPLATE.digest,
        capabilities=CHATTERBOX_PACK_TEMPLATE.capabilities,
        assets=(),
    )


def _plan(*, output_format: str = "wav") -> VoiceChunkPlan:
    raw = {
        "schema_version": 1,
        "ordinal": 0,
        "block_id": "block-1",
        "block_chunk_index": 0,
        "text": "Hello from the isolated voice worker.",
        "language": "en-US",
        "voice_profile_id": "voice-1",
        "voice_profile_digest": "1" * 64,
        "project_digest": "2" * 64,
        "engine_pack_id": _pack().id,
        "engine_pack_digest": _pack().digest,
        "performance_direction": "calm and gentle",
        "speed": 1.0,
        "pause_after_ms": 0,
        "pronunciations": (),
        "output_format": output_format,
        "sample_rate_hz": 24_000,
        "channels": 1,
        "seed": 7,
        "normalize_loudness": False,
    }
    provisional = VoiceChunkPlan.model_construct(id="", cache_key="", **raw)
    return VoiceChunkPlan(
        id=f"voice-chunk-{provisional._instance_digest()[:32]}",
        cache_key=f"voice-cache-{provisional._cache_digest()[:32]}",
        **raw,
    )


def _request(reference_id: str = "reference-1") -> EngineRenderRequest:
    return EngineRenderRequest(
        job_id="job-1",
        chunk=_plan(),
        engine_pack=_pack(),
        reference_artifact_ids=(reference_id,),
        reference_sha256=("a" * 64,),
    )


def _write_wav(path: Path, *, seconds: float = 0.1) -> None:
    frames = int(24_000 * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(b"\x00\x00" * frames)


def _fake_worker(path: Path) -> None:
    path.write_text(
        """
import argparse
import json
import sys
import wave

parser = argparse.ArgumentParser()
parser.add_argument("--health", action="store_true")
parser.add_argument("--serve", action="store_true")
args = parser.parse_args()
if args.health:
    print(json.dumps({"ok": True, "version": "test", "device": "cpu"}), flush=True)
    raise SystemExit(0)
print(json.dumps({"type": "ready", "protocol": 1}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    output = request["output_path"]
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\\x00\\x00" * 2400)
    print(json.dumps({
        "ok": True,
        "request_id": request["request_id"],
        "sample_rate_hz": 24000,
        "output_path": output,
    }), flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_chatterbox_worker_health_and_render_are_isolated(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    worker = tmp_path / "worker.py"
    _write_wav(reference)
    _fake_worker(worker)
    adapter = ChatterboxWorkerAdapter(
        sys.executable,
        tmp_path / "engine",
        lambda artifact_id: reference
        if artifact_id == "reference-1"
        else (_ for _ in ()).throw(KeyError(artifact_id)),
        worker_script=worker,
    )

    async def exercise() -> None:
        health = await adapter.health(_pack())
        assert health.available is True
        assert health.status == "ready"
        rendered = await adapter.render_chunk(_request())
        assert rendered.format == "wav"
        assert rendered.media_type == "audio/wav"
        assert rendered.sample_rate_hz == 24_000
        assert rendered.duration_seconds == pytest.approx(0.1, abs=0.01)
        assert rendered.content.startswith(b"RIFF")

    asyncio.run(exercise())


def test_chatterbox_worker_requires_exactly_one_registered_reference(
    tmp_path: Path,
) -> None:
    worker = tmp_path / "worker.py"
    _fake_worker(worker)
    adapter = ChatterboxWorkerAdapter(
        sys.executable,
        tmp_path / "engine",
        lambda _artifact_id: tmp_path / "missing.wav",
        worker_script=worker,
    )
    request = EngineRenderRequest(
        job_id="job-1",
        chunk=_plan(),
        engine_pack=_pack(),
        reference_artifact_ids=("one", "two"),
        reference_sha256=("1" * 64, "2" * 64),
    )

    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(adapter.render_chunk(request))


def test_chatterbox_worker_never_downloads_during_health_or_render(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.wav"
    worker = tmp_path / "worker.py"
    _write_wav(reference)
    _fake_worker(worker)
    adapter = ChatterboxWorkerAdapter(
        sys.executable,
        tmp_path / "engine",
        lambda _artifact_id: reference,
        worker_script=worker,
    )
    environment = adapter._environment(offline=True)

    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["HF_HOME"].startswith(str(tmp_path))
    assert environment["PKUSEG_HOME"].startswith(str(tmp_path))
    assert environment["PROJECT_MASTER_VOICE_ENGINE_ROOT"].startswith(str(tmp_path))


def test_external_worker_environment_removes_packaged_runtime_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHOME", "/tmp/appimage-python")
    monkeypatch.setenv("PYTHONPATH", "/tmp/appimage-python/site-packages")
    monkeypatch.setenv("PYTHONEXECUTABLE", "/tmp/appimage-python/bin/python")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/appimage-libraries")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib:/usr/lib64")

    environment = _external_process_environment()

    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert "PYTHONEXECUTABLE" not in environment
    if sys.platform != "win32":
        assert environment["LD_LIBRARY_PATH"] == "/usr/local/lib:/usr/lib64"
        assert "LD_LIBRARY_PATH_ORIG" not in environment


def test_chatterbox_adapter_preserves_virtualenv_launcher_symlink(
    tmp_path: Path,
) -> None:
    base_python = tmp_path / "python3.11"
    base_python.write_bytes(b"python")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    try:
        venv_python.symlink_to(base_python)
    except OSError:
        pytest.skip("This platform cannot create the virtualenv launcher symlink.")
    worker = tmp_path / "worker.py"
    _fake_worker(worker)

    adapter = ChatterboxWorkerAdapter(
        venv_python,
        tmp_path / "engine",
        lambda _artifact_id: tmp_path / "reference.wav",
        worker_script=worker,
    )

    assert Path(adapter.python_executable) == venv_python.absolute()
    assert Path(adapter.python_executable) != venv_python.resolve()


def test_worker_removes_sibling_adapter_from_external_import_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worker_directory = str(Path(chatterbox_worker.__file__).resolve().parent)
    unrelated = str(tmp_path.resolve())
    monkeypatch.setattr(sys, "path", [worker_directory, unrelated])

    chatterbox_worker._remove_worker_import_shadow()

    assert worker_directory not in sys.path
    assert unrelated in sys.path


def test_source_worker_resource_is_resolvable() -> None:
    worker = resolve_chatterbox_worker_script()

    assert worker is not None
    assert worker.name == "chatterbox_worker.py"
    assert worker.is_file()


def test_verified_inventory_requires_pinned_revisions_and_matching_sizes(
    tmp_path: Path,
) -> None:
    files = {
        "voice_encoder_checkpoint": "ve.pt",
        "t3_weights": "t3_mtl23ls_v3.safetensors",
        "generator_checkpoint": "s3gen.pt",
        "tokenizer": "grapheme_mtl_merged_expanded_v1.json",
        "conditioning_checkpoint": "conds.pt",
        "cangjie_map": "Cangjie5_TC.json",
    }
    assets = []
    for index, (logical_name, filename) in enumerate(files.items(), start=1):
        path = tmp_path / "models" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"asset-{index}".encode()
        path.write_bytes(content)
        assets.append(
            {
                "logical_name": logical_name,
                "relative_path": path.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    inventory = {
        "schema_version": 1,
        "engine_source_revision": chatterbox_worker._ENGINE_SOURCE_REVISION,
        "engine_version": "test",
        "model_repo": chatterbox_worker._REPO_ID,
        "model_revision": chatterbox_worker._MODEL_REVISION,
        "assets": assets,
    }
    inventory_path = tmp_path / "asset-inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    loaded = _load_verified_inventory(tmp_path)

    assert loaded is not None
    assert loaded[0] == chatterbox_worker._ENGINE_SOURCE_REVISION
    assert {asset.logical_name for asset in loaded[1]} == set(files)
    inventory["assets"][0]["size_bytes"] += 1
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    assert _load_verified_inventory(tmp_path) is None
