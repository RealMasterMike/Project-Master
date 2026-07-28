from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from project_master.dreams.models import (
    DreamPolicy,
    _require_identifier,
    _utc,
)


class CatchUpMode(StrEnum):
    SKIP = "skip"
    LATEST = "latest"
    ALL_BOUNDED = "all_bounded"


class WindowOrigin(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    CATCH_UP = "catch_up"


class ScheduleOwner(StrEnum):
    APP = "app"


class EligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    NOT_DUE = "not_due"
    DUPLICATE_WINDOW = "duplicate_window"
    OUTSIDE_QUIET_WINDOW = "outside_quiet_window"
    NOT_IDLE = "not_idle"
    MODEL_BUSY = "model_busy"
    CPU_BUSY = "cpu_busy"
    MEMORY_LOW = "memory_low"
    GPU_UNKNOWN = "gpu_unknown"
    GPU_LOW = "gpu_low"
    AC_POWER_REQUIRED = "ac_power_required"


@dataclass(frozen=True, slots=True)
class ResourceRules:
    min_idle_seconds: float = 300.0
    max_cpu_percent: float = 60.0
    min_available_memory_bytes: int = 2 * 1024**3
    min_gpu_free_bytes: int | None = None
    require_no_model_jobs: bool = True
    require_ac_power: bool = False

    def __post_init__(self) -> None:
        if self.min_idle_seconds < 0:
            raise ValueError("min_idle_seconds must not be negative")
        if not 0 <= self.max_cpu_percent <= 100:
            raise ValueError("max_cpu_percent must be between 0 and 100")
        if self.min_available_memory_bytes < 0:
            raise ValueError("min_available_memory_bytes must not be negative")
        if self.min_gpu_free_bytes is not None and self.min_gpu_free_bytes < 0:
            raise ValueError("min_gpu_free_bytes must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "min_idle_seconds": self.min_idle_seconds,
            "max_cpu_percent": self.max_cpu_percent,
            "min_available_memory_bytes": self.min_available_memory_bytes,
            "min_gpu_free_bytes": self.min_gpu_free_bytes,
            "require_no_model_jobs": self.require_no_model_jobs,
            "require_ac_power": self.require_ac_power,
        }


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    idle_seconds: float
    cpu_percent: float
    available_memory_bytes: int
    gpu_free_bytes: int | None
    active_model_jobs: int
    on_ac_power: bool

    def __post_init__(self) -> None:
        if self.idle_seconds < 0:
            raise ValueError("idle_seconds must not be negative")
        if not 0 <= self.cpu_percent <= 100:
            raise ValueError("cpu_percent must be between 0 and 100")
        if self.available_memory_bytes < 0:
            raise ValueError("available_memory_bytes must not be negative")
        if self.gpu_free_bytes is not None and self.gpu_free_bytes < 0:
            raise ValueError("gpu_free_bytes must not be negative")
        if self.active_model_jobs < 0:
            raise ValueError("active_model_jobs must not be negative")


@dataclass(frozen=True, slots=True)
class QuietWindow:
    """A local wall-clock interval in which scheduled dreaming may start."""

    timezone: str
    start_local: time
    end_local: time
    weekdays: tuple[int, ...] = tuple(range(7))

    def __post_init__(self) -> None:
        if self.start_local.tzinfo is not None or self.end_local.tzinfo is not None:
            raise ValueError("quiet-window times must not include tzinfo")
        if self.start_local == self.end_local:
            raise ValueError("quiet-window start and end must differ")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        normalized = tuple(sorted(set(self.weekdays)))
        if not normalized or any(day < 0 or day > 6 for day in normalized):
            raise ValueError("quiet-window weekdays must contain values from 0 through 6")
        object.__setattr__(self, "weekdays", normalized)

    def contains(self, now_utc: datetime) -> bool:
        now = _utc(now_utc, "now_utc").astimezone(ZoneInfo(self.timezone))
        wall = now.timetz().replace(tzinfo=None)
        if self.start_local < self.end_local:
            return now.weekday() in self.weekdays and self.start_local <= wall < self.end_local

        if wall >= self.start_local:
            anchor = now.date()
        elif wall < self.end_local:
            anchor = now.date() - timedelta(days=1)
        else:
            return False
        return anchor.weekday() in self.weekdays

    def to_dict(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "start_local": self.start_local.isoformat(),
            "end_local": self.end_local.isoformat(),
            "weekdays": list(self.weekdays),
        }


@dataclass(frozen=True, slots=True)
class DreamSchedule:
    schedule_id: str
    recipe_id: str
    timezone: str
    local_time: time
    created_at_utc: datetime
    owner: ScheduleOwner = ScheduleOwner.APP
    enabled: bool = True
    catch_up: CatchUpMode = CatchUpMode.LATEST
    on_time_grace: timedelta = timedelta(minutes=15)
    max_lookback_days: int = 7
    max_catch_up_windows: int = 3

    def __post_init__(self) -> None:
        if self.owner is not ScheduleOwner.APP:
            raise ValueError("Dream schedules are owned and evaluated by the running app")
        if not isinstance(self.catch_up, CatchUpMode):
            raise ValueError("catch_up must be a CatchUpMode")
        object.__setattr__(
            self,
            "schedule_id",
            _require_identifier(self.schedule_id, "schedule_id"),
        )
        object.__setattr__(
            self,
            "recipe_id",
            _require_identifier(self.recipe_id, "recipe_id"),
        )
        if self.local_time.tzinfo is not None:
            raise ValueError("local_time must be a wall-clock time without tzinfo")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        object.__setattr__(
            self,
            "created_at_utc",
            _utc(self.created_at_utc, "created_at_utc"),
        )
        if self.on_time_grace < timedelta(0):
            raise ValueError("on_time_grace must not be negative")
        if self.max_lookback_days < 1:
            raise ValueError("max_lookback_days must be positive")
        if self.max_catch_up_windows < 1:
            raise ValueError("max_catch_up_windows must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "schedule_id": self.schedule_id,
            "recipe_id": self.recipe_id,
            "timezone": self.timezone,
            "local_time": self.local_time.isoformat(),
            "created_at_utc": self.created_at_utc.isoformat(),
            "owner": self.owner.value,
            "enabled": self.enabled,
            "catch_up": self.catch_up.value,
            "on_time_grace_seconds": self.on_time_grace.total_seconds(),
            "max_lookback_days": self.max_lookback_days,
            "max_catch_up_windows": self.max_catch_up_windows,
        }


@dataclass(frozen=True, slots=True)
class ManualDreamRequest:
    request_id: str
    recipe_id: str
    requested_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            _require_identifier(self.request_id, "request_id"),
        )
        object.__setattr__(
            self,
            "recipe_id",
            _require_identifier(self.recipe_id, "recipe_id"),
        )
        object.__setattr__(
            self,
            "requested_at_utc",
            _utc(self.requested_at_utc, "requested_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class ScheduleWindow:
    window_key: str
    recipe_id: str
    origin: WindowOrigin
    due_at_utc: datetime
    timezone: str
    nominal_date: date
    nominal_time: time
    effective_local: datetime
    dst_adjusted: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "window_key": self.window_key,
            "recipe_id": self.recipe_id,
            "origin": self.origin.value,
            "due_at_utc": self.due_at_utc.isoformat(),
            "timezone": self.timezone,
            "nominal_date": self.nominal_date.isoformat(),
            "nominal_time": self.nominal_time.isoformat(),
            "effective_local": self.effective_local.isoformat(),
            "dst_adjusted": self.dst_adjusted,
        }


@dataclass(frozen=True, slots=True)
class ScheduleState:
    seen_window_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ScheduleEvaluation:
    eligible: bool
    reason: EligibilityReason
    windows: tuple[ScheduleWindow, ...]
    checked_at_utc: datetime


class WindowClaimStore(Protocol):
    def seen_window_keys(self) -> frozenset[str]:
        """Return persisted claimed/running/completed window keys."""

    def claim(self, window_key: str, claimed_at_utc: datetime) -> bool:
        """Atomically claim a key, returning false when it was already seen."""


class InMemoryWindowClaimStore:
    """Reference claim store; production integration should use a database unique key."""

    def __init__(self, seed: frozenset[str] | None = None) -> None:
        self._keys = set(seed or ())
        self._lock = threading.Lock()

    def seen_window_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._keys)

    def claim(self, window_key: str, claimed_at_utc: datetime) -> bool:
        _utc(claimed_at_utc, "claimed_at_utc")
        with self._lock:
            if window_key in self._keys:
                return False
            self._keys.add(window_key)
            return True


