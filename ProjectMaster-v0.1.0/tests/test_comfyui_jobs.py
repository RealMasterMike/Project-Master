from datetime import UTC, datetime

import pytest

from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyJob,
    InMemoryJobRepository,
    JobConflictError,
    JobStateError,
    JobStatus,
)


def new_job() -> ComfyJob:
    return ComfyJob.new(
        job_id="comfy-job-1",
        profile_id="local",
        workflow_revision_id="workflow-1",
        client_id="project-master-1",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_job_state_machine_and_optimistic_repository() -> None:
    repository = InMemoryJobRepository()
    original = repository.create(new_job())
    queued = original.transition(
        JobStatus.QUEUED,
        remote_prompt_id="remote-1",
        queue_number=7,
    )
    saved = repository.save(queued, expected_version=original.version)

    assert saved.status == JobStatus.QUEUED
    assert saved.version == 2
    assert repository.get(saved.id).queue_number == 7
    with pytest.raises(JobConflictError):
        repository.save(queued, expected_version=original.version)
    with pytest.raises(JobStateError):
        saved.transition(JobStatus.SUCCEEDED).transition(JobStatus.RUNNING)


def test_repository_snapshot_round_trip_is_detached() -> None:
    repository = InMemoryJobRepository()
    repository.create(new_job())
    snapshot = repository.export_snapshot()
    restored = InMemoryJobRepository.restore_snapshot(snapshot)
    snapshot[0]["status_detail"] = "mutated outside repository"

    assert restored.get("comfy-job-1").status_detail is None
    assert restored.export_snapshot()[0]["schema_version"] == 1


def test_legacy_job_payload_defaults_to_pending_artifact_import() -> None:
    legacy = new_job().model_dump(
        mode="json",
        exclude={"artifacts", "artifact_status", "artifact_error"},
    )

    restored = ComfyJob.model_validate(legacy)

    assert restored.artifacts == ()
    assert restored.artifact_status == ArtifactStatus.PENDING
    assert restored.artifact_error is None
