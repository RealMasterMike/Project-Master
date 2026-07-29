from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CHUNK_BYTES = 1024 * 1024


class MediaArtifactError(RuntimeError):
    pass


class MediaArtifactTooLargeError(MediaArtifactError):
    pass


class MediaArtifactIntegrityError(MediaArtifactError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMediaObject:
    sha256: str
    size_bytes: int
    path: Path


class FilesystemMediaArtifactStore:
    """Immutable SHA-256-addressed objects owned by Project Master."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        _ensure_private_directory(self.root)
        self.staging_directory = self.root / ".staging"
        _ensure_private_directory(self.staging_directory)

    def import_file(
        self,
        staged_path: str | Path,
        *,
        max_size_bytes: int,
    ) -> StoredMediaObject:
        source = Path(staged_path)
        if not source.is_file():
            raise FileNotFoundError(f"Staged media file does not exist: {source}")
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")

        temporary_path: Path | None = None
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".media-object-",
                dir=self.staging_directory,
            )
            temporary_path = Path(temporary_name)
            with source.open("rb") as reader, os.fdopen(file_descriptor, "wb") as writer:
                while chunk := reader.read(_CHUNK_BYTES):
                    size_bytes += len(chunk)
                    if size_bytes > max_size_bytes:
                        raise MediaArtifactTooLargeError(
                            f"Media file exceeds the {max_size_bytes}-byte limit."
                        )
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if size_bytes <= 0:
                raise MediaArtifactError("Media file cannot be empty.")

            sha256 = digest.hexdigest()
            destination = self._object_path(sha256)
            _ensure_private_directory(destination.parent)
            if destination.exists():
                self._verify(destination, sha256, size_bytes)
                _ensure_private_file(destination)
                temporary_path.unlink(missing_ok=True)
                temporary_path = None
            else:
                os.replace(temporary_path, destination)
                _ensure_private_file(destination)
                temporary_path = None
            return StoredMediaObject(sha256, size_bytes, destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def verified_path(self, sha256: str, size_bytes: int) -> Path:
        path = self._object_path(sha256)
        self._verify(path, sha256, size_bytes)
        return path

    def _object_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise MediaArtifactIntegrityError("Invalid media object checksum.")
        return self.root / sha256[:2] / sha256

    @staticmethod
    def _verify(path: Path, expected_sha256: str, expected_size: int) -> None:
        if not path.is_file():
            raise MediaArtifactIntegrityError("Media object is missing.")
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as reader:
            while chunk := reader.read(_CHUNK_BYTES):
                size_bytes += len(chunk)
                digest.update(chunk)
        if size_bytes != expected_size or digest.hexdigest() != expected_sha256:
            raise MediaArtifactIntegrityError("Media object failed SHA-256 integrity verification.")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)


def _ensure_private_file(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
