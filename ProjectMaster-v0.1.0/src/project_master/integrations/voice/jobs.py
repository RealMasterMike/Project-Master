from __future__ import annotations

import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.voice.cache import VoiceChunkPlan
from project_master.integrations.voice.profiles import RenderPurpose
from project_master.integrations.voice.projects import (
    RenderSettings,
    VoiceWorkflowOrigin,
)

VOICE_RENDER_OWNER_PREFIX = "voice-job-"


class RenderJobStatus(StrEnum):
    PLANNED = "planned"
    WAITING_RESOURCE = "waiting_resource"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class RenderChunkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    CACHED = "cached"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def complete(self) -> bool:
        return self in {self.SUCCEEDED, self.CACHED}


_JOB_TRANSITIONS: dict[RenderJobStatus, frozenset[RenderJobStatus]] = {
    RenderJobStatus.PLANNED: frozenset(
        {RenderJobStatus.WAITING_RESOURCE, RenderJobStatus.CANCELLED}
    ),
    RenderJobStatus.WAITING_RESOURCE: frozenset(
        {
            RenderJobStatus.RUNNING,
            RenderJobStatus.SUCCEEDED,
            RenderJobStatus.CANCEL_REQUESTED,
            RenderJobStatus.FAILED,
            RenderJobStatus.INTERRUPTED,
        }
    ),
    RenderJobStatus.RUNNING: frozenset(
        {
            RenderJobStatus.CANCEL_REQUESTED,
            RenderJobStatus.SUCCEEDED,
            RenderJobStatus.FAILED,
            RenderJobStatus.INTERRUPTED,
        }
    ),
    RenderJobStatus.CANCEL_REQUESTED: frozenset(
        {
            RenderJobStatus.CANCELLED,
            RenderJobStatus.SUCCEEDED,
            RenderJobStatus.FAILED,
            RenderJobStatus.INTERRUPTED,
        }
    ),
    RenderJobStatus.INTERRUPTED: frozenset(
        {RenderJobStatus.WAITING_RESOURCE, RenderJobStatus.CANCELLED}
    ),
    RenderJobStatus.SUCCEEDED: frozenset(),
    RenderJobStatus.FAILED: frozenset(),
    RenderJobStatus.CANCELLED: frozenset(),
}


class RenderJobStateError(RuntimeError):
    pass


class RenderJobNotFoundError(RenderJobStateError):
    pass


class RenderJobConflictError(RenderJobStateError):
    pass


