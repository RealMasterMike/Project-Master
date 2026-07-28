from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_master.agent import ProjectMasterAgent
from project_master.api import create_app
from project_master.config import MasterConfig
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.dreams import (
    DreamBackgroundConfig,
    DreamBackgroundExecutor,
    DreamExecutionStatus,
    DreamRecipe,
    DreamRecipeKind,
    DreamSchedule,
    DreamService,
    DreamSource,
    DreamStore,
    ResourceSnapshot,
    SourceKind,
)
from project_master.knowledge import KnowledgeStore
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ProjectSpec
from project_master.orchestration.resource import ResourceGovernor
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime, _scheduled_dream_sources
from project_master.team.models import (
    CatalogModel,
    CouncilResult,
    CouncilRun,
    CouncilStatus,
    ModelDetails,
    ProviderFailure,
    TeamMember,
    TeamPlan,
    TeamRole,
)
from project_master.tools.builtin import build_registry

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class ApiProvider:
    model = "test-model"

    def health(self) -> dict[str, object]:
        return {
            "ok": True,
            "models": [self.model],
            "configured_model": self.model,
        }

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, object]] | None = None,
    ) -> Message:
        return Message(role="assistant", content="Foreground response")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, object]] | None = None,
        cancellation=None,
    ) -> Iterator[Message]:
        yield Message(role="assistant", content="Foreground response")


class ControlledDreamRunner:
    def __init__(self, *, blocked: bool = False) -> None:
        self.calls = 0
        self.preferred_leads: list[str | None] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def run(self, plan, models, *, preferred_lead=None, cancellation=None) -> CouncilRun:
        self.calls += 1
        self.preferred_leads.append(preferred_lead)
        self.entered.set()
        while not self.release.wait(0.005):
            if cancellation is not None and cancellation.cancelled:
                return _council(plan.request.run_id, CouncilStatus.CANCELLED)
        if cancellation is not None and cancellation.cancelled:
            return _council(plan.request.run_id, CouncilStatus.CANCELLED)
        return _council(plan.request.run_id, CouncilStatus.COMPLETE)


def _council(run_id: str, status: CouncilStatus) -> CouncilRun:
    lead = TeamMember(
        member_id="digest:test-model",
        role=TeamRole.LEAD,
        model_tag="test-model",
        aliases=("test-model",),
        capabilities=frozenset({"completion", "tools"}),
        size_bytes=1,
    )
    failure = (
        ProviderFailure(code="cancelled", message="Cancelled cooperatively.")
        if status is CouncilStatus.CANCELLED
        else None
    )
    return CouncilRun(
        events=(),
        result=CouncilResult(
            run_id=run_id,
            status=status,
            final="Proposal pending review." if status is CouncilStatus.COMPLETE else "",
            final_truncated=False,
            plan=TeamPlan(lead=lead, workers=()),
            workers=(),
            failure=failure,
        ),
    )


def _model() -> CatalogModel:
    return CatalogModel(
        physical_id="digest:test-model",
        tags=("test-model",),
        digest="test-model",
        size_bytes=1,
        capabilities=frozenset({"completion", "tools"}),
        details=ModelDetails(family="test"),
    )


def _resources() -> ResourceSnapshot:
    return ResourceSnapshot(
        idle_seconds=3_600,
        cpu_percent=5,
        available_memory_bytes=16 * 1024**3,
        gpu_free_bytes=8 * 1024**3,
        active_model_jobs=0,
        on_ac_power=True,
    )


def _source(*, allow_dreaming: bool = True) -> DreamSource:
    return DreamSource(
        source_id="manual-note",
        kind=SourceKind.USER_NOTE,
        locator="user-note://manual-note",
        content="A bounded source for a proposal.",
        captured_at_utc=NOW,
        allow_dreaming=allow_dreaming,
    )


