from __future__ import annotations

import asyncio
import math
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

from project_master.media.artifacts import FilesystemMediaArtifactStore
from project_master.media.metadata import MediaMetadata, probe_media_metadata
from project_master.media.models import (
    VIDEO_TRIM_OPERATION,
    VIDEO_TRIM_RECIPE,
    MediaAsset,
    MediaAssetDerivation,
    MediaKind,
    kind_for_media_type,
    resolve_media_type,
    validate_file_name,
    validate_source,
)
from project_master.media.persistence import (
    MediaAssetNotFoundError,
    SQLiteMediaCatalog,
    is_foreign_key_error,
)
from project_master.media.signatures import validate_media_signature
from project_master.media.trimming import (
    VIDEO_TRIM_TIMEOUT_SECONDS,
    VideoTrimBusyError,
    VideoTrimProcessError,
    VideoTrimProcessResult,
    VideoTrimValidationError,
    build_video_trim_command,
    run_bounded_process,
)

MAX_MEDIA_UPLOAD_BYTES = 512 * 1024 * 1024
_TRIM_DURATION_TOLERANCE_SECONDS = 0.001
VideoTrimRunner: TypeAlias = Callable[
    [tuple[str, ...], float],
    VideoTrimProcessResult,
]


class MediaProjectNotFoundError(KeyError):
    pass


