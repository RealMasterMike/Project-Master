from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import wave
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from project_master.integrations.voice.artifacts import (
    VoiceArtifact,
    VoiceArtifactProvenance,
    VoiceArtifactStore,
)
from project_master.integrations.voice.cache import ChunkCache, ChunkCacheEntry
from project_master.integrations.voice.engine import RenderedAudio
from project_master.integrations.voice.jobs import (
    RenderJob,
    RenderJobConflictError,
    RenderJobNotFoundError,
    RenderJobRepository,
    RenderJobStatus,
)
from project_master.integrations.voice.manifests import InstalledEnginePack
from project_master.integrations.voice.profiles import VoiceProfile, VoiceReference
from project_master.integrations.voice.projects import VoiceProject
from project_master.memory.store import SQLiteStore

_MAX_REFERENCE_BYTES = 64 * 1024 * 1024


class SQLiteVoiceStore:
    """Durable Voice Studio registry, job, cache, reference, and artifact store."""

    def __init__(self, store: SQLiteStore, artifact_root: str | Path) -> None:
        self.store = store
        self.artifact_root = Path(artifact_root).resolve()
        self.reference_root = self.artifact_root / "references"
        self.render_root = self.artifact_root / "renders"
        self.reference_root.mkdir(parents=True, exist_ok=True)
        self.render_root.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.jobs: RenderJobRepository = SQLiteRenderJobRepository(self)
        self.cache: ChunkCache = SQLiteVoiceChunkCache(self)
        self.artifacts: VoiceArtifactStore = FilesystemVoiceArtifactStore(self)
        self.recover_interrupted_jobs()

    def _initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_profiles (
                    id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(id, revision)
                );

                CREATE TABLE IF NOT EXISTS voice_projects (
                    id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(id, revision)
                );

                CREATE TABLE IF NOT EXISTS voice_engine_packs (
                    id TEXT PRIMARY KEY,
                    engine_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS voice_references (
                    artifact_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS voice_render_jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS voice_chunk_cache (
                    cache_key TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS voice_artifacts (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_voice_jobs_created
                    ON voice_render_jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_voice_artifacts_created
                    ON voice_artifacts(created_at DESC);
                """
            )

    def save_profile(self, profile: VoiceProfile) -> VoiceProfile:
        validated = VoiceProfile.model_validate(profile.model_dump())
        try:
            with self.store.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO voice_profiles(
                        id, revision, payload_json, digest, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        validated.id,
                        validated.revision,
                        validated.model_dump_json(),
                        validated.digest,
                        validated.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get_profile(validated.id, revision=validated.revision)
            if existing != validated:
                raise ValueError(
                    "Voice profile revision already exists with different content."
                ) from None
            return existing
        return validated.model_copy(deep=True)

    def get_profile(
        self,
        profile_id: str,
        *,
        revision: int | None = None,
    ) -> VoiceProfile:
        version_clause = "AND revision = ?" if revision is not None else ""
        params: tuple[Any, ...] = (
            (profile_id, revision) if revision is not None else (profile_id,)
        )
        with self.store.connection() as conn:
            row = conn.execute(
                f"""
                SELECT payload_json FROM voice_profiles
                WHERE id = ? {version_clause}
                ORDER BY revision DESC LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown voice profile: {profile_id}")
        return VoiceProfile.model_validate_json(str(row["payload_json"]))

    def list_profiles(self) -> tuple[VoiceProfile, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT profile.payload_json
                FROM voice_profiles AS profile
                JOIN (
                    SELECT id, MAX(revision) AS revision
                    FROM voice_profiles GROUP BY id
                ) AS latest
                ON latest.id = profile.id AND latest.revision = profile.revision
                ORDER BY profile.id
                """
            ).fetchall()
        return tuple(
            VoiceProfile.model_validate_json(str(row["payload_json"])) for row in rows
        )

    def save_project(self, project: VoiceProject) -> VoiceProject:
        validated = VoiceProject.model_validate(project.model_dump())
        try:
            with self.store.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO voice_projects(
                        id, revision, payload_json, digest, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        validated.id,
                        validated.revision,
                        validated.model_dump_json(),
                        validated.digest,
                        validated.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get_project(validated.id, revision=validated.revision)
            if existing != validated:
                raise ValueError(
                    "Voice project revision already exists with different content."
                ) from None
            return existing
        return validated.model_copy(deep=True)

    def get_project(
        self,
        project_id: str,
        *,
        revision: int | None = None,
    ) -> VoiceProject:
        version_clause = "AND revision = ?" if revision is not None else ""
        params: tuple[Any, ...] = (
            (project_id, revision) if revision is not None else (project_id,)
        )
        with self.store.connection() as conn:
            row = conn.execute(
                f"""
                SELECT payload_json FROM voice_projects
                WHERE id = ? {version_clause}
                ORDER BY revision DESC LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown voice project: {project_id}")
        return VoiceProject.model_validate_json(str(row["payload_json"]))

    def list_projects(self) -> tuple[VoiceProject, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT project.payload_json
                FROM voice_projects AS project
                JOIN (
                    SELECT id, MAX(revision) AS revision
                    FROM voice_projects GROUP BY id
                ) AS latest
                ON latest.id = project.id AND latest.revision = project.revision
                ORDER BY project.id
                """
            ).fetchall()
        return tuple(
            VoiceProject.model_validate_json(str(row["payload_json"])) for row in rows
        )

    def upsert_pack(self, pack: InstalledEnginePack) -> InstalledEnginePack:
        validated = InstalledEnginePack.model_validate(pack.model_dump())
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO voice_engine_packs(
                    id, engine_id, payload_json, digest, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    engine_id = excluded.engine_id,
                    payload_json = excluded.payload_json,
                    digest = excluded.digest,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.id,
                    validated.engine_id,
                    validated.model_dump_json(),
                    validated.digest,
                    _now(),
                ),
            )
        return validated.model_copy(deep=True)

    def list_packs(self) -> tuple[InstalledEnginePack, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM voice_engine_packs ORDER BY id"
            ).fetchall()
        return tuple(
            InstalledEnginePack.model_validate_json(str(row["payload_json"]))
            for row in rows
        )

    def import_reference_wav(
        self,
        content: bytes,
        *,
        original_name: str,
        transcript: str | None = None,
    ) -> VoiceReference:
        if not content or len(content) > _MAX_REFERENCE_BYTES:
            raise ValueError("Voice reference must be between 1 byte and 64 MiB.")
        if not original_name.lower().endswith(".wav"):
            raise ValueError("Voice Studio currently imports WAV references.")
        try:
            with wave.open(BytesIO(content), "rb") as handle:
                channels = handle.getnchannels()
                sample_rate = handle.getframerate()
                frames = handle.getnframes()
                sample_width = handle.getsampwidth()
        except (EOFError, wave.Error) as exc:
            raise ValueError("Voice reference is not a valid WAV file.") from exc
        duration = frames / sample_rate if sample_rate else 0
        if channels not in {1, 2}:
            raise ValueError("Voice references must contain one or two channels.")
        if sample_width not in {1, 2, 3, 4}:
            raise ValueError("Voice reference uses an unsupported sample width.")
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"voice-reference-{digest[:24]}"
        relative_path = f"references/{artifact_id}.wav"
        target = self._resolve(relative_path)
        _atomic_write(target, content)
        reference = VoiceReference(
            artifact_id=artifact_id,
            sha256=digest,
            media_type="audio/wav",
            duration_seconds=duration,
            sample_rate_hz=sample_rate,
            channels=channels,
            transcript=transcript,
        )
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO voice_references(
                    artifact_id, payload_json, relative_path, original_name, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO NOTHING
                """,
                (
                    artifact_id,
                    reference.model_dump_json(),
                    relative_path,
                    Path(original_name).name[:255],
                    _now(),
                ),
            )
        return reference

    def get_reference(self, artifact_id: str) -> VoiceReference:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voice_references WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown voice reference: {artifact_id}")
        return VoiceReference.model_validate_json(str(row["payload_json"]))

    def list_references(self) -> tuple[VoiceReference, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM voice_references ORDER BY created_at DESC"
            ).fetchall()
        return tuple(
            VoiceReference.model_validate_json(str(row["payload_json"]))
            for row in rows
        )

    def reference_path(self, artifact_id: str) -> Path:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT relative_path FROM voice_references WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown voice reference: {artifact_id}")
        path = self._resolve(str(row["relative_path"]))
        if not path.is_file():
            raise FileNotFoundError(f"Voice reference content is missing: {artifact_id}")
        return path

    def create_job(self, job: RenderJob) -> RenderJob:
        validated = RenderJob.model_validate(job.model_dump())
        try:
            with self.store.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO voice_render_jobs(
                        id, project_id, status, version, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        validated.id,
                        validated.project_id,
                        validated.status.value,
                        validated.version,
                        validated.model_dump_json(),
                        validated.created_at.isoformat(),
                        validated.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RenderJobConflictError(
                f"Voice render job {validated.id!r} exists."
            ) from exc
        return validated.model_copy(deep=True)

    def get_job(self, job_id: str) -> RenderJob:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voice_render_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise RenderJobNotFoundError(
                f"Voice render job {job_id!r} does not exist."
            )
        return RenderJob.model_validate_json(str(row["payload_json"]))

    def save_job(self, job: RenderJob, *, expected_version: int) -> RenderJob:
        validated = RenderJob.model_validate(job.model_dump())
        if validated.version != expected_version:
            raise RenderJobConflictError(
                f"Voice render job {validated.id!r} changed concurrently."
            )
        saved = RenderJob.model_validate(
            validated.model_copy(
                update={"version": expected_version + 1},
                deep=True,
            ).model_dump()
        )
        with self.store.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE voice_render_jobs
                SET status = ?, version = ?, payload_json = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    saved.status.value,
                    saved.version,
                    saved.model_dump_json(),
                    saved.updated_at.isoformat(),
                    saved.id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT 1 FROM voice_render_jobs WHERE id = ?",
                    (saved.id,),
                ).fetchone()
                if exists is None:
                    raise RenderJobNotFoundError(
                        f"Voice render job {saved.id!r} does not exist."
                    )
                raise RenderJobConflictError(
                    f"Voice render job {saved.id!r} changed concurrently."
                )
        return saved.model_copy(deep=True)

    def list_jobs(self) -> tuple[RenderJob, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM voice_render_jobs ORDER BY created_at DESC"
            ).fetchall()
        return tuple(
            RenderJob.model_validate_json(str(row["payload_json"])) for row in rows
        )

    def recover_interrupted_jobs(self) -> int:
        recovered = 0
        for job in self.list_jobs():
            if job.status not in {
                RenderJobStatus.WAITING_RESOURCE,
                RenderJobStatus.RUNNING,
                RenderJobStatus.CANCEL_REQUESTED,
            }:
                continue
            interrupted = job.transition(
                RenderJobStatus.INTERRUPTED,
                error="The app stopped before this voice render reached a terminal state.",
            )
            self.save_job(interrupted, expected_version=job.version)
            recovered += 1
        return recovered

    def get_cache(self, cache_key: str) -> ChunkCacheEntry | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voice_chunk_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        return ChunkCacheEntry.model_validate_json(str(row["payload_json"]))

    def put_cache(self, entry: ChunkCacheEntry) -> None:
        validated = ChunkCacheEntry.model_validate(entry.model_dump())
        with self.store.connection() as conn:
            existing = conn.execute(
                "SELECT artifact_id FROM voice_chunk_cache WHERE cache_key = ?",
                (validated.cache_key,),
            ).fetchone()
            if (
                existing is not None
                and str(existing["artifact_id"]) != validated.artifact_id
            ):
                raise ValueError(
                    "A deterministic voice chunk cannot map to two artifacts."
                )
            conn.execute(
                """
                INSERT INTO voice_chunk_cache(
                    cache_key, artifact_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO NOTHING
                """,
                (
                    validated.cache_key,
                    validated.artifact_id,
                    validated.model_dump_json(),
                    validated.created_at.isoformat(),
                ),
            )

    def remove_cache(self, cache_key: str) -> None:
        with self.store.connection() as conn:
            conn.execute(
                "DELETE FROM voice_chunk_cache WHERE cache_key = ?",
                (cache_key,),
            )

    def store_artifact(
        self,
        audio: RenderedAudio,
        provenance: VoiceArtifactProvenance,
    ) -> VoiceArtifact:
        digest = hashlib.sha256(audio.content).hexdigest()
        cache_suffix = provenance.synthesis_cache_key.removeprefix("voice-cache-")
        artifact_id = f"voice-artifact-{cache_suffix}-{digest[:16]}"
        relative_path = f"renders/{artifact_id}.{audio.format}"
        artifact = VoiceArtifact(
            id=artifact_id,
            sha256=digest,
            size_bytes=len(audio.content),
            format=audio.format,
            media_type=audio.media_type,
            sample_rate_hz=audio.sample_rate_hz,
            channels=audio.channels,
            duration_seconds=audio.duration_seconds,
            created_at=datetime.now(UTC),
            provenance=provenance,
        )
        existing = self.get_artifact(artifact_id)
        if existing is not None:
            if existing.sha256 != artifact.sha256:
                raise ValueError("Voice artifact ID collision.")
            return existing
        _atomic_write(self._resolve(relative_path), audio.content)
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO voice_artifacts(
                    id, payload_json, relative_path, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.model_dump_json(),
                    relative_path,
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def get_artifact(self, artifact_id: str) -> VoiceArtifact | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM voice_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        return VoiceArtifact.model_validate_json(str(row["payload_json"]))

    def list_artifacts(self) -> tuple[VoiceArtifact, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM voice_artifacts ORDER BY created_at DESC"
            ).fetchall()
        return tuple(
            VoiceArtifact.model_validate_json(str(row["payload_json"])) for row in rows
        )

    def read_artifact(self, artifact_id: str) -> bytes:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT relative_path FROM voice_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown voice artifact: {artifact_id}")
        content = self._resolve(str(row["relative_path"])).read_bytes()
        artifact = self.get_artifact(artifact_id)
        if artifact is None or hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ValueError("Voice artifact content failed checksum verification.")
        return content

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self.artifact_root / relative_path).resolve()
        try:
            candidate.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ValueError("Voice artifact path escapes its storage root.") from exc
        return candidate


class SQLiteRenderJobRepository:
    def __init__(self, backend: SQLiteVoiceStore) -> None:
        self.backend = backend

    def create(self, job: RenderJob) -> RenderJob:
        return self.backend.create_job(job)

    def get(self, job_id: str) -> RenderJob:
        return self.backend.get_job(job_id)

    def save(self, job: RenderJob, *, expected_version: int) -> RenderJob:
        return self.backend.save_job(job, expected_version=expected_version)

    def list(self) -> tuple[RenderJob, ...]:
        return self.backend.list_jobs()


class SQLiteVoiceChunkCache:
    def __init__(self, backend: SQLiteVoiceStore) -> None:
        self.backend = backend

    def get(self, cache_key: str) -> ChunkCacheEntry | None:
        return self.backend.get_cache(cache_key)

    def put(self, entry: ChunkCacheEntry) -> None:
        self.backend.put_cache(entry)

    def remove(self, cache_key: str) -> None:
        self.backend.remove_cache(cache_key)


class FilesystemVoiceArtifactStore:
    def __init__(self, backend: SQLiteVoiceStore) -> None:
        self.backend = backend

    def store(
        self,
        audio: RenderedAudio,
        provenance: VoiceArtifactProvenance,
    ) -> VoiceArtifact:
        return self.backend.store_artifact(audio, provenance)

    def get(self, artifact_id: str) -> VoiceArtifact | None:
        return self.backend.get_artifact(artifact_id)

    def read(self, artifact_id: str) -> bytes:
        return self.backend.read_artifact(artifact_id)


def _atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(content).digest():
            raise ValueError("Existing voice artifact content does not match its digest.")
        return
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


def _now() -> str:
    return datetime.now(UTC).isoformat()
