from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from project_master.integrations.comfyui.artifacts import (
    ComfyArtifactProvenance,
    FilesystemComfyArtifactStore,
)
from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyInputImageProvenance,
    ComfyJob,
    JobConflictError,
    JobStatus,
)
from project_master.integrations.comfyui.persistence import SQLiteComfyStore
from project_master.integrations.comfyui.profiles import (
    ComfyAuth,
    ComfyUIProfile,
    SecretRef,
)
from project_master.integrations.comfyui.transport import (
    DownloadedOutput,
    OutputMetadata,
    OutputRef,
)
from project_master.integrations.comfyui.workflow import WorkflowRevision
from project_master.memory.store import SQLiteStore


def test_profiles_and_workflow_decisions_survive_restart(tmp_path) -> None:
    database = SQLiteStore(tmp_path / "master.db")
    store = SQLiteComfyStore(database)
    profile = ComfyUIProfile(
        id="studio",
        name="Local Studio",
        auth=ComfyAuth(secret_ref=SecretRef(key="COMFY_STUDIO_TOKEN")),
    )
    revision = WorkflowRevision.import_json(
        "Smoke workflow",
        {"1": {"class_type": "PreviewImage", "inputs": {"images": "test"}}},
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    store.upsert_profile(profile)
    store.save_workflow(revision)
    store.decide_workflow(revision.id, "approved", "Reviewed locally.")

    restarted = SQLiteComfyStore(SQLiteStore(tmp_path / "master.db"))
    assert restarted.list_profiles() == (profile,)
    restored = restarted.get_workflow(revision.id)
    assert restored.revision == revision
    assert restored.trust_state == "approved"
    assert restored.decision_note == "Reviewed locally."
    with database.connection() as connection:
        payload = str(
            connection.execute(
                "SELECT payload_json FROM comfy_profiles WHERE id = 'studio'"
            ).fetchone()["payload_json"]
        )
    assert "COMFY_STUDIO_TOKEN" in payload
    assert "secret-value-that-must-not-persist" not in payload


def test_comfy_jobs_are_durable_and_optimistically_versioned(tmp_path) -> None:
    store = SQLiteComfyStore(SQLiteStore(tmp_path / "master.db"))
    input_image = ComfyInputImageProvenance(
        binding_id="source_image",
        source_asset_id=f"media-asset-{'a' * 32}",
        source_sha256="b" * 64,
        source_name="source.png",
    )
    created = store.create(
        ComfyJob.new(
            job_id="comfy-job-one",
            profile_id="studio",
            workflow_revision_id="workflow-one",
            client_id="project-master-one",
            project_id="project-creator-one",
            input_images=(input_image,),
            now=datetime(2026, 7, 27, tzinfo=UTC),
        )
    )
    queued = created.transition(
        JobStatus.QUEUED,
        remote_prompt_id="prompt-one",
        queue_number=3,
        now=datetime(2026, 7, 27, 0, 1, tzinfo=UTC),
    )

    saved = store.save(queued, expected_version=created.version)

    assert saved.version == 2
    restored = SQLiteComfyStore(SQLiteStore(tmp_path / "master.db")).get(saved.id)
    assert restored == saved
    assert restored.project_id == "project-creator-one"
    assert restored.input_images == (input_image,)
    with pytest.raises(JobConflictError):
        store.save(queued, expected_version=created.version)


def test_legacy_comfy_job_without_project_id_loads_from_sqlite(tmp_path) -> None:
    database = SQLiteStore(tmp_path / "master.db")
    store = SQLiteComfyStore(database)
    created = store.create(
        ComfyJob.new(
            job_id="comfy-job-legacy",
            profile_id="studio",
            workflow_revision_id="workflow-one",
            client_id="project-master-legacy",
            now=datetime(2026, 7, 27, tzinfo=UTC),
        )
    )
    with database.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM comfy_jobs WHERE id = ?",
            (created.id,),
        ).fetchone()
        payload = json.loads(str(row["payload_json"]))
        payload.pop("project_id")
        payload.pop("input_images")
        connection.execute(
            "UPDATE comfy_jobs SET payload_json = ? WHERE id = ?",
            (json.dumps(payload), created.id),
        )

    restored = SQLiteComfyStore(SQLiteStore(tmp_path / "master.db")).get(created.id)

    assert restored.project_id is None
    assert restored.input_images == ()


def test_imported_artifact_provenance_survives_sqlite_restart(tmp_path) -> None:
    database_path = tmp_path / "master.db"
    store = SQLiteComfyStore(SQLiteStore(database_path))
    created = store.create(
        ComfyJob.new(
            job_id="comfy-job-artifact",
            profile_id="studio",
            workflow_revision_id="workflow-one",
            client_id="project-master-artifact",
            now=datetime(2026, 7, 27, tzinfo=UTC),
        )
    )
    queued = store.save(
        created.transition(
            JobStatus.QUEUED,
            remote_prompt_id="prompt-artifact",
        ),
        expected_version=created.version,
    )
    output = OutputMetadata(
        node_id="9",
        category="files",
        ref=OutputRef(filename="report.json"),
        media_type="application/json",
    )
    source_url = "http://127.0.0.1:8188/view?filename=report.json&subfolder=&type=output"
    downloaded = DownloadedOutput(
        content=b'{"verified":true}',
        media_type="application/json",
        source_url=source_url,
        fetched_at=datetime(2026, 7, 27, 0, 2, tzinfo=UTC),
    )
    artifact_store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    artifact = artifact_store.store(
        downloaded,
        ComfyArtifactProvenance(
            job_id=queued.id,
            profile_id=queued.profile_id,
            workflow_revision_id=queued.workflow_revision_id,
            workflow_digest="a" * 64,
            remote_prompt_id="prompt-artifact",
            output=output,
            history_sha256="b" * 64,
            history_status="success",
            source_base_url="http://127.0.0.1:8188",
            source_url=source_url,
            fetched_at=downloaded.fetched_at,
        ),
    )
    succeeded = queued.transition(
        JobStatus.SUCCEEDED,
        outputs=(output,),
        artifacts=(artifact,),
        artifact_status=ArtifactStatus.READY,
        now=datetime(2026, 7, 27, 0, 3, tzinfo=UTC),
    )

    saved = store.save(succeeded, expected_version=queued.version)
    restored = SQLiteComfyStore(SQLiteStore(database_path)).get(saved.id)

    assert restored == saved
    assert restored.artifacts == (artifact,)
    assert artifact_store.read(restored.artifacts[0]) == downloaded.content
