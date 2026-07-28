from __future__ import annotations

import threading
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

from project_master.dreams import (
    DreamBackgroundConfig,
    DreamBackgroundExecutor,
    DreamDisposition,
    DreamExecutionStatus,
    DreamInboxError,
    DreamRecipe,
    DreamRecipeKind,
    DreamSchedule,
    DreamService,
    DreamSource,
    DreamStore,
    QuietWindow,
    ResourceRules,
    ResourceSnapshot,
    ScheduleWindow,
    SourceKind,
    WindowOrigin,
)
from project_master.memory.store import SQLiteStore
from project_master.orchestration.resource import (
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)
from project_master.orchestration.store import OrchestrationStore
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

NOW = datetime(2026, 7, 27, 2, 5, tzinfo=UTC)


class ControlledRunner:
    def __init__(self, *, blocked: bool = False) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def run(self, plan, models, *, preferred_lead=None, cancellation=None) -> CouncilRun:
        self.calls += 1
        self.entered.set()
        while not self.release.wait(0.005):
            if cancellation is not None and cancellation.cancelled:
                return _council(plan.request.run_id, CouncilStatus.CANCELLED)
        if cancellation is not None and cancellation.cancelled:
            return _council(plan.request.run_id, CouncilStatus.CANCELLED)
        return _council(plan.request.run_id, CouncilStatus.COMPLETE)


class StoreCancellingRunner:
    def __init__(self) -> None:
        self.store: DreamStore | None = None

    def run(self, plan, models, *, preferred_lead=None, cancellation=None) -> CouncilRun:
        if self.store is None:
            raise RuntimeError("test runner was not attached to a Dream store")
        self.store.request_cancel(plan.request.run_id, requested_at_utc=NOW)
        return _council(plan.request.run_id, CouncilStatus.COMPLETE)


def _council(run_id: str, status: CouncilStatus) -> CouncilRun:
    lead = TeamMember(
        member_id="digest:lead",
        role=TeamRole.LEAD,
        model_tag="lead",
        aliases=("lead",),
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
            final=(
                "A bounded speculative proposal."
                if status is CouncilStatus.COMPLETE
                else ""
            ),
            final_truncated=False,
            plan=TeamPlan(lead=lead, workers=()),
            workers=(),
            failure=failure,
        ),
    )


def _source() -> DreamSource:
    return DreamSource(
        source_id="project-note",
        kind=SourceKind.PROJECT,
        locator="project://notes",
        content="A durable source observation for a proposal-only dream.",
        captured_at_utc=NOW,
    )


def _model() -> CatalogModel:
    return CatalogModel(
        physical_id="digest:lead",
        tags=("lead",),
        digest="lead",
        size_bytes=1,
        capabilities=frozenset({"completion", "tools"}),
        details=ModelDetails(family="test"),
    )


def _resources(**overrides: object) -> ResourceSnapshot:
    values: dict[str, object] = {
        "idle_seconds": 3_600.0,
        "cpu_percent": 5.0,
        "available_memory_bytes": 16 * 1024**3,
        "gpu_free_bytes": 8 * 1024**3,
        "active_model_jobs": 0,
        "on_ac_power": True,
    }
    values.update(overrides)
    return ResourceSnapshot(**values)  # type: ignore[arg-type]


def _service(path: Path, runner: ControlledRunner) -> DreamService:
    sqlite = SQLiteStore(path)
    OrchestrationStore(sqlite)
    service = DreamService(
        DreamStore(sqlite),
        runner,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    if not any(
        item.recipe.recipe_id == "scheduled-ideas"
        for item in service.list_recipes()
    ):
        service.save_recipe(
            DreamRecipe(
                recipe_id="scheduled-ideas",
                name="Scheduled Ideas",
                kind=DreamRecipeKind.CUSTOM,
                objective="Create a proposal from explicitly scoped project sources.",
                source_scopes=("project:*",),
            ),
            expected_version=0,
        )
    return service


def _executor(
    service: DreamService,
    *,
    resources: ResourceSnapshot | None = None,
) -> DreamBackgroundExecutor:
    return DreamBackgroundExecutor(
        service,
        ResourceGovernor(service.store.store),
        source_provider=lambda _schedule: [_source()],
        model_provider=lambda: [_model()],
        resource_provider=lambda: resources or _resources(),
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
        ),
        clock=lambda: NOW,
    )