class AppDreamScheduler:
    """Pure eligibility evaluator. The desktop app owns polling and persistence."""

    def evaluate_scheduled(
        self,
        schedule: DreamSchedule,
        policy: DreamPolicy,
        rules: ResourceRules,
        resources: ResourceSnapshot,
        *,
        now_utc: datetime,
        state: ScheduleState,
        quiet_window: QuietWindow | None = None,
    ) -> ScheduleEvaluation:
        now = _utc(now_utc, "now_utc")
        if not schedule.enabled or not policy.scheduled_enabled:
            return ScheduleEvaluation(False, EligibilityReason.DISABLED, (), now)
        due = self._due_windows(schedule, now)
        unseen = tuple(item for item in due if item.window_key not in state.seen_window_keys)
        if schedule.catch_up is CatchUpMode.ALL_BOUNDED:
            unseen = unseen[: schedule.max_catch_up_windows]
        if not unseen:
            reason = (
                EligibilityReason.DUPLICATE_WINDOW
                if due
                else EligibilityReason.NOT_DUE
            )
            return ScheduleEvaluation(False, reason, (), now)
        if quiet_window is not None and not quiet_window.contains(now):
            return ScheduleEvaluation(
                False,
                EligibilityReason.OUTSIDE_QUIET_WINDOW,
                unseen,
                now,
            )
        blocked = _resource_block(rules, resources, enforce_idle=True)
        if blocked is not None:
            return ScheduleEvaluation(False, blocked, unseen, now)
        return ScheduleEvaluation(True, EligibilityReason.ELIGIBLE, unseen, now)

    def evaluate_manual(
        self,
        request: ManualDreamRequest,
        policy: DreamPolicy,
        rules: ResourceRules,
        resources: ResourceSnapshot,
        *,
        state: ScheduleState,
    ) -> ScheduleEvaluation:
        now = request.requested_at_utc
        if not policy.manual_enabled:
            return ScheduleEvaluation(False, EligibilityReason.DISABLED, (), now)
        key = f"dream:manual:{request.recipe_id}:{request.request_id}"
        if key in state.seen_window_keys:
            return ScheduleEvaluation(False, EligibilityReason.DUPLICATE_WINDOW, (), now)
        blocked = _resource_block(
            rules,
            resources,
            enforce_idle=not policy.manual_bypasses_idle,
        )
        window = ScheduleWindow(
            window_key=key,
            recipe_id=request.recipe_id,
            origin=WindowOrigin.MANUAL,
            due_at_utc=now,
            timezone="UTC",
            nominal_date=now.date(),
            nominal_time=now.timetz().replace(tzinfo=None),
            effective_local=now,
        )
        if blocked is not None:
            return ScheduleEvaluation(False, blocked, (window,), now)
        return ScheduleEvaluation(True, EligibilityReason.ELIGIBLE, (window,), now)

    @staticmethod
    def claim_next(
        evaluation: ScheduleEvaluation,
        store: WindowClaimStore,
    ) -> ScheduleWindow | None:
        """Atomically claim one eligible window; another app tick may win the race."""
        if not evaluation.eligible:
            return None
        for window in evaluation.windows:
            if store.claim(window.window_key, evaluation.checked_at_utc):
                return window
        return None

    def _due_windows(
        self,
        schedule: DreamSchedule,
        now_utc: datetime,
    ) -> tuple[ScheduleWindow, ...]:
        zone = ZoneInfo(schedule.timezone)
        now_local_date = now_utc.astimezone(zone).date()
        earliest_date = now_local_date - timedelta(days=schedule.max_lookback_days)
        candidates: list[ScheduleWindow] = []
        current = earliest_date
        while current <= now_local_date:
            effective_local, dst_adjusted = _resolve_local(
                current,
                schedule.local_time,
                zone,
            )
            due_at = effective_local.astimezone(UTC)
            if schedule.created_at_utc <= due_at <= now_utc:
                lateness = now_utc - due_at
                is_on_time = lateness <= schedule.on_time_grace
                if schedule.catch_up is not CatchUpMode.SKIP or is_on_time:
                    origin = (
                        WindowOrigin.SCHEDULED
                        if is_on_time
                        else WindowOrigin.CATCH_UP
                    )
                    candidates.append(
                        ScheduleWindow(
                            window_key=_schedule_window_key(schedule, current),
                            recipe_id=schedule.recipe_id,
                            origin=origin,
                            due_at_utc=due_at,
                            timezone=schedule.timezone,
                            nominal_date=current,
                            nominal_time=schedule.local_time,
                            effective_local=effective_local,
                            dst_adjusted=dst_adjusted,
                        )
                    )
            current += timedelta(days=1)

        candidates.sort(key=lambda item: item.due_at_utc)
        if schedule.catch_up is CatchUpMode.LATEST:
            return tuple(candidates[-1:])
        if schedule.catch_up is CatchUpMode.ALL_BOUNDED:
            return tuple(candidates)
        return tuple(candidates[-1:])


