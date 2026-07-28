from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from project_master.dreams.models import DreamPolicy
from project_master.dreams.scheduling import (
    AppDreamScheduler,
    CatchUpMode,
    DreamSchedule,
    EligibilityReason,
    InMemoryWindowClaimStore,
    ManualDreamRequest,
    ResourceRules,
    ResourceSnapshot,
    ScheduleOwner,
    ScheduleState,
    WindowOrigin,
)


def _resources(**overrides: object) -> ResourceSnapshot:
    values: dict[str, object] = {
        "idle_seconds": 1_000.0,
        "cpu_percent": 10.0,
        "available_memory_bytes": 16 * 1024**3,
        "gpu_free_bytes": 8 * 1024**3,
        "active_model_jobs": 0,
        "on_ac_power": True,
    }
    values.update(overrides)
    return ResourceSnapshot(**values)  # type: ignore[arg-type]


def _schedule(
    *,
    timezone: str = "America/New_York",
    local_time: time = time(2, 0),
    catch_up: CatchUpMode = CatchUpMode.SKIP,
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    grace: timedelta = timedelta(minutes=15),
    max_catch_up: int = 3,
) -> DreamSchedule:
    return DreamSchedule(
        schedule_id="nightly",
        recipe_id="idea-garden",
        timezone=timezone,
        local_time=local_time,
        created_at_utc=created_at,
        catch_up=catch_up,
        on_time_grace=grace,
        max_catch_up_windows=max_catch_up,
    )


def test_daily_window_key_is_stable_across_restart_and_claims_are_idempotent() -> None:
    scheduler = AppDreamScheduler()
    schedule = _schedule()
    policy = DreamPolicy(scheduled_enabled=True)
    now = datetime(2026, 7, 27, 6, 5, tzinfo=UTC)

    first = scheduler.evaluate_scheduled(
        schedule,
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(),
    )

    assert first.eligible is True
    window = first.windows[0]
    assert schedule.owner is ScheduleOwner.APP
    assert window.window_key == "dream:schedule:nightly:2026-07-27:020000"
    assert window.due_at_utc == datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
    store = InMemoryWindowClaimStore()
    assert scheduler.claim_next(first, store) == window
    assert scheduler.claim_next(first, store) is None

    restarted_store = InMemoryWindowClaimStore(store.seen_window_keys())
    restarted = AppDreamScheduler().evaluate_scheduled(
        schedule,
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(restarted_store.seen_window_keys()),
    )
    assert restarted.eligible is False
    assert restarted.reason is EligibilityReason.DUPLICATE_WINDOW


def test_spring_dst_gap_runs_at_first_valid_local_minute() -> None:
    evaluation = AppDreamScheduler().evaluate_scheduled(
        _schedule(local_time=time(2, 30)),
        DreamPolicy(scheduled_enabled=True),
        ResourceRules(),
        _resources(),
        now_utc=datetime(2026, 3, 8, 7, 5, tzinfo=UTC),
        state=ScheduleState(),
    )

    assert evaluation.eligible is True
    window = evaluation.windows[0]
    assert window.nominal_time == time(2, 30)
    assert window.effective_local.hour == 3
    assert window.effective_local.minute == 0
    assert window.due_at_utc == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    assert window.dst_adjusted is True
    assert window.window_key.endswith("2026-03-08:023000")


def test_fall_dst_overlap_uses_first_physical_occurrence_once() -> None:
    evaluation = AppDreamScheduler().evaluate_scheduled(
        _schedule(local_time=time(1, 30)),
        DreamPolicy(scheduled_enabled=True),
        ResourceRules(),
        _resources(),
        now_utc=datetime(2026, 11, 1, 5, 35, tzinfo=UTC),
        state=ScheduleState(),
    )

    assert evaluation.eligible is True
    window = evaluation.windows[0]
    assert window.due_at_utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert window.effective_local.fold == 0
    assert window.dst_adjusted is False
    assert len(evaluation.windows) == 1