def _schedule() -> DreamSchedule:
    return DreamSchedule(
        schedule_id="nightly",
        recipe_id="scheduled-ideas",
        timezone="UTC",
        local_time=time(2, 0),
        created_at_utc=datetime(2026, 7, 1, tzinfo=UTC),
    )


def _window() -> ScheduleWindow:
    return ScheduleWindow(
        window_key="dream:schedule:nightly:2026-07-27:020000",
        recipe_id="scheduled-ideas",
        origin=WindowOrigin.SCHEDULED,
        due_at_utc=datetime(2026, 7, 27, 2, 0, tzinfo=UTC),
        timezone="UTC",
        nominal_date=NOW.date(),
        nominal_time=time(2, 0),
        effective_local=datetime(2026, 7, 27, 2, 0, tzinfo=UTC),
    )


def test_schedule_quiet_window_and_enabled_state_persist_across_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first = _service(path, ControlledRunner())
    quiet = QuietWindow(
        timezone="America/New_York",
        start_local=time(22, 0),
        end_local=time(6, 0),
        weekdays=(0, 1, 2, 3, 4),
    )
    stored = first.save_schedule(
        _schedule(),
        resource_rules=ResourceRules(
            min_idle_seconds=600,
            min_gpu_free_bytes=4 * 1024**3,
        ),
        quiet_window=quiet,
        expected_version=0,
    )

    assert stored.version == 1
    assert quiet.contains(datetime(2026, 7, 28, 9, 0, tzinfo=UTC))
    assert not quiet.contains(datetime(2026, 7, 28, 12, 0, tzinfo=UTC))

    disabled = first.set_schedule_enabled("nightly", False)
    assert disabled.version == 2
    restarted = _service(path, ControlledRunner())
    persisted = restarted.list_schedules()[0]
    assert persisted.schedule.enabled is False
    assert persisted.quiet_window == quiet
    assert persisted.resource_rules.min_idle_seconds == 600
    assert restarted.list_schedules(enabled_only=True) == ()


def test_schedule_can_be_paused_after_its_custom_recipe_is_deleted(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "master.db", ControlledRunner())
    service.save_recipe(
        DreamRecipe(
            recipe_id="temporary-scheduled-recipe",
            name="Temporary scheduled recipe",
            kind=DreamRecipeKind.CUSTOM,
            objective="Exercise fail-safe schedule pausing.",
            source_scopes=("project:*",),
        ),
        expected_version=0,
    )
    stored = service.save_schedule(
        DreamSchedule(
            schedule_id="temporary-nightly",
            recipe_id="temporary-scheduled-recipe",
            timezone="UTC",
            local_time=time(2, 0),
            created_at_utc=NOW,
        ),
        resource_rules=ResourceRules(min_idle_seconds=123),
        quiet_window=QuietWindow(
            timezone="UTC",
            start_local=time(1, 0),
            end_local=time(4, 0),
        ),
        expected_version=0,
    )
    service.delete_recipe("temporary-scheduled-recipe")

    paused = service.set_schedule_enabled("temporary-nightly", False)

    assert stored.version == 1
    assert paused.enabled is False
    assert paused.version == 2
    assert paused.created_at_utc == stored.created_at_utc
    assert paused.resource_rules == stored.resource_rules
    assert paused.quiet_window == stored.quiet_window
    assert service.get_schedule("temporary-nightly") == paused

    restarted = _service(tmp_path / "master.db", ControlledRunner())
    assert restarted.get_schedule("temporary-nightly") == paused
    assert restarted.list_schedules(enabled_only=True) == ()
    with pytest.raises(DreamInboxError, match="unknown dream recipe"):
        restarted.set_schedule_enabled("temporary-nightly", True)
    with pytest.raises(DreamInboxError, match="unknown dream recipe"):
        restarted.save_schedule(
            DreamSchedule(
                schedule_id="unknown-recipe-draft",
                recipe_id="missing-recipe",
                timezone="UTC",
                local_time=time(2, 0),
                created_at_utc=NOW,
                enabled=False,
            ),
            expected_version=0,
        )


