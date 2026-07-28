from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import (
    ApprovalSpec,
    ArtifactSpec,
    JobSpec,
    ProjectSpec,
    RoleSpec,
    RunSpec,
    TaskSpec,
)
from project_master.orchestration.resource import ResourceGovernor
from project_master.orchestration.store import OrchestrationStore


def build_store(tmp_path: Path) -> tuple[OrchestrationStore, SQLiteStore]:
    sqlite = SQLiteStore(tmp_path / "master.db")
    return OrchestrationStore(sqlite), sqlite


def test_project_run_task_handoff_artifact_and_verification_round_trip(tmp_path: Path) -> None:
    store, _sqlite = build_store(tmp_path)
    project_spec = ProjectSpec(name="Creator project", root_path="/tmp/project")
    project_id = store.get_or_create_project(project_spec)
    assert store.get_or_create_project(project_spec) == project_id
    run_id = store.create_run(
        RunSpec(project_id=project_id, kind="team", objective="Produce a verified draft")
    )
    builder_id = store.add_role(
        RoleSpec(
            run_id=run_id,
            role="Builder",
            model="builder-model",
            model_digest="abc123",
            assignment="Create the draft",
            permissions=["workspace_read"],
        )
    )
    verifier_id = store.add_role(
        RoleSpec(
            run_id=run_id,
            role="Verifier",
            model="verifier-model",
            assignment="Check the draft",
        )
    )
    task_id = store.create_task(
        TaskSpec(
            run_id=run_id,
            title="Draft",
            objective="Create one draft",
            role_instance_id=builder_id,
            completion_criteria=["Artifact exists"],
        )
    )

    store.set_run_status(run_id, "running")
    store.set_task_status(task_id, "ready")
    store.set_task_status(task_id, "running")
    packet_id = store.save_context_packet(
        run_id,
        "Create one draft",
        {"instructions": ["Be concise"]},
        [{"kind": "user_instruction", "id": "message-1"}],
        task_id=task_id,
        role_instance_id=builder_id,
    )
    handoff_id = store.add_handoff(
        run_id,
        {
            "objective": "Verify the draft",
            "context_packet_id": packet_id,
            "artifacts": ["draft.md"],
        },
        task_id=task_id,
        from_role_instance_id=builder_id,
        to_role_instance_id=verifier_id,
    )
    artifact_id = store.add_artifact(
        ArtifactSpec(
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            producer_role_instance_id=builder_id,
            kind="markdown",
            name="draft.md",
            sha256="0" * 64,
            size_bytes=42,
            provenance={"context_packet_id": packet_id},
        )
    )
    store.set_task_status(task_id, "complete", {"artifact_id": artifact_id})
    verification_id = store.add_verification(
        run_id,
        "pass",
        ["Artifact exists"],
        [],
        [{"artifact_id": artifact_id}],
        task_id=task_id,
        verifier_role_instance_id=verifier_id,
    )
    store.set_run_status(run_id, "verifying")
    store.set_run_status(run_id, "complete")

    run = store.get_run(run_id)
    assert run is not None
    assert run["status"] == "complete"
    assert store.list_roles(run_id)[0]["permissions"] == ["workspace_read"]
    assert store.list_tasks(run_id)[0]["result"]["artifact_id"] == artifact_id
    assert store.list_artifacts(project_id, run_id)[0]["provenance"] == {
        "context_packet_id": packet_id
    }
    events = store.list_events(run_id)
    assert any(event["payload"].get("handoff_id") == handoff_id for event in events)
    assert any(
        event["payload"].get("verification_id") == verification_id for event in events
    )


def test_invalid_state_transition_is_rejected(tmp_path: Path) -> None:
    store, _sqlite = build_store(tmp_path)
    project_id = store.create_project(ProjectSpec(name="State test"))
    run_id = store.create_run(RunSpec(project_id=project_id, kind="team", objective="Test"))

    with pytest.raises(ValueError, match="Invalid run transition"):
        store.set_run_status(run_id, "complete")