def test_missed_window_modes_skip_latest_and_bounded_all() -> None:
    now = datetime(2026, 1, 4, 13, 0, tzinfo=UTC)
    base = _schedule(
        timezone="UTC",
        local_time=time(12, 0),
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        grace=timedelta(minutes=10),
    )
    scheduler = AppDreamScheduler()
    policy = DreamPolicy(scheduled_enabled=True)

    skipped = scheduler.evaluate_scheduled(
        replace(base, catch_up=CatchUpMode.SKIP),
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(),
    )
    latest = scheduler.evaluate_scheduled(
        replace(base, catch_up=CatchUpMode.LATEST),
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(),
    )
    all_bounded = scheduler.evaluate_scheduled(
        replace(
            base,
            catch_up=CatchUpMode.ALL_BOUNDED,
            max_catch_up_windows=2,
        ),
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(),
    )

    assert skipped.reason is EligibilityReason.NOT_DUE
    assert [window.nominal_date.isoformat() for window in latest.windows] == [
        "2026-01-04"
    ]
    assert [window.nominal_date.isoformat() for window in all_bounded.windows] == [
        "2026-01-01",
        "2026-01-02",
    ]
    seen = frozenset(window.window_key for window in all_bounded.windows)
    next_batch = scheduler.evaluate_scheduled(
        replace(
            base,
            catch_up=CatchUpMode.ALL_BOUNDED,
            max_catch_up_windows=2,
        ),
        policy,
        ResourceRules(),
        _resources(),
        now_utc=now,
        state=ScheduleState(seen),
    )
    assert [window.nominal_date.isoformat() for window in next_batch.windows] == [
        "2026-01-03",
        "2026-01-04",
    ]
    assert all(window.origin is WindowOrigin.CATCH_UP for window in latest.windows)


def test_scheduled_runs_obey_idle_and_resource_rules() -> None:
    args = (
        _schedule(),
        DreamPolicy(scheduled_enabled=True),
        ResourceRules(min_gpu_free_bytes=4 * 1024**3),
    )
    now = datetime(2026, 7, 27, 6, 5, tzinfo=UTC)

    not_idle = AppDreamScheduler().evaluate_scheduled(
        *args,
        _resources(idle_seconds=5.0),
        now_utc=now,
        state=ScheduleState(),
    )
    model_busy = AppDreamScheduler().evaluate_scheduled(
        *args,
        _resources(active_model_jobs=1),
        now_utc=now,
        state=ScheduleState(),
    )
    gpu_unknown = AppDreamScheduler().evaluate_scheduled(
        *args,
        _resources(gpu_free_bytes=None),
        now_utc=now,
        state=ScheduleState(),
    )

    assert not_idle.reason is EligibilityReason.NOT_IDLE
    assert model_busy.reason is EligibilityReason.MODEL_BUSY
    assert gpu_unknown.reason is EligibilityReason.GPU_UNKNOWN
    assert not_idle.windows


def test_manual_request_bypasses_idle_but_not_safety_resources_or_duplicate_key() -> None:
    scheduler = AppDreamScheduler()
    request = ManualDreamRequest(
        request_id="click-123",
        recipe_id="idea-garden",
        requested_at_utc=datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    )
    policy = DreamPolicy(manual_bypasses_idle=True)
    rules = ResourceRules(min_gpu_free_bytes=4 * 1024**3)

    eligible = scheduler.evaluate_manual(
        request,
        policy,
        rules,
        _resources(idle_seconds=0),
        state=ScheduleState(),
    )
    assert eligible.eligible is True
    assert eligible.windows[0].origin is WindowOrigin.MANUAL

    duplicate = scheduler.evaluate_manual(
        request,
        policy,
        rules,
        _resources(),
        state=ScheduleState(frozenset({eligible.windows[0].window_key})),
    )
    low_gpu = scheduler.evaluate_manual(
        replace(request, request_id="click-124"),
        policy,
        rules,
        _resources(gpu_free_bytes=1),
        state=ScheduleState(),
    )
    assert duplicate.reason is EligibilityReason.DUPLICATE_WINDOW
    assert low_gpu.reason is EligibilityReason.GPU_LOW


def test_schedule_requires_iana_timezone_and_aware_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        _schedule(timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="timezone-aware"):
        _schedule(created_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="owned and evaluated"):
        replace(_schedule(), owner="external")  # type: ignore[arg-type]