def test_manual_submission_returns_while_background_run_is_still_working(
    tmp_path: Path,
) -> None:
    runner = ControlledRunner(blocked=True)
    service = _service(tmp_path / "master.db", runner)
    executor = _executor(service)

    queued = executor.submit_manual(
        recipe_id="idea-garden",
        request_id="nonblocking",
        sources=[_source()],
    )

    assert queued.run.status is DreamExecutionStatus.CLAIMED
    assert runner.entered.wait(1)
    assert service.get_run(queued.run.run_id).status is DreamExecutionStatus.RUNNING
    runner.release.set()
    assert executor.wait_for_idle(2)

    finished = service.get_run(queued.run.run_id)
    assert finished.status is DreamExecutionStatus.COMPLETE
    assert finished.item_id is not None
    item = service.get_inbox_item(finished.item_id)
    assert item.disposition is DreamDisposition.PENDING
    event_types = {
        event.event_type for event in service.list_events(run_id=finished.run_id)
    }
    assert {
        "run_claimed",
        "background_dispatched",
        "run_started",
        "run_complete",
    } <= event_types


def test_scheduled_window_runs_once_and_remains_claimed_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first_runner = ControlledRunner()
    first = _service(path, first_runner)
    first.save_schedule(
        _schedule(),
        quiet_window=QuietWindow(
            timezone="UTC",
            start_local=time(1, 0),
            end_local=time(4, 0),
        ),
        expected_version=0,
    )
    executor = _executor(first)

    report = executor.tick(now_utc=NOW)
    assert len(report.started_run_ids) == 1
    assert executor.wait_for_idle(2)
    assert first_runner.calls == 1

    second_runner = ControlledRunner()
    restarted = _service(path, second_runner)
    replay = _executor(restarted).tick(now_utc=NOW)

    assert replay.started_run_ids == ()
    assert second_runner.calls == 0
    assert len(restarted.list_runs()) == 1
    assert restarted.store.seen_window_keys() == {
        "dream:schedule:nightly:2026-07-27:020000"
    }


