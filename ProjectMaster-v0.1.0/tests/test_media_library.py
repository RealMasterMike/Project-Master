from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from project_master.agent import ProjectMasterAgent
from project_master.api import create_app
from project_master.config import MasterConfig
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.media import (
    VIDEO_TRIM_TIMEOUT_SECONDS,
    FilesystemMediaArtifactStore,
    MediaArtifactIntegrityError,
    MediaAssetNotFoundError,
    MediaLibraryService,
    MediaMetadata,
    MediaValidationError,
    SQLiteMediaCatalog,
    VideoTrimBusyError,
    VideoTrimProcessError,
    VideoTrimProcessResult,
    VideoTrimTimeoutError,
    VideoTrimUnavailableError,
    VideoTrimValidationError,
    create_media_router,
    probe_media_metadata,
    resolve_media_type,
    run_bounded_process,
    validate_file_name,
)
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ProjectSpec
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime
from project_master.tools.builtin import build_registry

_PNG = b"\x89PNG\r\n\x1a\n" + b"project-master-test-image"
_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


class _Provider:
    model = "test-model"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "models": [self.model], "configured_model": self.model}

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages, tools
        return Message(role="assistant", content="unused")


def _library(
    tmp_path: Path,
    *,
    metadata: MediaMetadata | None = None,
    video_trim_runner: Any | None = None,
    executable_finder: Any | None = None,
) -> tuple[MediaLibraryService, OrchestrationStore, str]:
    store = SQLiteStore(tmp_path / "master.db")
    projects = OrchestrationStore(store)
    project_id = projects.create_project(ProjectSpec(name="Creator"))
    catalog = SQLiteMediaCatalog(store)
    kwargs: dict[str, Any] = {}
    if video_trim_runner is not None:
        kwargs["video_trim_runner"] = video_trim_runner
    if executable_finder is not None:
        kwargs["executable_finder"] = executable_finder
    service = MediaLibraryService(
        catalog,
        FilesystemMediaArtifactStore(tmp_path / "media-artifacts"),
        project_exists=lambda candidate: projects.get_project(candidate) is not None,
        metadata_probe=lambda _path: metadata or MediaMetadata(),
        **kwargs,
    )
    return service, projects, project_id


def _runtime(
    tmp_path: Path,
    service: MediaLibraryService,
    projects: OrchestrationStore,
) -> MasterRuntime:
    config = MasterConfig(
        model="test-model",
        db_path=tmp_path / "master.db",
        workspace_root=tmp_path / "workspace",
    )
    store = projects.store
    provider = _Provider()
    profiler = StyleProfiler(store)
    agent = ProjectMasterAgent(
        provider=provider,  # type: ignore[arg-type]
        tools=build_registry(store, config.workspace_root),
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
    )
    return MasterRuntime(
        config,
        store,
        profiler,
        provider,  # type: ignore[arg-type]
        agent,
        orchestration=projects,
        media=service,
        media_catalog=service.catalog,
    )


def test_media_asset_import_is_immutable_project_scoped_and_content_addressed(
    tmp_path: Path,
) -> None:
    service, projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(duration_seconds=2.5, width=640, height=360),
    )
    staged = tmp_path / "upload.png"
    staged.write_bytes(_PNG)

    asset = service.import_staged_file(
        project_id,
        staged,
        file_name="still.png",
        declared_media_type="image/png",
    )

    assert asset.project_ids == (project_id,)
    assert asset.kind.value == "image"
    assert asset.source == "upload"
    assert asset.sha256 == hashlib.sha256(_PNG).hexdigest()
    assert asset.size_bytes == len(_PNG)
    assert asset.duration_seconds == 2.5
    assert asset.width == 640
    assert asset.height == 360
    assert service.list_project_assets(project_id) == (asset,)
    assert (
        service.import_staged_file(
            project_id,
            staged,
            file_name="still-copy.png",
            declared_media_type="image/png",
        )
        == asset
    )
    with pytest.raises(FrozenInstanceError):
        asset.name = "changed.png"  # type: ignore[misc]

    second_project = projects.create_project(ProjectSpec(name="Second"))
    linked = service.catalog.link(second_project, asset.id)
    assert set(linked.project_ids) == {project_id, second_project}
    assert service.get_project_asset(second_project, asset.id) == linked


