from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from project_master.integrations.comfyui.artifacts import (
    ComfyArtifactProvenance,
    FilesystemComfyArtifactStore,
)
from project_master.integrations.comfyui.transport import (
    DownloadedOutput,
    OutputMetadata,
    OutputRef,
)
from project_master.tools.base import ToolRegistry
from project_master.tools.comfyui import register_comfyui_tools


def provenance(
    output: OutputMetadata,
    *,
    fetched_at: datetime = datetime(2026, 7, 27, tzinfo=UTC),
) -> tuple[DownloadedOutput, ComfyArtifactProvenance]:
    source_url = "http://127.0.0.1:8188/view?" + urlencode(
        {
            "filename": output.ref.filename,
            "subfolder": output.ref.subfolder,
            "type": output.ref.type,
        }
    )
    download = DownloadedOutput(
        content=b"\x89PNG\r\n\x1a\nverified-content",
        media_type=output.media_type or "application/octet-stream",
        source_url=source_url,
        fetched_at=fetched_at,
    )
    evidence = ComfyArtifactProvenance(
        job_id="comfy-job-one",
        profile_id="local",
        workflow_revision_id="comfy-wf-one",
        workflow_digest="a" * 64,
        remote_prompt_id="prompt-one",
        output=output,
        history_sha256="b" * 64,
        history_status="success",
        source_base_url="http://127.0.0.1:8188",
        source_url=source_url,
        fetched_at=fetched_at,
    )
    return download, evidence


def test_artifact_store_hashes_and_atomically_persists_provenance(tmp_path) -> None:
    store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    output = OutputMetadata(
        node_id="9",
        category="images",
        output_index=0,
        ref=OutputRef(filename="final.png", subfolder="daily"),
        media_type="image/png",
    )
    download, evidence = provenance(output)

    artifact = store.store(download, evidence)

    assert artifact.sha256 == ("4bd03aea4d1addcda0ef5a171518d94e5d3667390e7d3dedb8ad6c8876c336d6")
    assert artifact.relative_path.startswith("jobs/comfy-job-one/comfy-artifact-")
    assert artifact.relative_path.endswith(".png")
    assert "final.png" not in artifact.relative_path
    assert store.read(artifact) == download.content
    manifest = store.path_for(artifact).with_name(f"{artifact.id}.metadata.json")
    assert artifact.id in manifest.read_text("utf-8")
    assert evidence.remote_prompt_id in manifest.read_text("utf-8")


def test_artifact_store_is_idempotent_and_repairs_verified_content(tmp_path) -> None:
    store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="final.png", subfolder="daily"),
        media_type="image/png",
    )
    download, evidence = provenance(output)
    first = store.store(download, evidence)
    path = store.path_for(first)
    path.write_bytes(b"corrupt")
    later_download, later_evidence = provenance(
        output,
        fetched_at=datetime(2026, 7, 28, tzinfo=UTC),
    )

    restored = store.store(later_download, later_evidence)

    assert restored == first
    assert store.read(restored) == download.content


def test_artifact_store_enforces_size_and_source_origin(tmp_path) -> None:
    store = FilesystemComfyArtifactStore(
        tmp_path / "comfy-artifacts",
        max_artifact_bytes=4,
    )
    output = OutputMetadata(
        node_id="9",
        category="files",
        ref=OutputRef(filename="result.bin"),
        media_type="application/octet-stream",
    )
    download, evidence = provenance(output)
    with pytest.raises(ValueError, match="size limit"):
        store.store(download, evidence)

    with pytest.raises(ValueError, match="profile origin"):
        ComfyArtifactProvenance(
            **evidence.model_dump(
                exclude={"source_url"},
            ),
            source_url="http://127.0.0.1:9999/view?filename=result.bin",
        )
    with pytest.raises(ValueError, match="/view"):
        ComfyArtifactProvenance(
            **evidence.model_dump(
                exclude={"source_url"},
            ),
            source_url=(
                "http://127.0.0.1:8188/admin?filename=final.png&subfolder=daily&type=output"
            ),
        )


def test_agent_artifact_tool_lists_verified_local_artifacts(tmp_path) -> None:
    store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="final.png"),
        media_type="image/png",
    )
    download, evidence = provenance(output)
    artifact = store.store(download, evidence)

    class Service:
        def job_status(self, job_id: str) -> object:
            assert job_id == "comfy-job-one"
            # Rootless chat scope: the job carries no project association either.
            return SimpleNamespace(project_id=None)

        def artifacts(self, job_id: str) -> tuple[object, ...]:
            assert job_id == "comfy-job-one"
            return (artifact,)

        def outputs(self, _job_id: str) -> tuple[object, ...]:
            raise AssertionError("remote output metadata must not be returned as artifacts")

    registry = ToolRegistry()
    register_comfyui_tools(
        registry,
        Service(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
    )

    ok, payload = registry.execute(
        "comfy_run_artifacts",
        {"job_id": "comfy-job-one"},
    )

    assert ok is True
    assert artifact.id in payload
    assert artifact.sha256 in payload