def test_restart_resumes_claimed_run_without_creating_a_duplicate_window(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first = _service(path, ControlledRunner())
    first.save_schedule(_schedule(), expected_version=0)
    queued = first.queue_scheduled(
        window=_window(),
        schedule_id="nightly",
        sources=[_source()],
    )
    assert queued.run.status is DreamExecutionStatus.CLAIMED

    runner = ControlledRunner()
    restarted = _service(path, runner)
    executor = _executor(restarted)
    recovered = executor.tick(now_utc=NOW)

    assert recovered.recovered_run_ids == (queued.run.run_id,)
    assert recovered.started_run_ids == (queued.run.run_id,)
    assert executor.wait_for_idle(2)
    assert restarted.get_run(queued.run.run_id).status is DreamExecutionStatus.COMPLETE
    assert runner.calls == 1
    assert len(restarted.list_runs()) == 1
    assert restarted.store.seen_window_keys() == {queued.run.window_key}


def test_recovered_schedule_is_cancelled_when_source_consent_was_revoked(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first = _service(path, ControlledRunner())
    first.save_schedule(_schedule(), expected_version=0)
    still_allowed = DreamSource(
        source_id="still-allowed",
        kind=SourceKind.PROJECT,
        locator="project://still-allowed",
        content="This second source remains authorized.",
        captured_at_utc=NOW,
    )
    queued = first.queue_scheduled(
        window=_window(),
        schedule_id="nightly",
        sources=[_source(), still_allowed],
    )

    runner = ControlledRunner()
    restarted = _service(path, runner)
    executor = DreamBackgroundExecutor(
        restarted,
        ResourceGovernor(restarted.store.store),
        source_provider=lambda _schedule: (still_allowed,),
        model_provider=lambda: [_model()],
        resource_provider=_resources,
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
        ),
        clock=lambda: NOW,
    )

    report = executor.tick(now_utc=NOW)

    assert report.started_run_ids == ()
    assert report.recovered_run_ids == (queued.run.run_id,)
    assert report.deferred == (("nightly", "authorization_revoked"),)
    assert restarted.get_run(queued.run.run_id).status is DreamExecutionStatus.CANCELLED
    assert runner.calls == 0
    events = restarted.list_events(run_id=queued.run.run_id)
    assert events[-1].event_type == "recovery_authorization_denied"
    assert events[-1].payload == {"reason": "source_authorization_changed"}


def test_transient_recovery_authorization_error_retries_without_cancelling(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first = _service(path, ControlledRunner())
    first.save_schedule(_schedule(), expected_version=0)
    queued = first.queue_scheduled(
        window=_window(),
        schedule_id="nightly",
        sources=[_source()],
    )

    source_available = False

    def sources(_schedule) -> tuple[DreamSource, ...]:
        if not source_available:
            raise RuntimeError("temporary source database outage")
        return (_source(),)

    runner = ControlledRunner()
    restarted = _service(path, runner)
    executor = DreamBackgroundExecutor(
        restarted,
        ResourceGovernor(restarted.store.store),
        source_provider=sources,
        model_provider=lambda: [_model()],
        resource_provider=_resources,
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
        ),
        clock=lambda: NOW,
    )

    deferred = executor.tick(now_utc=NOW)
    assert deferred.started_run_ids == ()
    assert deferred.deferred == (("nightly", "authorization_error"),)
    assert restarted.get_run(queued.run.run_id).status is DreamExecutionStatus.CLAIMED
    assert runner.calls == 0
    assert restarted.list_events(run_id=queued.run.run_id)[-1].event_type == (
        "recovery_authorization_error"
    )

    source_available = True
    resumed = executor.tick(now_utc=NOW)
    assert resumed.started_run_ids == (queued.run.run_id,)
    assert executor.wait_for_idle(2)
    assert restarted.get_run(queued.run.run_id).status is DreamExecutionStatus.COMPLETE
    assert runner.calls == 1


def test_resource_lease_defers_then_interactive_priority_cancels_background_work(
    tmp_path: Path,
) -> None:
    runner = ControlledRunner(blocked=True)
    service = _service(tmp_path / "master.db", runner)
    governor = ResourceGovernor(service.store.store)
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        "interactive-chat",
        ttl_seconds=30,
    )
    executor = DreamBackgroundExecutor(
        service,
        governor,
        source_provider=lambda _schedule: [_source()],
        model_provider=lambda: [_model()],
        resource_provider=lambda: _resources(),
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
        ),
        clock=lambda: NOW,
    )
    service.save_schedule(_schedule(), expected_version=0)

    deferred = executor.tick(now_utc=NOW)
    run_id = service.list_runs()[0].run_id
    assert deferred.started_run_ids == ()
    assert service.get_run(run_id).status is DreamExecutionStatus.CLAIMED
    assert governor.release(LOCAL_GPU_INFERENCE_RESOURCE, "interactive-chat")

    started = executor.tick(now_utc=NOW)
    assert started.started_run_ids == (run_id,)
    assert runner.entered.wait(1)
    executor.set_interactive_busy(True)
    assert executor.wait_for_idle(2)

    cancelled = service.get_run(run_id)
    assert cancelled.status is DreamExecutionStatus.CANCELLED
    assert cancelled.item_id is None
    assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None
    event_types = {
        event.event_type for event in service.list_events(run_id=run_id)
    }
    assert "resource_deferred" in event_types
    assert "interactive_preemption" in event_types
    assert "cancel_requested" in event_types
    assert "run_cancelled" in event_types