def test_media_import_rejects_mislabeled_bytes_before_cataloging(tmp_path: Path) -> None:
    service, _projects, project_id = _library(tmp_path)
    staged = tmp_path / "not-an-image.png"
    staged.write_text("<script>alert('not media')</script>", encoding="utf-8")

    with pytest.raises(MediaValidationError, match="does not match"):
        service.import_staged_file(
            project_id,
            staged,
            file_name="not-an-image.png",
            declared_media_type="image/png",
        )

    assert service.list_project_assets(project_id) == ()


def test_media_artifact_read_verifies_sha256_and_uses_private_permissions(
    tmp_path: Path,
) -> None:
    service, _projects, project_id = _library(tmp_path)
    staged = tmp_path / "upload.png"
    staged.write_bytes(_PNG)
    asset = service.import_staged_file(
        project_id,
        staged,
        file_name="still.png",
        declared_media_type="image/png",
    )
    _asset, object_path = service.verified_content_path(asset.id)

    if os.name != "nt":
        assert stat.S_IMODE(service.artifacts.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(service.staging_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(object_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(object_path.stat().st_mode) == 0o600

    object_path.write_bytes(b"corrupted")
    with pytest.raises(MediaArtifactIntegrityError, match="SHA-256"):
        service.verified_content_path(asset.id)


def test_media_validation_rejects_paths_and_mime_extension_mismatch() -> None:
    with pytest.raises(MediaValidationError, match="path"):
        validate_file_name("../../payload.png")

    with pytest.raises(MediaValidationError, match="does not match"):
        resolve_media_type("payload.png", "video/mp4")


def test_ffprobe_metadata_is_bounded_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isom")

    def completed(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                b'{"streams":[{"duration":"3.25","width":1920,"height":1080}],'
                b'"format":{"duration":"3.5"}}'
            ),
            stderr=b"",
        )

    monkeypatch.setattr("project_master.media.metadata.subprocess.run", completed)
    assert probe_media_metadata(path) == MediaMetadata(3.25, 1920, 1080)

    def unavailable(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError

    monkeypatch.setattr("project_master.media.metadata.subprocess.run", unavailable)
    assert probe_media_metadata(path) == MediaMetadata()

    monkeypatch.setattr(
        "project_master.media.metadata.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout=b"x" * (64 * 1024 + 1),
            stderr=b"",
        ),
    )
    assert probe_media_metadata(path) == MediaMetadata()


def test_authenticated_media_api_upload_list_and_inline_content(tmp_path: Path) -> None:
    service, projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(width=320, height=180),
    )
    app = create_app(_runtime(tmp_path, service, projects), session_token="desktop-secret")
    token = {"X-Project-Master-Token": "desktop-secret"}

    with TestClient(app) as client:
        assert client.get("/api/v1/media/health").status_code == 401
        health = client.get("/api/v1/media/health", headers=token).json()
        assert health == {
            "ok": True,
            "max_upload_bytes": 512 * 1024 * 1024,
            "supported_kinds": ["image", "video", "audio"],
            "integrity": "sha256-on-read",
            "ffmpeg_available": shutil.which("ffmpeg") is not None,
            "ffprobe_available": shutil.which("ffprobe") is not None,
        }

        response = client.post(
            f"/api/v1/projects/{project_id}/media",
            params={"file_name": "creator still.png"},
            content=_PNG,
            headers={**token, "Content-Type": "image/png"},
        )
        assert response.status_code == 201
        asset = response.json()["asset"]
        assert set(asset) == {
            "id",
            "project_ids",
            "name",
            "kind",
            "source",
            "media_type",
            "sha256",
            "size_bytes",
            "duration_seconds",
            "width",
            "height",
            "created_at",
            "derivation",
        }
        assert asset["project_ids"] == [project_id]
        assert asset["kind"] == "image"
        assert asset["width"] == 320
        assert asset["height"] == 180
        assert asset["derivation"] is None

        listing = client.get(
            f"/api/v1/projects/{project_id}/media",
            headers=token,
        ).json()
        assert listing == {"assets": [asset]}

        assert client.get(f"/api/v1/media/assets/{asset['id']}/content").status_code == 401
        content = client.get(
            f"/api/v1/media/assets/{asset['id']}/content",
            headers=token,
        )
        assert content.status_code == 200
        assert content.content == _PNG
        assert content.headers["content-type"] == "image/png"
        assert content.headers["content-disposition"].startswith("inline;")
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["etag"] == f'"sha256:{asset["sha256"]}"'


def test_media_api_rejects_unknown_projects_unsafe_names_and_non_media(
    tmp_path: Path,
) -> None:
    service, projects, project_id = _library(tmp_path)
    app = create_app(_runtime(tmp_path, service, projects), session_token="token")
    token = {"X-Project-Master-Token": "token", "Content-Type": "image/png"}

    with TestClient(app) as client:
        unknown = client.post(
            "/api/v1/projects/missing/media",
            params={"file_name": "still.png"},
            content=_PNG,
            headers=token,
        )
        assert unknown.status_code == 404

        unsafe = client.post(
            f"/api/v1/projects/{project_id}/media",
            params={"file_name": "../still.png"},
            content=_PNG,
            headers=token,
        )
        assert unsafe.status_code == 422

        mislabeled = client.post(
            f"/api/v1/projects/{project_id}/media",
            params={"file_name": "still.png"},
            content=b"plain text",
            headers=token,
        )
        assert mislabeled.status_code == 415
        assert service.list_project_assets(project_id) == ()

        missing = client.get(
            "/api/v1/media/assets/media-asset-00000000000000000000000000000000/content",
            headers={"X-Project-Master-Token": "token"},
        )
        assert missing.status_code == 404
        with pytest.raises(MediaAssetNotFoundError):
            service.get_asset("media-asset-00000000000000000000000000000000")


def test_media_router_enforces_streaming_size_limit_and_cleans_staging(
    tmp_path: Path,
) -> None:
    service, _projects, project_id = _library(tmp_path)
    app = FastAPI()
    app.include_router(create_media_router(service, max_upload_bytes=8))

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/media",
            params={"file_name": "still.png"},
            content=_PNG,
            headers={"Content-Type": "image/png"},
        )

    assert response.status_code == 413
    assert not tuple(service.staging_directory.iterdir())


