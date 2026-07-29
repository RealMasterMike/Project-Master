from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from project_master.media.artifacts import (
    MediaArtifactError,
    MediaArtifactIntegrityError,
    MediaArtifactTooLargeError,
)
from project_master.media.models import MediaValidationError, validate_file_name
from project_master.media.persistence import MediaAssetNotFoundError
from project_master.media.service import (
    MAX_MEDIA_UPLOAD_BYTES,
    MediaLibraryService,
    MediaProjectNotFoundError,
)
from project_master.media.trimming import (
    VideoTrimBusyError,
    VideoTrimProcessError,
    VideoTrimTimeoutError,
    VideoTrimUnavailableError,
    VideoTrimValidationError,
)


class VideoTrimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    output_name: str | None = Field(default=None, min_length=1, max_length=255)


def create_media_router(
    service: MediaLibraryService,
    *,
    max_upload_bytes: int = MAX_MEDIA_UPLOAD_BYTES,
) -> APIRouter:
    if max_upload_bytes <= 0:
        raise ValueError("max_upload_bytes must be positive")
    router = APIRouter()

    @router.get("/api/v1/media/health")
    def media_health() -> dict[str, object]:
        return {**service.health(), "max_upload_bytes": max_upload_bytes}

    @router.get("/api/v1/projects/{project_id}/media")
    def list_project_media(project_id: str) -> dict[str, object]:
        try:
            assets = service.list_project_assets(project_id)
        except MediaProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return {"assets": [asset.to_dict() for asset in assets]}

    @router.post("/api/v1/projects/{project_id}/media", status_code=201)
    async def upload_project_media(
        project_id: str,
        request: Request,
        file_name: str | None = Query(default=None, max_length=255),
        x_file_name: str | None = Header(
            default=None,
            alias="X-File-Name",
            max_length=255,
        ),
    ) -> dict[str, object]:
        if file_name and x_file_name and file_name != x_file_name:
            raise HTTPException(
                status_code=400,
                detail="file_name and X-File-Name must match when both are supplied.",
            )
        selected_name = file_name or x_file_name
        if selected_name is None:
            raise HTTPException(
                status_code=422,
                detail="Provide file_name or the X-File-Name header.",
            )
        try:
            selected_name = validate_file_name(selected_name)
        except MediaValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            service.list_project_assets(project_id)
        except MediaProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc
        if content_length > max_upload_bytes:
            raise HTTPException(status_code=413, detail="Media upload exceeds the size limit.")

        staged_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".media-upload-",
                dir=service.staging_directory,
                delete=False,
            ) as staged:
                staged_path = Path(staged.name)
                size_bytes = 0
                async for chunk in request.stream():
                    size_bytes += len(chunk)
                    if size_bytes > max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Media upload exceeds the size limit.",
                        )
                    staged.write(chunk)
            try:
                asset = service.import_staged_file(
                    project_id,
                    staged_path,
                    file_name=selected_name,
                    declared_media_type=request.headers.get("content-type"),
                    source="upload",
                    max_size_bytes=max_upload_bytes,
                )
            except MediaValidationError as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
            except MediaArtifactTooLargeError as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except MediaArtifactError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except MediaProjectNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Project not found") from exc
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
        return {"asset": asset.to_dict()}

    @router.post(
        "/api/v1/projects/{project_id}/media/{asset_id}/trim",
        status_code=201,
    )
    async def trim_project_video(
        project_id: str,
        asset_id: str,
        body: VideoTrimRequest,
    ) -> dict[str, object]:
        try:
            asset = await service.trim_video(
                project_id,
                asset_id,
                start_seconds=body.start_seconds,
                end_seconds=body.end_seconds,
                output_name=body.output_name,
            )
        except MediaProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except MediaAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Media asset not found") from exc
        except MediaArtifactIntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Source media failed integrity verification.",
            ) from exc
        except VideoTrimBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail="Another video trim is already running.",
            ) from exc
        except VideoTrimUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail="FFmpeg is not available.",
            ) from exc
        except VideoTrimTimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="Video trim exceeded the processing time limit.",
            ) from exc
        except VideoTrimValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except VideoTrimProcessError as exc:
            raise HTTPException(
                status_code=422,
                detail="FFmpeg could not process the source video.",
            ) from exc
        except MediaArtifactTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (MediaArtifactError, MediaValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"asset": asset.to_dict()}

    @router.get("/api/v1/media/assets/{asset_id}/content")
    def get_media_content(asset_id: str) -> FileResponse:
        try:
            asset, path = service.verified_content_path(asset_id)
        except MediaAssetNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Media asset not found") from exc
        except MediaArtifactIntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Media asset failed integrity verification.",
            ) from exc
        return FileResponse(
            path,
            media_type=asset.media_type,
            filename=asset.name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, immutable",
                "ETag": f'"sha256:{asset.sha256}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
