from __future__ import annotations

import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.comfyui.artifacts import ComfyArtifact
from project_master.integrations.comfyui.transport import OutputMetadata


class JobStatus(StrEnum):
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ORPHANED = "orphaned"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.SUBMITTING: frozenset(
        {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.ORPHANED,
        }
    ),
    JobStatus.QUEUED: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.CANCEL_REQUESTED,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.ORPHANED,
        }
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.QUEUED,
            JobStatus.CANCEL_REQUESTED,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.ORPHANED,
        }
    ),
    JobStatus.CANCEL_REQUESTED: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.ORPHANED,
        }
    ),
    JobStatus.ORPHANED: frozenset(
        {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class JobStateError(RuntimeError):
    pass


class JobNotFoundError(JobStateError):
    pass


class JobConflictError(JobStateError):
    pass


class ComfyJob(BaseModel):
    """Serializable state for one Project Master-owned ComfyUI prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    profile_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    workflow_revision_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    client_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    status: JobStatus = JobStatus.SUBMITTING
    remote_prompt_id: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    queue_number: int | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status_detail: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=1000)
    outputs: tuple[OutputMetadata, ...] = ()
    artifacts: tuple[ComfyArtifact, ...] = ()
    artifact_status: ArtifactStatus = ArtifactStatus.PENDING
    artifact_error: str | None = Field(default=None, max_length=1000)
    version: int = Field(default=1, ge=1)

    @field_validator("created_at", "updated_at", "started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("ComfyUI job timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_state(self) -> ComfyJob:
        remote_required = {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.CANCEL_REQUESTED,
            JobStatus.SUCCEEDED,
        }
        if self.status in remote_required and self.remote_prompt_id is None:
            raise ValueError(f"Job status {self.status} requires a remote_prompt_id.")
        if self.status.terminal and self.finished_at is None:
            raise ValueError(f"Terminal job status {self.status} requires finished_at.")
        if not self.status.terminal and self.finished_at is not None:
            raise ValueError("Non-terminal ComfyUI jobs cannot have finished_at.")
        if self.status == JobStatus.FAILED and not self.error:
            raise ValueError("Failed ComfyUI jobs require an error.")
        if self.status != JobStatus.SUCCEEDED and (
            self.artifacts
            or self.artifact_status != ArtifactStatus.PENDING
            or self.artifact_error is not None
        ):
            raise ValueError("Only successful ComfyUI jobs may contain imported artifacts.")
        if self.artifact_status == ArtifactStatus.READY and self.artifact_error:
            raise ValueError("Ready ComfyUI artifacts cannot retain an import error.")
        if self.artifact_status == ArtifactStatus.PARTIAL and (
            not self.artifacts or not self.artifact_error
        ):
            raise ValueError("Partial ComfyUI artifact imports require content and an error.")
        if (
            self.artifact_status
            in {
                ArtifactStatus.FAILED,
                ArtifactStatus.UNAVAILABLE,
            }
            and not self.artifact_error
        ):
            raise ValueError("Incomplete ComfyUI artifact imports require an error.")
        if self.artifact_status == ArtifactStatus.PENDING and (
            self.artifacts or self.artifact_error is not None
        ):
            raise ValueError("Pending ComfyUI artifacts cannot contain results.")
        for artifact in self.artifacts:
            provenance = artifact.provenance
            if (
                provenance.job_id != self.id
                or provenance.profile_id != self.profile_id
                or provenance.workflow_revision_id != self.workflow_revision_id
                or provenance.remote_prompt_id != self.remote_prompt_id
                or provenance.output not in self.outputs
            ):
                raise ValueError("ComfyUI artifact provenance does not match its job.")
        if len({artifact.id for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("ComfyUI jobs cannot contain duplicate artifacts.")
        artifact_outputs = tuple(artifact.provenance.output for artifact in self.artifacts)
        if len(set(item.model_dump_json() for item in artifact_outputs)) != len(artifact_outputs):
            raise ValueError("ComfyUI jobs cannot import an output more than once.")
        if self.artifact_status == ArtifactStatus.READY and artifact_outputs != self.outputs:
            raise ValueError("Ready ComfyUI jobs must import every output in order.")
        if self.updated_at < self.created_at:
            raise ValueError("ComfyUI job updated_at cannot precede created_at.")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("ComfyUI job started_at cannot precede created_at.")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("ComfyUI job finished_at cannot precede created_at.")
        return self

    @classmethod
    def new(
        cls,
        *,
        job_id: str,
        profile_id: str,
        workflow_revision_id: str,
        client_id: str,
        now: datetime | None = None,
    ) -> ComfyJob:
        timestamp = now or datetime.now(UTC)
        return cls(
            id=job_id,
            profile_id=profile_id,
            workflow_revision_id=workflow_revision_id,
            client_id=client_id,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def transition(
        self,
        status: JobStatus,
        *,
        now: datetime | None = None,
        remote_prompt_id: str | None = None,
        queue_number: int | None = None,
        status_detail: str | None = None,
        error: str | None = None,
        outputs: tuple[OutputMetadata, ...] | None = None,
        artifacts: tuple[ComfyArtifact, ...] | None = None,
        artifact_status: ArtifactStatus | None = None,
        artifact_error: str | None = None,
    ) -> ComfyJob:
        if status == self.status:
            return self.update_observation(
                now=now,
                queue_number=queue_number,
                status_detail=status_detail,
                outputs=outputs,
                artifacts=artifacts,
                artifact_status=artifact_status,
                artifact_error=artifact_error,
            )
        if status not in _TRANSITIONS[self.status]:
            raise JobStateError(f"Invalid ComfyUI job transition: {self.status} -> {status}.")
        timestamp = now or datetime.now(UTC)
        effective_prompt_id = remote_prompt_id or self.remote_prompt_id
        started_at = self.started_at
        if status == JobStatus.RUNNING and started_at is None:
            started_at = timestamp
        finished_at = timestamp if status.terminal else None
        candidate = self.model_copy(
            update={
                "status": status,
                "remote_prompt_id": effective_prompt_id,
                "queue_number": queue_number,
                "updated_at": timestamp,
                "started_at": started_at,
                "finished_at": finished_at,
                "status_detail": status_detail,
                "error": error,
                "outputs": outputs if outputs is not None else self.outputs,
                "artifacts": artifacts if artifacts is not None else self.artifacts,
                "artifact_status": (
                    artifact_status if artifact_status is not None else self.artifact_status
                ),
                "artifact_error": (
                    None
                    if artifact_status == ArtifactStatus.READY
                    else (artifact_error if artifact_error is not None else self.artifact_error)
                ),
            },
            deep=True,
        )
        return ComfyJob.model_validate(candidate.model_dump())

    def update_observation(
        self,
        *,
        now: datetime | None = None,
        queue_number: int | None = None,
        status_detail: str | None = None,
        outputs: tuple[OutputMetadata, ...] | None = None,
        artifacts: tuple[ComfyArtifact, ...] | None = None,
        artifact_status: ArtifactStatus | None = None,
        artifact_error: str | None = None,
    ) -> ComfyJob:
        candidate = self.model_copy(
            update={
                "queue_number": queue_number,
                "updated_at": now or datetime.now(UTC),
                "status_detail": status_detail,
                "outputs": outputs if outputs is not None else self.outputs,
                "artifacts": artifacts if artifacts is not None else self.artifacts,
                "artifact_status": (
                    artifact_status if artifact_status is not None else self.artifact_status
                ),
                "artifact_error": (
                    None
                    if artifact_status == ArtifactStatus.READY
                    else (artifact_error if artifact_error is not None else self.artifact_error)
                ),
            },
            deep=True,
        )
        return ComfyJob.model_validate(candidate.model_dump())


class JobRepository(Protocol):
    """Persistence boundary; SQLite can replace the in-memory implementation unchanged."""

    def create(self, job: ComfyJob) -> ComfyJob: ...

    def get(self, job_id: str) -> ComfyJob: ...

    def save(self, job: ComfyJob, *, expected_version: int) -> ComfyJob: ...

    def list(self, *, profile_id: str | None = None) -> tuple[ComfyJob, ...]: ...


class InMemoryJobRepository:
    """Thread-safe reference repository with optimistic concurrency and snapshot restore."""

    def __init__(self) -> None:
        self._jobs: dict[str, ComfyJob] = {}
        self._lock = threading.RLock()

    def create(self, job: ComfyJob) -> ComfyJob:
        validated = ComfyJob.model_validate(job.model_dump())
        with self._lock:
            if validated.id in self._jobs:
                raise JobConflictError(f"ComfyUI job {validated.id!r} already exists.")
            self._jobs[validated.id] = validated.model_copy(deep=True)
            return validated.model_copy(deep=True)

    def get(self, job_id: str) -> ComfyJob:
        with self._lock:
            try:
                return self._jobs[job_id].model_copy(deep=True)
            except KeyError as exc:
                raise JobNotFoundError(f"ComfyUI job {job_id!r} does not exist.") from exc

    def save(self, job: ComfyJob, *, expected_version: int) -> ComfyJob:
        validated = ComfyJob.model_validate(job.model_dump())
        with self._lock:
            current = self._jobs.get(validated.id)
            if current is None:
                raise JobNotFoundError(f"ComfyUI job {validated.id!r} does not exist.")
            if current.version != expected_version or validated.version != expected_version:
                raise JobConflictError(
                    f"ComfyUI job {validated.id!r} changed during this operation."
                )
            saved = validated.model_copy(update={"version": expected_version + 1}, deep=True)
            saved = ComfyJob.model_validate(saved.model_dump())
            self._jobs[saved.id] = saved
            return saved.model_copy(deep=True)

    def list(self, *, profile_id: str | None = None) -> tuple[ComfyJob, ...]:
        with self._lock:
            jobs = (
                job
                for job in self._jobs.values()
                if profile_id is None or job.profile_id == profile_id
            )
            return tuple(
                job.model_copy(deep=True)
                for job in sorted(jobs, key=lambda item: (item.created_at, item.id))
            )

    def export_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                job.model_dump(mode="json")
                for job in sorted(self._jobs.values(), key=lambda item: item.id)
            ]

    @classmethod
    def restore_snapshot(cls, snapshot: list[dict[str, Any]]) -> InMemoryJobRepository:
        repository = cls()
        for raw in snapshot:
            repository.create(ComfyJob.model_validate(raw))
        return repository