def test_video_trim_uses_fixed_frame_accurate_command_and_persists_derivation(
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], float]] = []

    def runner(
        command: tuple[str, ...],
        timeout_seconds: float,
    ) -> VideoTrimProcessResult:
        output_path = Path(command[-1])
        assert output_path.is_file()
        if os.name != "nt":
            assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
        calls.append((command, timeout_seconds))
        output_path.write_bytes(_MP4)
        return VideoTrimProcessResult(0, b"", False)

    service, _projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(duration_seconds=8.0, width=641, height=359),
        video_trim_runner=runner,
    )
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(_MP4)
    source = service.import_staged_file(
        project_id,
        source_path,
        file_name="source clip.mp4",
        declared_media_type="video/mp4",
    )
    _source_asset, verified_source_path = service.verified_content_path(source.id)

    first = asyncio.run(
        service.trim_video(
            project_id,
            source.id,
            start_seconds=1.25,
            end_seconds=3.75,
            output_name="short take.mp4",
        )
    )

    assert calls == [
        (
            (
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostats",
                "-xerror",
                "-i",
                str(verified_source_path),
                "-ss",
                "1.25",
                "-t",
                "2.5",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                "-y",
                calls[0][0][-1],
            ),
            VIDEO_TRIM_TIMEOUT_SECONDS,
        )
    ]
    assert first.id != source.id
    assert first.name == "short take.mp4"
    assert first.kind.value == "video"
    assert first.source == "trim"
    assert first.media_type == "video/mp4"
    assert first.derivation is not None
    assert first.derivation.to_dict() == {
        "operation": "video_trim",
        "source_asset_id": source.id,
        "start_seconds": 1.25,
        "end_seconds": 3.75,
        "recipe": "mp4-h264-aac-v1",
    }
    assert service.get_asset(first.id) == first
    assert not tuple(service.staging_directory.iterdir())

    second = asyncio.run(
        service.trim_video(
            project_id,
            source.id,
            start_seconds=1.25,
            end_seconds=3.75,
        )
    )
    assert second.id != first.id
    assert second.sha256 == first.sha256
    assert second.name == "source clip-trim-1_25-3_75.mp4"
    assert len(service.list_project_assets(project_id)) == 3
    assert not tuple(service.staging_directory.iterdir())

    app = FastAPI()
    app.include_router(create_media_router(service))
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/media/{source.id}/trim",
            json={
                "start_seconds": 0,
                "end_seconds": 1,
                "output_name": "api take.mp4",
            },
        )
    assert response.status_code == 201
    api_asset = response.json()["asset"]
    assert api_asset["name"] == "api take.mp4"
    assert api_asset["derivation"] == {
        "operation": "video_trim",
        "source_asset_id": source.id,
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "recipe": "mp4-h264-aac-v1",
    }