def _schedule_window_key(schedule: DreamSchedule, nominal_date: date) -> str:
    wall_time = schedule.local_time.strftime("%H%M%S")
    return (
        f"dream:schedule:{schedule.schedule_id}:"
        f"{nominal_date.isoformat()}:{wall_time}"
    )


def _resolve_local(
    nominal_date: date,
    nominal_time: time,
    zone: ZoneInfo,
) -> tuple[datetime, bool]:
    naive = datetime.combine(nominal_date, nominal_time)
    candidates = _valid_local_candidates(naive, zone)
    if candidates:
        # During a fall-back overlap, choose the first physical occurrence deterministically.
        return min(candidates, key=lambda item: item.astimezone(UTC)), False

    # During a spring-forward gap, run at the first valid local minute after the gap.
    probe = naive
    for _minute in range(180):
        probe += timedelta(minutes=1)
        candidates = _valid_local_candidates(probe, zone)
        if candidates:
            return min(candidates, key=lambda item: item.astimezone(UTC)), True
    raise ValueError(f"could not resolve local schedule time in {zone.key}")


def _valid_local_candidates(naive: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        utc_candidate = candidate.astimezone(UTC)
        round_trip = utc_candidate.astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive:
            candidates[utc_candidate] = round_trip
    return tuple(candidates.values())


def _resource_block(
    rules: ResourceRules,
    resources: ResourceSnapshot,
    *,
    enforce_idle: bool,
) -> EligibilityReason | None:
    if enforce_idle and resources.idle_seconds < rules.min_idle_seconds:
        return EligibilityReason.NOT_IDLE
    if rules.require_no_model_jobs and resources.active_model_jobs > 0:
        return EligibilityReason.MODEL_BUSY
    if resources.cpu_percent > rules.max_cpu_percent:
        return EligibilityReason.CPU_BUSY
    if resources.available_memory_bytes < rules.min_available_memory_bytes:
        return EligibilityReason.MEMORY_LOW
    if rules.min_gpu_free_bytes is not None:
        if resources.gpu_free_bytes is None:
            return EligibilityReason.GPU_UNKNOWN
        if resources.gpu_free_bytes < rules.min_gpu_free_bytes:
            return EligibilityReason.GPU_LOW
    if rules.require_ac_power and not resources.on_ac_power:
        return EligibilityReason.AC_POWER_REQUIRED
    return None