def test_project_dreaming_consent_preserves_unrelated_metadata(tmp_path: Path) -> None:
    store, _sqlite = build_store(tmp_path)
    project_id = store.create_project(
        ProjectSpec(
            name="Dream source",
            metadata={
                "owner": "mike",
                "nested": {"keep": True},
                "allow_dreaming": False,
            },
        )
    )

    enabled = store.set_project_dreaming(project_id, True)
    disabled = store.set_project_dreaming(project_id, False)

    assert enabled["metadata"] == {
        "owner": "mike",
        "nested": {"keep": True},
        "allow_dreaming": True,
    }
    assert disabled["metadata"] == {
        "owner": "mike",
        "nested": {"keep": True},
        "allow_dreaming": False,
    }
    assert store.get_project(project_id) == disabled
    with pytest.raises(KeyError, match="Unknown project"):
        store.set_project_dreaming("project_missing", True)


def test_approval_must_be_resolved_once(tmp_path: Path) -> None:
    store, _sqlite = build_store(tmp_path)
    project_id = store.create_project(ProjectSpec(name="Approval test"))
    run_id = store.create_run(RunSpec(project_id=project_id, kind="tool", objective="Write"))
    approval_id = store.request_approval(
        ApprovalSpec(
            run_id=run_id,
            action_kind="workspace_write",
            target="notes.md",
            request={"path": "notes.md", "content_hash": "abc"},
            risk="low",
            reversible=True,
            rollback_plan="Restore checkpoint",
        )
    )

    assert store.list_approvals()[0]["reversible"] is True
    store.resolve_approval(approval_id, "approved", "Approved once")
    assert store.list_approvals(status="approved")[0]["decision_note"] == "Approved once"
    with pytest.raises(ValueError, match="already approved"):
        store.resolve_approval(approval_id, "rejected")


def test_job_idempotency_transitions_and_resource_lease(tmp_path: Path) -> None:
    store, sqlite = build_store(tmp_path)
    job_id = store.enqueue_job(
        JobSpec(kind="dream", payload={"recipe": "idea_garden"}, idempotency_key="night-1")
    )
    duplicate = store.enqueue_job(
        JobSpec(kind="dream", payload={"recipe": "different"}, idempotency_key="night-1")
    )
    assert duplicate == job_id

    store.set_job_state(job_id, "queued")
    store.set_job_state(job_id, "running")
    governor = ResourceGovernor(sqlite)
    assert governor.acquire("gpu:heavy", "dream-worker", job_id=job_id, ttl_seconds=60)
    assert not governor.acquire("gpu:heavy", "voice-worker", ttl_seconds=60)
    assert governor.renew("gpu:heavy", "dream-worker", ttl_seconds=120)
    assert governor.status("gpu:heavy")["job_id"] == job_id
    assert governor.release("gpu:heavy", "dream-worker")

    store.set_job_state(job_id, "complete", result={"items": 3})
    assert store.get_job(job_id)["result"] == {"items": 3}


def test_resource_lease_has_exactly_one_winner_under_concurrent_acquire(
    tmp_path: Path,
) -> None:
    _store, sqlite = build_store(tmp_path)
    governor = ResourceGovernor(sqlite)

    for attempt in range(8):
        resource_key = f"gpu:concurrent:{attempt}"
        barrier = Barrier(3)

        def acquire(
            owner: str,
            current_barrier: Barrier = barrier,
            current_key: str = resource_key,
        ) -> tuple[str, bool]:
            current_barrier.wait()
            return owner, governor.acquire(current_key, owner, ttl_seconds=30)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(acquire, "foreground"),
                pool.submit(acquire, "dream-background"),
            ]
            barrier.wait()
            outcomes = [future.result(timeout=2) for future in futures]

        winners = [owner for owner, acquired in outcomes if acquired]
        assert len(winners) == 1
        assert governor.status(resource_key)["owner"] == winners[0]
        assert governor.release(resource_key, winners[0])