def test_video_trim_lock_rejects_concurrent_work_without_queueing(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner(
        command: tuple[str, ...],
        _timeout_seconds: float,
    ) -> VideoTrimProcessResult:
        started.set()
        assert release.wait(timeout=5)
        Path(command[-1]).write_bytes(_MP4)
        return VideoTrimProcessResult(0, b"", False)

    service, _projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(duration_seconds=4.0),
        video_trim_runner=runner,
    )
    path = tmp_path / "source.mp4"
    path.write_bytes(_MP4)
    source = service.import_staged_file(
        project_id,
        path,
        file_name="source.mp4",
        declared_media_type="video/mp4",
    )

    async def exercise() -> None:
        first = asyncio.create_task(
            service.trim_video(
                project_id,
                source.id,
                start_seconds=0,
                end_seconds=1,
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            with pytest.raises(VideoTrimBusyError, match="already running"):
                await service.trim_video(
                    project_id,
                    source.id,
                    start_seconds=1,
                    end_seconds=2,
                )
        finally:
            release.set()
        assert (await first).derivation is not None

    asyncio.run(exercise())
    assert not tuple(service.staging_directory.iterdir())


def test_video_trim_rejects_wrong_kind_bounds_and_cross_project_source(
    tmp_path: Path,
) -> None:
    service, projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(duration_seconds=2.0),
    )
    image_path = tmp_path / "still.png"
    image_path.write_bytes(_PNG)
    image = service.import_staged_file(
        project_id,
        image_path,
        file_name="still.png",
        declared_media_type="image/png",
    )
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(_MP4)
    video = service.import_staged_file(
        project_id,
        video_path,
        file_name="source.mp4",
        declared_media_type="video/mp4",
    )
    other_project = projects.create_project(ProjectSpec(name="Other"))

    with pytest.raises(VideoTrimValidationError, match="Only video"):
        asyncio.run(
            service.trim_video(
                project_id,
                image.id,
                start_seconds=0,
                end_seconds=1,
            )
        )
    with pytest.raises(VideoTrimValidationError, match="finite"):
        asyncio.run(
            service.trim_video(
                project_id,
                video.id,
                start_seconds=1,
                end_seconds=1,
            )
        )
    with pytest.raises(VideoTrimValidationError, match="duration"):
        asyncio.run(
            service.trim_video(
                project_id,
                video.id,
                start_seconds=0,
                end_seconds=3,
            )
        )
    with pytest.raises(MediaAssetNotFoundError):
        asyncio.run(
            service.trim_video(
                other_project,
                video.id,
                start_seconds=0,
                end_seconds=1,
            )
        )


def test_video_trim_process_failure_bounds_stderr_and_cleans_staging(
    tmp_path: Path,
) -> None:
    def runner(
        command: tuple[str, ...],
        _timeout_seconds: float,
    ) -> VideoTrimProcessResult:
        Path(command[-1]).write_bytes(_MP4)
        return VideoTrimProcessResult(7, b"x" * (128 * 1024), True)

    service, _projects, project_id = _library(
        tmp_path,
        metadata=MediaMetadata(duration_seconds=3.0),
        video_trim_runner=runner,
    )
    path = tmp_path / "source.mp4"
    path.write_bytes(_MP4)
    source = service.import_staged_file(
        project_id,
        path,
        file_name="source.mp4",
        declared_media_type="video/mp4",
    )

    with pytest.raises(VideoTrimProcessError) as raised:
        asyncio.run(
            service.trim_video(
                project_id,
                source.id,
                start_seconds=0,
                end_seconds=1,
            )
        )
    assert len(raised.value.stderr) == 64 * 1024
    assert service.list_project_assets(project_id) == (source,)
    assert not tuple(service.staging_directory.iterdir())


def test_video_trim_api_is_strict_and_maps_processing_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _projects, project_id = _library(tmp_path)
    app = FastAPI()
    app.include_router(create_media_router(service))

    with TestClient(app) as client:
        invalid = client.post(
            f"/api/v1/projects/{project_id}/media/"
            "media-asset-00000000000000000000000000000000/trim",
            json={"start_seconds": "0", "end_seconds": 1, "unexpected": True},
        )
        assert invalid.status_code == 422

        errors = (
            (VideoTrimBusyError("busy"), 409),
            (VideoTrimUnavailableError("missing"), 503),
            (VideoTrimTimeoutError("timeout"), 504),
            (VideoTrimProcessError("failed"), 422),
        )
        for error, expected_status in errors:

            async def fail(*_args: Any, _error: Exception = error, **_kwargs: Any) -> Any:
                raise _error

            monkeypatch.setattr(service, "trim_video", fail)
            response = client.post(
                f"/api/v1/projects/{project_id}/media/"
                "media-asset-00000000000000000000000000000000/trim",
                json={"start_seconds": 0, "end_seconds": 1},
            )
            assert response.status_code == expected_status


def test_bounded_process_retains_at_most_64_kib_of_stderr() -> None:
    result = run_bounded_process(
        (
            sys.executable,
            "-c",
            "import sys; sys.stderr.buffer.write(b'x' * 131072); raise SystemExit(7)",
        ),
        10,
    )

    assert result.returncode == 7
    assert len(result.stderr) == 64 * 1024
    assert result.stderr_truncated is True


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required for the round-trip acceptance test.",
)
def test_real_ffmpeg_video_trim_round_trip(tmp_path: Path) -> None:
    source_path = tmp_path / "odd-source.mkv"
    generated = subprocess.run(
        (
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=65x63:rate=12",
            "-t",
            "1.2",
            "-c:v",
            "ffv1",
            "-y",
            str(source_path),
        ),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if generated.returncode != 0:
        pytest.skip("Installed FFmpeg cannot generate the local FFV1 fixture.")

    store = SQLiteStore(tmp_path / "master.db")
    projects = OrchestrationStore(store)
    project_id = projects.create_project(ProjectSpec(name="Creator"))
    service = MediaLibraryService(
        SQLiteMediaCatalog(store),
        FilesystemMediaArtifactStore(tmp_path / "media-artifacts"),
        project_exists=lambda candidate: projects.get_project(candidate) is not None,
    )
    source = service.import_staged_file(
        project_id,
        source_path,
        file_name="odd-source.mkv",
        declared_media_type="video/x-matroska",
    )

    trimmed = asyncio.run(
        service.trim_video(
            project_id,
            source.id,
            start_seconds=0.2,
            end_seconds=0.8,
        )
    )

    assert trimmed.derivation is not None
    assert trimmed.duration_seconds == pytest.approx(0.6, abs=0.15)
    assert trimmed.width == 66
    assert trimmed.height == 64
    _asset, trimmed_path = service.verified_content_path(trimmed.id)
    assert trimmed_path.read_bytes()[4:8] == b"ftyp"
