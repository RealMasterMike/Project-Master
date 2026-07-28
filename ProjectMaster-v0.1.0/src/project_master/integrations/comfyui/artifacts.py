from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.comfyui.transport import (
    DownloadedOutput,
    OutputMetadata,
)

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,16}$")
_SAFE_RELATIVE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SAFE_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
_ACTIVE_MEDIA_TYPES = frozenset(
    {
        "application/javascript",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/html",
        "text/javascript",
    }
)
_SAFE_STORED_MEDIA_TYPES = frozenset(
    {
        "application/gzip",
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/zip",
        "model/gltf-binary",
        "model/gltf+json",
        "text/csv",
        "text/plain",
    }
)


class ComfyArtifactProvenance(BaseModel):
    """Immutable evidence connecting a local artifact to its remote ComfyUI output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    profile_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    workflow_revision_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    remote_prompt_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    output: OutputMetadata
    history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_status: str | None = Field(default=None, max_length=500)
    source_base_url: str = Field(min_length=1, max_length=2_048)
    source_url: str = Field(min_length=1, max_length=4_096)
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ComfyUI artifact timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def require_exact_source_origin(self) -> ComfyArtifactProvenance:
        if _url_origin(self.source_base_url) != _url_origin(self.source_url):
            raise ValueError("ComfyUI artifact source must remain on the profile origin.")
        base = urlsplit(self.source_base_url)
        source = urlsplit(self.source_url)
        if base.query or base.fragment or source.fragment:
            raise ValueError("ComfyUI artifact source URL is invalid.")
        expected_path = f"{base.path.rstrip('/')}/view"
        if source.path != expected_path:
            raise ValueError("ComfyUI artifact source must use the official /view route.")
        try:
            query = parse_qs(
                source.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError as exc:
            raise ValueError("ComfyUI artifact source query is invalid.") from exc
        expected_query = {
            "filename": [self.output.ref.filename],
            "subfolder": [self.output.ref.subfolder],
            "type": [self.output.ref.type],
        }
        if query != expected_query:
            raise ValueError("ComfyUI artifact source does not match its output locator.")
        return self


class ComfyArtifact(BaseModel):
    """A verified, app-owned copy of one authoritative ComfyUI output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^comfy-artifact-[0-9a-f]{40}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: str = Field(min_length=1, max_length=127)
    original_filename: str = Field(min_length=1, max_length=240)
    relative_path: str = Field(min_length=1, max_length=600)
    created_at: datetime
    verified: Literal[True] = True
    provenance: ComfyArtifactProvenance

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ComfyUI artifact timestamps must include a timezone.")
        return value

    @field_validator("relative_path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        if "\\" in value or "\x00" in value:
            raise ValueError("ComfyUI artifact paths must use safe POSIX components.")
        path = PurePosixPath(value)
        if path.is_absolute() or any(
            part in {"", ".", ".."} or not _SAFE_RELATIVE_PART.fullmatch(part)
            for part in path.parts
        ):
            raise ValueError("ComfyUI artifact path is unsafe.")
        return value

    @field_validator("media_type")
    @classmethod
    def require_safe_media_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if (
            not _SAFE_MEDIA_TYPE.fullmatch(normalized)
            or normalized in _ACTIVE_MEDIA_TYPES
            or (
                normalized not in _SAFE_STORED_MEDIA_TYPES
                and normalized.split("/", 1)[0] not in {"audio", "image", "video"}
            )
        ):
            raise ValueError("ComfyUI artifact media type is unsafe.")
        return normalized

    @model_validator(mode="after")
    def bind_storage_path_to_provenance(self) -> ComfyArtifact:
        expected = f"jobs/{self.provenance.job_id}/{self.id}{_safe_suffix(self.original_filename)}"
        if self.relative_path != expected:
            raise ValueError("ComfyUI artifact path does not match its provenance.")
        return self


class ComfyArtifactStore(Protocol):
    def store(
        self,
        download: DownloadedOutput,
        provenance: ComfyArtifactProvenance,
    ) -> ComfyArtifact: ...

    def path_for(self, artifact: ComfyArtifact) -> Path: ...

    def read(self, artifact: ComfyArtifact) -> bytes: ...


class FilesystemComfyArtifactStore:
    """Content-addressed artifact storage with atomic content and manifest promotion."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_artifact_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if (
            not isinstance(max_artifact_bytes, int)
            or isinstance(max_artifact_bytes, bool)
            or max_artifact_bytes < 1
        ):
            raise ValueError("ComfyUI artifact size limit must be a positive integer.")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o700)
        self.max_artifact_bytes = max_artifact_bytes

    def store(
        self,
        download: DownloadedOutput,
        provenance: ComfyArtifactProvenance,
    ) -> ComfyArtifact:
        validated_provenance = ComfyArtifactProvenance.model_validate(provenance.model_dump())
        content = bytes(download.content)
        if not content:
            raise ValueError("ComfyUI returned an empty artifact.")
        if len(content) > self.max_artifact_bytes:
            raise ValueError("ComfyUI artifact exceeds the configured storage size limit.")
        if download.source_url != validated_provenance.source_url:
            raise ValueError("ComfyUI download provenance does not match its source URL.")
        expected_media_type = validated_provenance.output.media_type
        if expected_media_type is not None:
            expected_media_type = expected_media_type.lower()
            if expected_media_type.endswith("/*"):
                if download.media_type.split("/", 1)[0] != expected_media_type[:-2]:
                    raise ValueError(
                        "ComfyUI artifact media type does not match its output metadata."
                    )
            elif download.media_type != expected_media_type:
                raise ValueError("ComfyUI artifact media type does not match its output metadata.")
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"comfy-artifact-{_identity_digest(digest, validated_provenance)[:40]}"
        suffix = _safe_suffix(validated_provenance.output.ref.filename)
        relative_path = f"jobs/{validated_provenance.job_id}/{artifact_id}{suffix}"
        artifact = ComfyArtifact(
            id=artifact_id,
            sha256=digest,
            size_bytes=len(content),
            media_type=download.media_type,
            original_filename=validated_provenance.output.ref.filename,
            relative_path=relative_path,
            created_at=datetime.now(UTC),
            provenance=validated_provenance,
        )
        target = self._resolve(relative_path)
        manifest = self._resolve(f"jobs/{validated_provenance.job_id}/{artifact_id}.metadata.json")
        if manifest.exists():
            existing = ComfyArtifact.model_validate_json(manifest.read_text("utf-8"))
            if (
                existing.id != artifact.id
                or existing.sha256 != artifact.sha256
                or _identity_payload(existing.sha256, existing.provenance)
                != _identity_payload(artifact.sha256, artifact.provenance)
            ):
                raise ValueError("ComfyUI artifact ID collision.")
            try:
                self._verify_path(existing, target)
            except ValueError:
                _atomic_write(target, content, replace_mismatch=True)
                self._verify_path(existing, target)
            return existing

        _atomic_write(target, content, replace_mismatch=True)
        try:
            _atomic_write(
                manifest,
                f"{artifact.model_dump_json(indent=2)}\n".encode(),
            )
        except ValueError:
            # A concurrent identical import may have promoted its manifest first.
            existing = ComfyArtifact.model_validate_json(manifest.read_text("utf-8"))
            if existing.id != artifact.id or _identity_payload(
                existing.sha256, existing.provenance
            ) != _identity_payload(artifact.sha256, artifact.provenance):
                raise
            self._verify_path(existing, target)
            return existing
        return artifact

    def path_for(self, artifact: ComfyArtifact) -> Path:
        validated = ComfyArtifact.model_validate(artifact.model_dump())
        target = self._resolve(validated.relative_path)
        self._verify_path(validated, target)
        return target

    def read(self, artifact: ComfyArtifact) -> bytes:
        return self.path_for(artifact).read_bytes()

    def _resolve(self, relative_path: str) -> Path:
        parts = PurePosixPath(relative_path).parts
        candidate = self.root.joinpath(*parts).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("ComfyUI artifact path escapes its storage root.") from exc
        return candidate

    @staticmethod
    def _verify_path(artifact: ComfyArtifact, target: Path) -> None:
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise ValueError("ComfyUI artifact content is unavailable.") from exc
        if (
            len(content) != artifact.size_bytes
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise ValueError("ComfyUI artifact content failed checksum verification.")


def _identity_payload(
    digest: str,
    provenance: ComfyArtifactProvenance,
) -> dict[str, object]:
    return {
        "sha256": digest,
        "job_id": provenance.job_id,
        "profile_id": provenance.profile_id,
        "workflow_revision_id": provenance.workflow_revision_id,
        "workflow_digest": provenance.workflow_digest,
        "remote_prompt_id": provenance.remote_prompt_id,
        "output": provenance.output.model_dump(mode="json"),
        "history_sha256": provenance.history_sha256,
        "history_status": provenance.history_status,
        "source_base_url": provenance.source_base_url,
        "source_url": provenance.source_url,
    }


def _identity_digest(
    digest: str,
    provenance: ComfyArtifactProvenance,
) -> str:
    encoded = json.dumps(
        _identity_payload(digest, provenance),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix
    return suffix.lower() if _SAFE_SUFFIX.fullmatch(suffix) else ""


def _url_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("ComfyUI artifact source URL is invalid.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ComfyUI artifact source URL cannot contain credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ComfyUI artifact source URL has an invalid port.") from exc
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower().rstrip("."), effective_port


def _atomic_write(
    target: Path,
    content: bytes,
    *,
    replace_mismatch: bool = False,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        matches = (
            target.stat().st_size != len(content)
            or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(content).digest()
        )
        if not matches:
            return
        if not replace_mismatch:
            raise ValueError("Existing ComfyUI artifact content does not match its digest.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
