from datetime import UTC, datetime

import pytest

from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyInputImageProvenance,
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


def image_provenance() -> ComfyInputImageProvenance:
    return ComfyInputImageProvenance(
        binding_id="source_image",
        source_asset_id=f"media-asset-{'a' * 32}",
        source_sha256="b" * 64,
        source_name="source.png",
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
    repository.create(
        ComfyJob.new(
            job_id="comfy-job-1",
            profile_id="local",
            workflow_revision_id="workflow-1",
            client_id="project-master-1",
            project_id="project-creator-1",
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    snapshot = repository.export_snapshot()
    restored = InMemoryJobRepository.restore_snapshot(snapshot)
    snapshot[0]["status_detail"] = "mutated outside repository"

    assert restored.get("comfy-job-1").status_detail is None
    assert restored.get("comfy-job-1").project_id == "project-creator-1"
    assert restored.export_snapshot()[0]["schema_version"] == 1


def test_legacy_job_payload_defaults_optional_fields() -> None:
    legacy = new_job().model_dump(
        mode="json",
        exclude={
            "project_id",
            "input_images",
            "artifacts",
            "artifact_status",
            "artifact_error",
        },
    )

    restored = ComfyJob.model_validate(legacy)

    assert restored.project_id is None
    assert restored.input_images == ()
    assert restored.artifacts == ()
    assert restored.artifact_status == ArtifactStatus.PENDING
    assert restored.artifact_error is None


@pytest.mark.parametrize("project_id", ["", "creator project", "/creator"])
def test_project_association_rejects_invalid_identifiers(project_id: str) -> None:
    with pytest.raises(ValueError):
        ComfyJob.new(
            job_id="comfy-job-1",
            profile_id="local",
            workflow_revision_id="workflow-1",
            client_id="project-master-1",
            project_id=project_id,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_image_input_provenance_requires_project_and_unique_bindings() -> None:
    provenance = image_provenance()
    with pytest.raises(ValueError, match="project association"):
        ComfyJob.new(
            job_id="comfy-job-1",
            profile_id="local",
            workflow_revision_id="workflow-1",
            client_id="project-master-1",
            input_images=(provenance,),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="bindings must be unique"):
        ComfyJob.new(
            job_id="comfy-job-1",
            profile_id="local",
            workflow_revision_id="workflow-1",
            client_id="project-master-1",
            project_id="project-creator-1",
            input_images=(provenance, provenance),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