def _runtime(path: Path, runner: ControlledDreamRunner) -> MasterRuntime:
    config = MasterConfig(
        model="test-model",
        db_path=path,
        workspace_root=path.parent / "workspace",
    )
    store = SQLiteStore(path)
    orchestration = OrchestrationStore(store)
    profiler = StyleProfiler(store)
    provider = ApiProvider()
    agent = ProjectMasterAgent(
        provider=provider,  # type: ignore[arg-type]
        tools=build_registry(store, config.workspace_root),
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
    )
    dream_store = DreamStore(store)
    dream = DreamService(dream_store, runner, clock=lambda: NOW)  # type: ignore[arg-type]
    governor = ResourceGovernor(store)
    runtime = MasterRuntime(
        config,
        store,
        profiler,
        provider,  # type: ignore[arg-type]
        agent,
        orchestration=orchestration,
        dream=dream,
        dream_store=dream_store,
        resource_governor=governor,
    )
    runtime.dream_background = DreamBackgroundExecutor(
        dream,
        governor,
        source_provider=lambda _schedule: (),
        model_provider=lambda: (_model(),),
        resource_provider=_resources,
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
            preferred_lead="test-model",
        ),
        clock=lambda: NOW,
    )
    return runtime


def test_api_lifespan_truthfully_starts_and_stops_dream_background(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path / "master.db", ControlledDreamRunner())
    assert runtime.dream_background is not None
    assert runtime.dream_background.running is False

    with TestClient(create_app(runtime)) as client:
        overview = client.get("/api/v1/dreams")
        assert overview.status_code == 200
        assert overview.json()["scheduled_execution_enabled"] is True
        assert overview.json()["background_configured"] is True
        assert runtime.dream_background.running is True

    assert runtime.dream_background.running is False


