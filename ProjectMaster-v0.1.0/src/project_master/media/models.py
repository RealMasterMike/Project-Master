from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any

_ASSET_ID = re.compile(r"^media-asset-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
VIDEO_TRIM_OPERATION = "video_trim"
VIDEO_TRIM_RECIPE = "mp4-h264-aac-v1"


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


_MEDIA_TYPES: dict[str, MediaKind] = {
    "image/avif": MediaKind.IMAGE,
    "image/bmp": MediaKind.IMAGE,
    "image/gif": MediaKind.IMAGE,
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/tiff": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "video/mp4": MediaKind.VIDEO,
    "video/mpeg": MediaKind.VIDEO,
    "video/quicktime": MediaKind.VIDEO,
    "video/webm": MediaKind.VIDEO,
    "video/x-matroska": MediaKind.VIDEO,
    "audio/aac": MediaKind.AUDIO,
    "audio/flac": MediaKind.AUDIO,
    "audio/mp4": MediaKind.AUDIO,
    "audio/mpeg": MediaKind.AUDIO,
    "audio/ogg": MediaKind.AUDIO,
    "audio/wav": MediaKind.AUDIO,
    "audio/webm": MediaKind.AUDIO,
}

_MEDIA_TYPE_ALIASES = {
    "audio/x-flac": "audio/flac",
    "audio/x-wav": "audio/wav",
    "image/jpg": "image/jpeg",
}

_EXTENSION_MEDIA_TYPES = {
    ".aac": "audio/aac",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".mka": "audio/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".wav": "audio/wav",
    ".weba": "audio/webm",
    ".webm": "video/webm",
    ".webp": "image/webp",
}


class MediaValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MediaAssetDerivation:
    operation: str
    source_asset_id: str
    start_seconds: float
    end_seconds: float
    recipe: str

    def __post_init__(self) -> None:
        if self.operation != VIDEO_TRIM_OPERATION:
            raise MediaValidationError("Media derivation operation is invalid.")
        if _ASSET_ID.fullmatch(self.source_asset_id) is None:
            raise MediaValidationError("Media derivation source asset ID is invalid.")
        if (
            isinstance(self.start_seconds, bool)
            or isinstance(self.end_seconds, bool)
            or not math.isfinite(self.start_seconds)
            or self.start_seconds < 0
            or not math.isfinite(self.end_seconds)
            or self.end_seconds <= self.start_seconds
        ):
            raise MediaValidationError("Media derivation trim bounds are invalid.")
        if self.recipe != VIDEO_TRIM_RECIPE:
            raise MediaValidationError("Media derivation recipe is invalid.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "source_asset_id": self.source_asset_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "recipe": self.recipe,
        }


@dataclass(frozen=True, slots=True)
class MediaAsset:
    id: str
    project_ids: tuple[str, ...]
    name: str
    kind: MediaKind
    source: str
    media_type: str
    sha256: str
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    created_at: datetime
    derivation: MediaAssetDerivation | None = None

    def __post_init__(self) -> None:
        if _ASSET_ID.fullmatch(self.id) is None:
            raise MediaValidationError("Media asset ID is invalid.")
        if tuple(sorted(set(self.project_ids))) != self.project_ids:
            raise MediaValidationError("Project IDs must be unique and sorted.")
        if any(not project_id.strip() for project_id in self.project_ids):
            raise MediaValidationError("Project IDs cannot be empty.")
        if validate_file_name(self.name) != self.name:
            raise MediaValidationError("Media asset name is not normalized.")
        if not isinstance(self.kind, MediaKind):
            raise MediaValidationError("Media asset kind is invalid.")
        if _SOURCE.fullmatch(self.source) is None:
            raise MediaValidationError("Media asset source is invalid.")
        if normalize_media_type(self.media_type) != self.media_type:
            raise MediaValidationError("Media type is not normalized or supported.")
        if kind_for_media_type(self.media_type) is not self.kind:
            raise MediaValidationError("Media type does not match media kind.")
        if _SHA256.fullmatch(self.sha256) is None:
            raise MediaValidationError("Media asset SHA-256 is invalid.")
        if isinstance(self.size_bytes, bool) or self.size_bytes <= 0:
            raise MediaValidationError("Media asset size must be positive.")
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise MediaValidationError("Media duration must be finite and non-negative.")
        for label, value in (("width", self.width), ("height", self.height)):
            if value is not None and (isinstance(value, bool) or value <= 0):
                raise MediaValidationError(f"Media {label} must be positive.")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise MediaValidationError("Media asset creation time must be timezone-aware.")
        if self.source == "trim" and self.derivation is None:
            raise MediaValidationError("Trim assets must preserve their derivation.")
        if self.derivation is not None:
            if self.kind is not MediaKind.VIDEO or self.source != "trim":
                raise MediaValidationError(
                    "Derived video trims must be video assets with the trim source."
                )
            if self.derivation.source_asset_id == self.id:
                raise MediaValidationError("A derived media asset cannot be its own source.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_ids": list(self.project_ids),
            "name": self.name,
            "kind": self.kind.value,
            "source": self.source,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "derivation": self.derivation.to_dict() if self.derivation is not None else None,
        }


def validate_file_name(value: str) -> str:
    if not isinstance(value, str):
        raise MediaValidationError("A file name is required.")
    name = value.strip()
    if not name or len(name) > 255:
        raise MediaValidationError("File name must contain 1 to 255 characters.")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise MediaValidationError("File name must not contain a path.")
    if _CONTROL_CHARACTERS.search(name):
        raise MediaValidationError("File name contains control characters.")
    if PurePath(name).name != name:
        raise MediaValidationError("File name must not contain a path.")
    return name


def validate_source(value: str) -> str:
    source = value.strip().lower()
    if _SOURCE.fullmatch(source) is None:
        raise MediaValidationError(
            "Media source must be a lowercase identifier up to 80 characters."
        )
    return source


def normalize_media_type(value: str) -> str:
    media_type = value.partition(";")[0].strip().lower()
    media_type = _MEDIA_TYPE_ALIASES.get(media_type, media_type)
    if media_type not in _MEDIA_TYPES:
        raise MediaValidationError("Unsupported media type.")
    return media_type


def resolve_media_type(file_name: str, declared_media_type: str | None) -> str:
    name = validate_file_name(file_name)
    inferred = _EXTENSION_MEDIA_TYPES.get(PurePath(name).suffix.lower())
    declared = (declared_media_type or "").partition(";")[0].strip().lower()
    if not declared or declared == "application/octet-stream":
        if inferred is None:
            raise MediaValidationError(
                "A supported Content-Type or recognized file extension is required."
            )
        return inferred

    normalized = normalize_media_type(declared)
    if inferred is not None and normalize_media_type(inferred) != normalized:
        raise MediaValidationError("Content-Type does not match the file extension.")
    return normalized


def kind_for_media_type(media_type: str) -> MediaKind:
    try:
        return _MEDIA_TYPES[normalize_media_type(media_type)]
    except KeyError as exc:  # pragma: no cover - normalize_media_type guards this
        raise MediaValidationError("Unsupported media type.") from exc