class MediaLibraryService:
    def __init__(
        self,
        catalog: SQLiteMediaCatalog,
        artifacts: FilesystemMediaArtifactStore,
        *,
        project_exists: Callable[[str], bool],
        metadata_probe: Callable[[Path], MediaMetadata] = probe_media_metadata,
        ffmpeg_executable: str = "ffmpeg",
        ffprobe_executable: str = "ffprobe",
        video_trim_runner: VideoTrimRunner = run_bounded_process,
        executable_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.catalog = catalog
        self.artifacts = artifacts
        self.project_exists = project_exists
        self.metadata_probe = metadata_probe
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable
        self.video_trim_runner = video_trim_runner
        self.executable_finder = executable_finder
        self._video_trim_lock = threading.Lock()

    @property
    def staging_directory(self) -> Path:
        return self.artifacts.staging_directory

    def import_staged_file(
        self,
        project_id: str,
        staged_path: str | Path,
        *,
        file_name: str,
        declared_media_type: str | None,
        source: str = "upload",
        max_size_bytes: int = MAX_MEDIA_UPLOAD_BYTES,
    ) -> MediaAsset:
        return self._import_staged_file(
            project_id,
            staged_path,
            file_name=file_name,
            declared_media_type=declared_media_type,
            source=source,
            max_size_bytes=max_size_bytes,
            derivation=None,
            deduplicate=True,
        )

    async def trim_video(
        self,
        project_id: str,
        asset_id: str,
        *,
        start_seconds: float,
        end_seconds: float,
        output_name: str | None = None,
    ) -> MediaAsset:
        start, end = _validate_trim_bounds(start_seconds, end_seconds)
        selected_output_name = (
            _validate_trim_output_name(output_name) if output_name is not None else None
        )
        if not self._video_trim_lock.acquire(blocking=False):
            raise VideoTrimBusyError("Another video trim is already running.")

        try:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._trim_video_locked_sync,
                    project_id,
                    asset_id,
                    start,
                    end,
                    selected_output_name,
                )
            )
        except BaseException:
            self._video_trim_lock.release()
            raise
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Shielding keeps cleanup and the one-at-a-time lease owned by the worker.
            worker.add_done_callback(_consume_cancelled_trim_result)
            raise

    def _trim_video_locked_sync(
        self,
        project_id: str,
        asset_id: str,
        start_seconds: float,
        end_seconds: float,
        output_name: str | None,
    ) -> MediaAsset:
        try:
            return self._trim_video_sync(
                project_id,
                asset_id,
                start_seconds,
                end_seconds,
                output_name,
            )
        finally:
            self._video_trim_lock.release()

    def _trim_video_sync(
        self,
        project_id: str,
        asset_id: str,
        start_seconds: float,
        end_seconds: float,
        output_name: str | None,
    ) -> MediaAsset:
        source_asset = self.get_project_asset(project_id, asset_id)
        if source_asset.kind is not MediaKind.VIDEO:
            raise VideoTrimValidationError("Only video assets can be trimmed.")
        if (
            source_asset.duration_seconds is not None
            and end_seconds > source_asset.duration_seconds + _TRIM_DURATION_TOLERANCE_SECONDS
        ):
            raise VideoTrimValidationError("Trim end must not exceed the source video duration.")
        source_path = self.artifacts.verified_path(
            source_asset.sha256,
            source_asset.size_bytes,
        )
        selected_name = output_name or _default_trim_output_name(
            source_asset.name,
            start_seconds,
            end_seconds,
        )
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".video-trim-",
            suffix=".mp4",
            dir=self.staging_directory,
        )
        output_path = Path(temporary_name)
        os.close(file_descriptor)
        if os.name != "nt":
            os.chmod(output_path, 0o600)
        try:
            command = build_video_trim_command(
                self.ffmpeg_executable,
                source_path,
                output_path,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
            result = self.video_trim_runner(command, VIDEO_TRIM_TIMEOUT_SECONDS)
            if result.returncode != 0:
                raise VideoTrimProcessError(
                    "FFmpeg could not trim the video.",
                    stderr=result.stderr,
                )
            if not output_path.is_file() or output_path.stat().st_size <= 0:
                raise VideoTrimProcessError("FFmpeg completed without producing a video.")
            derivation = MediaAssetDerivation(
                operation=VIDEO_TRIM_OPERATION,
                source_asset_id=source_asset.id,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                recipe=VIDEO_TRIM_RECIPE,
            )
            return self._import_staged_file(
                project_id,
                output_path,
                file_name=selected_name,
                declared_media_type="video/mp4",
                source="trim",
                max_size_bytes=MAX_MEDIA_UPLOAD_BYTES,
                derivation=derivation,
                deduplicate=False,
            )
        finally:
            output_path.unlink(missing_ok=True)

    def _import_staged_file(
        self,
        project_id: str,
        staged_path: str | Path,
        *,
        file_name: str,
        declared_media_type: str | None,
        source: str,
        max_size_bytes: int,
        derivation: MediaAssetDerivation | None,
        deduplicate: bool,
    ) -> MediaAsset:
        self._require_project(project_id)
        name = validate_file_name(file_name)
        media_type = resolve_media_type(name, declared_media_type)
        normalized_source = validate_source(source)
        stored = self.artifacts.import_file(
            staged_path,
            max_size_bytes=max_size_bytes,
        )
        validate_media_signature(stored.path, media_type)
        if deduplicate:
            existing = self.catalog.find_for_project(
                project_id,
                source=normalized_source,
                sha256=stored.sha256,
            )
            if existing is not None:
                return existing
        metadata = self.metadata_probe(stored.path)
        asset = MediaAsset(
            id=f"media-asset-{uuid4().hex}",
            project_ids=(project_id,),
            name=name,
            kind=kind_for_media_type(media_type),
            source=normalized_source,
            media_type=media_type,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            duration_seconds=metadata.duration_seconds,
            width=metadata.width,
            height=metadata.height,
            created_at=datetime.now(UTC),
            derivation=derivation,
        )
        try:
            return self.catalog.add(project_id, asset, derivation=derivation)
        except sqlite3.IntegrityError as exc:
            if is_foreign_key_error(exc):
                raise MediaProjectNotFoundError(project_id) from exc
            raise

    def list_project_assets(self, project_id: str) -> tuple[MediaAsset, ...]:
        self._require_project(project_id)
        return self.catalog.list_for_project(project_id)

    def get_project_asset(self, project_id: str, asset_id: str) -> MediaAsset:
        self._require_project(project_id)
        return self.catalog.get_for_project(project_id, asset_id)

    def get_asset(self, asset_id: str) -> MediaAsset:
        return self.catalog.get(asset_id)

    def verified_content_path(self, asset_id: str) -> tuple[MediaAsset, Path]:
        asset = self.get_asset(asset_id)
        return asset, self.artifacts.verified_path(asset.sha256, asset.size_bytes)

    def health(self) -> dict[str, object]:
        return {
            "ok": self.catalog.health(),
            "max_upload_bytes": MAX_MEDIA_UPLOAD_BYTES,
            "supported_kinds": ["image", "video", "audio"],
            "integrity": "sha256-on-read",
            "ffmpeg_available": self.executable_finder(self.ffmpeg_executable) is not None,
            "ffprobe_available": self.executable_finder(self.ffprobe_executable) is not None,
        }

    def _require_project(self, project_id: str) -> None:
        if not project_id.strip() or not self.project_exists(project_id):
            raise MediaProjectNotFoundError(project_id)


def _validate_trim_bounds(
    start_seconds: float,
    end_seconds: float,
) -> tuple[float, float]:
    if (
        isinstance(start_seconds, bool)
        or isinstance(end_seconds, bool)
        or not math.isfinite(start_seconds)
        or not math.isfinite(end_seconds)
        or start_seconds < 0
        or end_seconds <= start_seconds
    ):
        raise VideoTrimValidationError(
            "Trim bounds must be finite and end after a non-negative start."
        )
    return float(start_seconds), float(end_seconds)


def _validate_trim_output_name(output_name: str) -> str:
    name = validate_file_name(output_name)
    if Path(name).suffix.casefold() != ".mp4":
        raise VideoTrimValidationError("Trim output_name must use the .mp4 extension.")
    return name


def _default_trim_output_name(
    source_name: str,
    start_seconds: float,
    end_seconds: float,
) -> str:
    suffix = f"-trim-{_name_seconds(start_seconds)}-{_name_seconds(end_seconds)}.mp4"
    source_stem = Path(source_name).stem.strip() or "video"
    available_stem = max(1, 255 - len(suffix))
    return validate_file_name(f"{source_stem[:available_stem]}{suffix}")


def _name_seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", "_")


def _consume_cancelled_trim_result(task: asyncio.Task[MediaAsset]) -> None:
    try:
        task.result()
    except BaseException:
        pass


__all__ = [
    "MAX_MEDIA_UPLOAD_BYTES",
    "MediaAssetNotFoundError",
    "MediaLibraryService",
    "MediaProjectNotFoundError",
]