def test_schedule_crud_and_nonblocking_manual_status_cancel_events(
    tmp_path: Path,
) -> None:
    runner = ControlledDreamRunner(blocked=True)
    runtime = _runtime(tmp_path / "master.db", runner)
    assert runtime.dream is not None
    runtime.dream.save_recipe(
        DreamRecipe(
            recipe_id="api-scheduled",
            name="API scheduled",
            kind=DreamRecipeKind.CUSTOM,
            objective="Exercise schedule lifecycle APIs with an explicit source scope.",
            source_scopes=("memory:dream-notes",),
        ),
        expected_version=0,
    )
    runtime.store.remember(
        "dream-notes",
        "api-schedule-source",
        {
            "content": "A consented source for schedule lifecycle testing.",
            "allow_dreaming": True,
        },
    )

    with TestClient(create_app(runtime)) as client:
        created = client.post(
            "/api/v1/dreams/schedules",
            json={
                "schedule_id": "nightly",
                "recipe_id": "api-scheduled",
                "timezone": "UTC",
                "local_time": "23:59:59",
                "enabled": False,
                "expected_version": 0,
                "resource_rules": {
                    "min_idle_seconds": 600,
                    "min_gpu_free_bytes": 4 * 1024**3,
                },
                "quiet_window": {
                    "timezone": "UTC",
                    "start_local": "22:00:00",
                    "end_local": "06:00:00",
                    "weekdays": [0, 1, 2, 3, 4],
                },
            },
        )
        assert created.status_code == 201
        assert created.json()["version"] == 1
        assert created.json()["quiet_window"]["start_local"] == "22:00:00"
        assert client.get("/api/v1/dreams/schedules/nightly").status_code == 200
        assert len(client.get("/api/v1/dreams/schedules").json()["schedules"]) == 1

        enabled = client.post(
            "/api/v1/dreams/schedules/nightly/enabled",
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        assert enabled.json()["version"] == 2

        queued = client.post(
            "/api/v1/dreams/runs/manual",
            json={
                "recipe_id": "idea-garden",
                "request_id": "api-nonblocking",
                "preferred_lead": "test-model",
                "sources": [
                    {
                        "source_id": "manual-note",
                        "kind": "user_note",
                        "locator": "user-note://manual-note",
                        "content": "A bounded source for a proposal.",
                    }
                ],
            },
        )
        assert queued.status_code == 202
        assert queued.json()["accepted"] is True
        assert queued.json()["background"] is True
        assert queued.json()["run"]["status"] == "claimed"
        assert queued.json()["run"]["preferred_lead"] == "test-model"
        run_id = queued.json()["run"]["run_id"]
        assert runner.entered.wait(1)
        assert runner.preferred_leads == ["test-model"]
        assert client.get(f"/api/v1/dreams/runs/{run_id}").json()["status"] == "running"

        cancelled = client.post(f"/api/v1/dreams/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert runtime.dream_background is not None
        assert runtime.dream_background.wait_for_idle(2)
        assert client.get(f"/api/v1/dreams/runs/{run_id}").json()["status"] == "cancelled"
        events = client.get(
            "/api/v1/dreams/events",
            params={"run_id": run_id},
        ).json()["events"]
        assert {"cancel_requested", "run_cancelled"} <= {
            event["event_type"] for event in events
        }
        assert client.get("/api/v1/dreams/inbox").json()["items"] == []

        deleted = client.delete("/api/v1/dreams/schedules/nightly")
        assert deleted.status_code == 204
        assert client.get("/api/v1/dreams/schedules").json()["schedules"] == []


def test_project_consent_and_enabled_schedule_readiness_contract(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path / "master.db", ControlledDreamRunner())
    assert runtime.orchestration is not None
    assert runtime.dream is not None

    active_project = runtime.orchestration.create_project(
        ProjectSpec(
            name="Active source",
            root_path=str(tmp_path),
            metadata={"owner": "mike"},
        )
    )
    archived_project = runtime.orchestration.create_project(
        ProjectSpec(
            name="Archived source",
            root_path=str(tmp_path),
            metadata={"allow_dreaming": True},
        )
    )
    knowledge = KnowledgeStore(runtime.store)
    for project_id, relative_path, content in (
        (active_project, "active.md", "Active indexed evidence."),
        (archived_project, "archived.md", "Archived indexed evidence."),
    ):
        knowledge.index_document(
            project_id=project_id,
            root_path=tmp_path,
            relative_path=relative_path,
            content=content,
            size_bytes=len(content),
        )
    with runtime.store.connection() as conn:
        conn.execute(
            "UPDATE projects SET status = 'archived' WHERE id = ?",
            (archived_project,),
        )

    for recipe_id, project_id in (
        ("active-project-schedule", active_project),
        ("archived-project-schedule", archived_project),
    ):
        runtime.dream.save_recipe(
            DreamRecipe(
                recipe_id=recipe_id,
                name=recipe_id,
                kind=DreamRecipeKind.CUSTOM,
                objective="Use a single explicitly scoped project source.",
                source_scopes=(f"project:{project_id}",),
            ),
            expected_version=0,
        )
    runtime.store.remember(
        "dream-notes",
        "eligible-memory",
        {
            "content": "A consented memory for scheduled dreaming.",
            "allow_dreaming": True,
        },
    )
    runtime.store.remember(
        "private-dream-notes",
        "secret-memory",
        {
            "content": "This consented memory is still too sensitive.",
            "allow_dreaming": True,
            "sensitivity": "secret",
        },
    )
    for recipe_id, scopes in (
        ("memory-project-mixed", (f"project:{archived_project}", "memory:dream-notes")),
        ("memory-only", ("memory:dream-notes",)),
        ("memory-missing", ("memory:empty-namespace",)),
        ("wildcard-sources", ("*",)),
        ("secret-memory", ("memory:private-dream-notes",)),
        ("unsupported-scheduled-source", ("artifact:*",)),
    ):
        runtime.dream.save_recipe(
            DreamRecipe(
                recipe_id=recipe_id,
                name=recipe_id,
                kind=DreamRecipeKind.CUSTOM,
                objective="Exercise durable scheduled source readiness.",
                source_scopes=scopes,
            ),
            expected_version=0,
        )

    headers = {"X-Project-Master-Token": "secret"}

    def schedule_payload(
        schedule_id: str,
        recipe_id: str,
        *,
        enabled: bool,
    ) -> dict[str, object]:
        return {
            "schedule_id": schedule_id,
            "recipe_id": recipe_id,
            "timezone": "UTC",
            "local_time": "23:59:59",
            "enabled": enabled,
            "expected_version": 0,
        }

    with TestClient(create_app(runtime, session_token="secret")) as client:
        unauthenticated = client.post(
            f"/api/v1/projects/{active_project}/dreaming",
            json={"enabled": True},
        )
        assert unauthenticated.status_code == 401

        not_consented = client.post(
            "/api/v1/dreams/schedules",
            headers=headers,
            json=schedule_payload(
                "active-nightly",
                "active-project-schedule",
                enabled=True,
            ),
        )
        assert not_consented.status_code == 409
        assert "no consented source" in not_consented.json()["detail"]
        assert (
            client.get(
                "/api/v1/dreams/schedules",
                headers=headers,
            ).json()["schedules"]
            == []
        )

        enabled = client.post(
            f"/api/v1/projects/{active_project}/dreaming",
            headers=headers,
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["metadata"] == {
            "owner": "mike",
            "allow_dreaming": True,
        }
        disabled = client.post(
            f"/api/v1/projects/{active_project}/dreaming",
            headers=headers,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["metadata"] == {
            "owner": "mike",
            "allow_dreaming": False,
        }
        assert (
            client.post(
                f"/api/v1/projects/{active_project}/dreaming",
                headers=headers,
                json={"enabled": True},
            ).status_code
            == 200
        )

        ready = client.post(
            "/api/v1/dreams/schedules",
            headers=headers,
            json=schedule_payload(
                "active-nightly",
                "active-project-schedule",
                enabled=True,
            ),
        )
        assert ready.status_code == 201
        assert ready.json()["enabled"] is True

        archived = client.post(
            "/api/v1/dreams/schedules",
            headers=headers,
            json=schedule_payload(
                "archived-nightly",
                "archived-project-schedule",
                enabled=True,
            ),
        )
        assert archived.status_code == 409
        assert "no consented source" in archived.json()["detail"]

        for schedule_id, recipe_id in (
            ("mixed-nightly", "memory-project-mixed"),
            ("memory-nightly", "memory-only"),
            ("wildcard-nightly", "wildcard-sources"),
        ):
            response = client.post(
                "/api/v1/dreams/schedules",
                headers=headers,
                json=schedule_payload(
                    schedule_id,
                    recipe_id,
                    enabled=True,
                ),
            )
            assert response.status_code == 201

        for schedule_id, recipe_id in (
            ("missing-memory-nightly", "memory-missing"),
            ("secret-memory-nightly", "secret-memory"),
        ):
            response = client.post(
                "/api/v1/dreams/schedules",
                headers=headers,
                json=schedule_payload(
                    schedule_id,
                    recipe_id,
                    enabled=True,
                ),
            )
            assert response.status_code == 409
            assert "no consented source" in response.json()["detail"]

        unsupported = client.post(
            "/api/v1/dreams/schedules",
            headers=headers,
            json=schedule_payload(
                "unsupported-nightly",
                "unsupported-scheduled-source",
                enabled=True,
            ),
        )
        assert unsupported.status_code == 422
        assert "artifact:*" in unsupported.json()["detail"]

        disabled_unscoped = client.post(
            "/api/v1/dreams/schedules",
            headers=headers,
            json=schedule_payload(
                "unscoped-nightly",
                "idea-garden",
                enabled=False,
            ),
        )
        assert disabled_unscoped.status_code == 201
        rejected_enable = client.post(
            "/api/v1/dreams/schedules/unscoped-nightly/enabled",
            headers=headers,
            json={"enabled": True},
        )
        assert rejected_enable.status_code == 422
        assert "explicit source_scopes" in rejected_enable.json()["detail"]
        unchanged = client.get(
            "/api/v1/dreams/schedules/unscoped-nightly",
            headers=headers,
        ).json()
        assert unchanged["enabled"] is False
        assert unchanged["version"] == 1

        missing = client.post(
            "/api/v1/projects/project_missing/dreaming",
            headers=headers,
            json={"enabled": True},
        )
        assert missing.status_code == 404


def test_lifespan_restart_resumes_a_claimed_run_without_duplicate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first = _runtime(path, ControlledDreamRunner())
    assert first.dream is not None
    queued = first.dream.queue_manual(
        recipe_id="idea-garden",
        request_id="resume-on-startup",
        sources=[_source()],
    )

    runner = ControlledDreamRunner()
    restarted = _runtime(path, runner)
    with TestClient(create_app(restarted)):
        assert restarted.dream_background is not None
        assert runner.entered.wait(1)
        assert restarted.dream_background.wait_for_idle(2)

    assert restarted.dream is not None
    finished = restarted.dream.get_run(queued.run.run_id)
    assert finished.status is DreamExecutionStatus.COMPLETE
    assert runner.calls == 1
    assert len(restarted.dream.list_runs()) == 1


def test_scheduled_source_provider_requires_scope_and_durable_opt_in(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    runtime = _runtime(path, ControlledDreamRunner())
    assert runtime.orchestration is not None
    assert runtime.dream is not None
    assert runtime.dream_store is not None

    allowed_project = runtime.orchestration.create_project(
        ProjectSpec(
            name="Allowed",
            root_path=str(tmp_path),
            metadata={"allow_dreaming": True},
        )
    )
    blocked_project = runtime.orchestration.create_project(
        ProjectSpec(
            name="Blocked",
            root_path=str(tmp_path),
            metadata={"allow_dreaming": False},
        )
    )
    archived_project = runtime.orchestration.create_project(
        ProjectSpec(
            name="Archived",
            root_path=str(tmp_path),
            metadata={"allow_dreaming": True},
        )
    )
    knowledge = KnowledgeStore(runtime.store)
    knowledge.index_document(
        project_id=allowed_project,
        root_path=tmp_path,
        relative_path="allowed.md",
        content="Allowed project evidence.",
        size_bytes=25,
    )
    knowledge.index_document(
        project_id=blocked_project,
        root_path=tmp_path,
        relative_path="blocked.md",
        content="Blocked project evidence.",
        size_bytes=25,
    )
    knowledge.index_document(
        project_id=archived_project,
        root_path=tmp_path,
        relative_path="archived.md",
        content="Archived project evidence.",
        size_bytes=26,
    )
    with runtime.store.connection() as conn:
        conn.execute(
            "UPDATE projects SET status = 'archived' WHERE id = ?",
            (archived_project,),
        )
    runtime.store.remember(
        "dream-notes",
        "allowed-memory",
        {"content": "Allowed memory evidence.", "allow_dreaming": True},
    )
    runtime.store.remember(
        "dream-notes",
        "blocked-memory",
        {"content": "Blocked memory evidence.", "allow_dreaming": False},
    )
    runtime.dream.save_recipe(
        DreamRecipe(
            recipe_id="consent-scoped",
            name="Consent scoped",
            kind=DreamRecipeKind.CUSTOM,
            objective="Use only explicitly consented evidence.",
            source_scopes=(
                f"project:{allowed_project}",
                f"project:{blocked_project}",
                f"project:{archived_project}",
                "memory:dream-notes",
            ),
        ),
        expected_version=0,
    )
    stored_schedule = runtime.dream.save_schedule(
        DreamSchedule(
            schedule_id="consent-nightly",
            recipe_id="consent-scoped",
            timezone="UTC",
            local_time=time(2, 0),
            created_at_utc=NOW,
            enabled=False,
        ),
        expected_version=0,
    )

    sources = _scheduled_dream_sources(
        stored_schedule,
        dream_store=runtime.dream_store,
        store=runtime.store,
        orchestration=runtime.orchestration,
    )

    assert {source.content for source in sources} == {
        "Allowed project evidence.",
        "Allowed memory evidence.",
    }
    assert all(source.allow_dreaming for source in sources)
    with pytest.raises(ValueError, match="outside recipe source_scopes"):
        runtime.dream.queue_manual(
            recipe_id="consent-scoped",
            request_id="wrong-scope",
            sources=[_source()],
        )

    opted_out = runtime.dream.queue_manual(
        recipe_id="consent-scoped",
        request_id="opted-out",
        sources=[
            DreamSource(
                source_id="allowed-but-opted-out",
                kind=SourceKind.PROJECT,
                locator=f"project://{allowed_project}/note.md",
                content="Do not use this.",
                captured_at_utc=NOW,
                allow_dreaming=False,
            )
        ],
    )
    snapshot = runtime.dream_store.snapshot_for_run(opted_out.run.run_id)
    assert snapshot.entries == ()
    assert snapshot.exclusions[0].reason == "source opted out of dreaming"


def test_foreground_chat_preempts_a_running_dream(tmp_path: Path) -> None:
    runner = ControlledDreamRunner(blocked=True)
    runtime = _runtime(tmp_path / "master.db", runner)

    with TestClient(create_app(runtime)) as client:
        queued = client.post(
            "/api/v1/dreams/runs/manual",
            json={
                "recipe_id": "risk-scan",
                "request_id": "preempt-for-chat",
                "sources": [
                    {
                        "source_id": "manual-note",
                        "locator": "user-note://manual-note",
                        "content": "A bounded source.",
                    }
                ],
            },
        )
        run_id = queued.json()["run"]["run_id"]
        assert runner.entered.wait(1)

        chatted = client.post("/api/v1/chat", json={"message": "Need an answer now."})

        assert chatted.status_code == 200
        assert chatted.json()["message"] == "Foreground response"
        assert runtime.dream_background is not None
        assert runtime.dream_background.wait_for_idle(2)
        assert client.get(f"/api/v1/dreams/runs/{run_id}").json()["status"] == "cancelled"
        assert runtime.interactive_model_busy is False