class RenderChunkState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: VoiceChunkPlan
    status: RenderChunkStatus = RenderChunkStatus.PENDING
    artifact_id: str | None = None
    engine_run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = Field(default=None, max_length=1000)
    attempts: int = Field(default=0, ge=0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Voice chunk timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_state(self) -> RenderChunkState:
        if self.status.complete and not self.artifact_id:
            raise ValueError("Completed voice chunks require an artifact.")
        if self.status == RenderChunkStatus.FAILED and not self.error:
            raise ValueError("Failed voice chunks require an error.")
        if self.status in {
            RenderChunkStatus.SUCCEEDED,
            RenderChunkStatus.CACHED,
            RenderChunkStatus.FAILED,
            RenderChunkStatus.CANCELLED,
        } and self.finished_at is None:
            raise ValueError("Finished voice chunks require finished_at.")
        return self

    def running(self, *, now: datetime | None = None) -> RenderChunkState:
        if self.status != RenderChunkStatus.PENDING:
            raise RenderJobStateError(f"Cannot start chunk in state {self.status}.")
        timestamp = now or datetime.now(UTC)
        return RenderChunkState.model_validate(
            self.model_copy(
                update={
                    "status": RenderChunkStatus.RUNNING,
                    "started_at": timestamp,
                    "attempts": self.attempts + 1,
                    "error": None,
                }
            ).model_dump()
        )

    def completed(
        self,
        artifact_id: str,
        *,
        cached: bool,
        engine_run_id: str | None = None,
        now: datetime | None = None,
    ) -> RenderChunkState:
        if self.status not in {RenderChunkStatus.PENDING, RenderChunkStatus.RUNNING}:
            raise RenderJobStateError(f"Cannot complete chunk in state {self.status}.")
        timestamp = now or datetime.now(UTC)
        return RenderChunkState.model_validate(
            self.model_copy(
                update={
                    "status": (
                        RenderChunkStatus.CACHED if cached else RenderChunkStatus.SUCCEEDED
                    ),
                    "artifact_id": artifact_id,
                    "engine_run_id": engine_run_id,
                    "started_at": self.started_at or timestamp,
                    "finished_at": timestamp,
                }
            ).model_dump()
        )

    def failed(self, error: str, *, now: datetime | None = None) -> RenderChunkState:
        if self.status != RenderChunkStatus.RUNNING:
            raise RenderJobStateError(f"Cannot fail chunk in state {self.status}.")
        return RenderChunkState.model_validate(
            self.model_copy(
                update={
                    "status": RenderChunkStatus.FAILED,
                    "error": error[:1000],
                    "finished_at": now or datetime.now(UTC),
                }
            ).model_dump()
        )

    def cancelled(self, *, now: datetime | None = None) -> RenderChunkState:
        if self.status.complete or self.status == RenderChunkStatus.CANCELLED:
            return self
        return RenderChunkState.model_validate(
            self.model_copy(
                update={
                    "status": RenderChunkStatus.CANCELLED,
                    "finished_at": now or datetime.now(UTC),
                }
            ).model_dump()
        )

    def interrupted(self) -> RenderChunkState:
        if self.status != RenderChunkStatus.RUNNING:
            return self
        return RenderChunkState.model_validate(
            self.model_copy(
                update={
                    "status": RenderChunkStatus.PENDING,
                    "artifact_id": None,
                    "engine_run_id": None,
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                }
            ).model_dump()
        )


class RenderJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    project_id: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    project_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_pack_id: str
    engine_pack_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: RenderPurpose
    settings: RenderSettings
    chunks: tuple[RenderChunkState, ...]
    origin: VoiceWorkflowOrigin = VoiceWorkflowOrigin.VOICE_STUDIO
    status: RenderJobStatus = RenderJobStatus.PLANNED
    resource_lease_id: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = Field(default=None, max_length=1000)
    version: int = Field(default=1, ge=1)

    @classmethod
    def new(
        cls,
        *,
        job_id: str,
        project_id: str,
        project_digest: str,
        engine_pack_id: str,
        engine_pack_digest: str,
        purpose: RenderPurpose,
        settings: RenderSettings,
        plans: tuple[VoiceChunkPlan, ...],
        origin: VoiceWorkflowOrigin = VoiceWorkflowOrigin.VOICE_STUDIO,
        now: datetime | None = None,
    ) -> RenderJob:
        timestamp = now or datetime.now(UTC)
        return cls(
            id=job_id,
            project_id=project_id,
            project_digest=project_digest,
            engine_pack_id=engine_pack_id,
            engine_pack_digest=engine_pack_digest,
            purpose=purpose,
            settings=settings,
            chunks=tuple(RenderChunkState(plan=plan) for plan in plans),
            origin=origin,
            created_at=timestamp,
            updated_at=timestamp,
        )

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_chat_speech_origin(cls, value: Any) -> Any:
        """Classify chat-speech jobs persisted before origin metadata existed."""
        if (
            isinstance(value, dict)
            and "origin" not in value
            and str(value.get("project_id", "")).startswith("chat-speech-")
        ):
            return {**value, "origin": VoiceWorkflowOrigin.CHAT_SPEECH}
        return value

    @field_validator("created_at", "updated_at", "started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Voice render job timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_job(self) -> RenderJob:
        if not self.chunks:
            raise ValueError("Voice render job requires at least one chunk.")
        ids = [chunk.plan.id for chunk in self.chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("Voice render job chunk IDs must be unique.")
        if self.status == RenderJobStatus.SUCCEEDED:
            if not all(chunk.status.complete for chunk in self.chunks):
                raise ValueError("Successful voice render job has incomplete chunks.")
        if self.status == RenderJobStatus.FAILED and not self.error:
            raise ValueError("Failed voice render job requires an error.")
        if self.status.terminal and self.finished_at is None:
            raise ValueError("Terminal voice render job requires finished_at.")
        if not self.status.terminal and self.finished_at is not None:
            raise ValueError("Non-terminal voice render job cannot have finished_at.")
        return self

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            chunk.artifact_id
            for chunk in self.chunks
            if chunk.artifact_id is not None and chunk.status.complete
        )

    @property
    def studio_visible(self) -> bool:
        return self.origin.studio_visible

    def transition(
        self,
        status: RenderJobStatus,
        *,
        error: str | None = None,
        lease_id: str | None = None,
        now: datetime | None = None,
    ) -> RenderJob:
        if status != self.status and status not in _JOB_TRANSITIONS[self.status]:
            raise RenderJobStateError(f"Invalid voice job transition: {self.status} -> {status}.")
        timestamp = now or datetime.now(UTC)
        started_at = self.started_at
        if status == RenderJobStatus.RUNNING and started_at is None:
            started_at = timestamp
        finished_at = timestamp if status.terminal else None
        chunks = self.chunks
        if status == RenderJobStatus.CANCELLED:
            chunks = tuple(chunk.cancelled(now=timestamp) for chunk in chunks)
        elif status == RenderJobStatus.INTERRUPTED:
            chunks = tuple(chunk.interrupted() for chunk in chunks)
        effective_lease_id = lease_id
        if (
            effective_lease_id is None
            and status in {RenderJobStatus.RUNNING, RenderJobStatus.CANCEL_REQUESTED}
        ):
            effective_lease_id = self.resource_lease_id
        candidate = self.model_copy(
            update={
                "status": status,
                "chunks": chunks,
                "resource_lease_id": effective_lease_id,
                "updated_at": timestamp,
                "started_at": started_at,
                "finished_at": finished_at,
                "error": error[:1000] if error else None,
            },
            deep=True,
        )
        return RenderJob.model_validate(candidate.model_dump())

    def replace_chunk(self, replacement: RenderChunkState) -> RenderJob:
        found = False
        chunks: list[RenderChunkState] = []
        for chunk in self.chunks:
            if chunk.plan.id == replacement.plan.id:
                chunks.append(replacement)
                found = True
            else:
                chunks.append(chunk)
        if not found:
            raise RenderJobStateError(f"Unknown voice chunk {replacement.plan.id!r}.")
        candidate = self.model_copy(
            update={"chunks": tuple(chunks), "updated_at": datetime.now(UTC)},
            deep=True,
        )
        return RenderJob.model_validate(candidate.model_dump())


class RenderJobRepository(Protocol):
    def create(self, job: RenderJob) -> RenderJob: ...

    def get(self, job_id: str) -> RenderJob: ...

    def save(self, job: RenderJob, *, expected_version: int) -> RenderJob: ...

    def list(self) -> tuple[RenderJob, ...]: ...


class InMemoryRenderJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, RenderJob] = {}
        self._lock = threading.RLock()

    def create(self, job: RenderJob) -> RenderJob:
        validated = RenderJob.model_validate(job.model_dump())
        with self._lock:
            if validated.id in self._jobs:
                raise RenderJobConflictError(f"Voice render job {validated.id!r} exists.")
            self._jobs[validated.id] = validated.model_copy(deep=True)
            return validated.model_copy(deep=True)

    def get(self, job_id: str) -> RenderJob:
        with self._lock:
            try:
                return self._jobs[job_id].model_copy(deep=True)
            except KeyError as exc:
                raise RenderJobNotFoundError(
                    f"Voice render job {job_id!r} does not exist."
                ) from exc

    def save(self, job: RenderJob, *, expected_version: int) -> RenderJob:
        validated = RenderJob.model_validate(job.model_dump())
        with self._lock:
            current = self._jobs.get(validated.id)
            if current is None:
                raise RenderJobNotFoundError(
                    f"Voice render job {validated.id!r} does not exist."
                )
            if current.version != expected_version or validated.version != expected_version:
                raise RenderJobConflictError(
                    f"Voice render job {validated.id!r} changed concurrently."
                )
            saved = validated.model_copy(update={"version": expected_version + 1}, deep=True)
            saved = RenderJob.model_validate(saved.model_dump())
            self._jobs[saved.id] = saved
            return saved.model_copy(deep=True)

    def list(self) -> tuple[RenderJob, ...]:
        with self._lock:
            return tuple(
                job.model_copy(deep=True)
                for job in sorted(
                    self._jobs.values(), key=lambda item: (item.created_at, item.id)
                )
            )

    def export_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                job.model_dump(mode="json")
                for job in sorted(self._jobs.values(), key=lambda item: item.id)
            ]

    @classmethod
    def restore_snapshot(
        cls, snapshot: list[dict[str, Any]]
    ) -> InMemoryRenderJobRepository:
        repository = cls()
        for raw in snapshot:
            repository.create(RenderJob.model_validate(raw))
        return repository