def test_quiet_window_and_active_model_jobs_defer_without_claiming(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    service = _service(path, ControlledRunner())
    service.save_schedule(
        _schedule(),
        quiet_window=QuietWindow(
            timezone="UTC",
            start_local=time(3, 0),
            end_local=time(4, 0),
        ),
        expected_version=0,
    )
    outside = _executor(service).tick(now_utc=NOW)

    assert outside.deferred == (("nightly", "outside_quiet_window"),)
    assert service.list_runs() == ()
    assert service.store.seen_window_keys() == frozenset()

    current = service.store.get_schedule("nightly")
    service.store.save_schedule(
        current.schedule,
        resource_rules=current.resource_rules,
        quiet_window=QuietWindow(
            timezone="UTC",
            start_local=time(1, 0),
            end_local=time(4, 0),
        ),
        updated_at_utc=NOW,
        expected_version=current.version,
    )
    busy = _executor(service, resources=_resources(active_model_jobs=1)).tick(
        now_utc=NOW
    )
    assert busy.deferred == (("nightly", "model_busy"),)
    assert service.list_runs() == ()


def test_empty_consent_filtered_schedule_is_deferred_without_claiming(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "master.db", ControlledRunner())
    service.save_schedule(_schedule(), expected_version=0)
    executor = DreamBackgroundExecutor(
        service,
        ResourceGovernor(service.store.store),
        source_provider=lambda _schedule: (),
        model_provider=lambda: [_model()],
        resource_provider=_resources,
        config=DreamBackgroundConfig(
            poll_interval_seconds=0.1,
            lease_ttl_seconds=5,
        ),
        clock=lambda: NOW,
    )

    report = executor.tick(now_utc=NOW)

    assert report.started_run_ids == ()
    assert report.deferred == (("nightly", "schedule_error"),)
    assert service.list_runs() == ()
    assert service.store.seen_window_keys() == frozenset()
    events = service.list_events(schedule_id="nightly")
    assert events[-1].event_type == "schedule_error"
    assert "at least one consented source" in events[-1].message


def test_cancelling_a_claimed_run_is_terminal_and_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path / "master.db", ControlledRunner())
    queued = service.queue_manual(
        recipe_id="risk-scan",
        request_id="cancel-before-start",
        sources=[_source()],
    )

    first = service.cancel(queued.run.run_id)
    second = service.cancel(queued.run.run_id)

    assert first.status is DreamExecutionStatus.CANCELLED
    assert second.status is DreamExecutionStatus.CANCELLED
    assert service.list_inbox() == ()
    replay = service.queue_manual(
        recipe_id="risk-scan",
        request_id="cancel-before-start",
        sources=[_source()],
    )
    assert replay.already_existed is True
    assert replay.run.run_id == queued.run.run_id


def test_direct_execution_resumes_an_existing_claimed_manual_run(
    tmp_path: Path,
) -> None:
    runner = ControlledRunner()
    service = _service(tmp_path / "master.db", runner)
    queued = service.queue_manual(
        recipe_id="idea-garden",
        request_id="resume-direct",
        sources=[_source()],
    )

    resumed = service.execute_manual(
        recipe_id="idea-garden",
        request_id="resume-direct",
        sources=[_source()],
        models=[_model()],
    )

    assert resumed.run.run_id == queued.run.run_id
    assert resumed.run.status is DreamExecutionStatus.COMPLETE
    assert runner.calls == 1


def test_cancel_requested_during_finalization_wins_over_a_late_success(
    tmp_path: Path,
) -> None:
    sqlite = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(sqlite)
    runner = StoreCancellingRunner()
    store = DreamStore(sqlite)
    runner.store = store
    service = DreamService(store, runner, clock=lambda: NOW)  # type: ignore[arg-type]

    execution = service.execute_manual(
        recipe_id="idea-garden",
        request_id="cancel-at-finalize",
        sources=[_source()],
        models=[_model()],
    )

    assert execution.run.status is DreamExecutionStatus.CANCELLED
    assert execution.item is None
    assert execution.run.item_id is None
    assert service.list_inbox() == ()


def test_claimed_custom_recipe_uses_its_pinned_version_after_recipe_deletion(
    tmp_path: Path,
) -> None:
    runner = ControlledRunner()
    service = _service(tmp_path / "master.db", runner)
    service.save_recipe(
        DreamRecipe(
            recipe_id="custom-durable",
            name="Durable Custom",
            kind=DreamRecipeKind.CUSTOM,
            objective="Produce a proposal from the pinned recipe version.",
        ),
        expected_version=0,
    )
    queued = service.queue_manual(
        recipe_id="custom-durable",
        request_id="queued-before-delete",
        sources=[_source()],
    )
    service.delete_recipe("custom-durable")

    finished = service.execute_queued(queued.run.run_id, models=[_model()])

    assert finished.run.status is DreamExecutionStatus.COMPLETE
    assert finished.item is not None
    assert runner.calls == 1
