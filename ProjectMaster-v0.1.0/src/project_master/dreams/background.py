from __future__ import annotations

import threading
import time as monotonic_time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from project_master.core.cancellation import CancellationToken
from project_master.dreams.inbox import DreamInboxError
from project_master.dreams.models import DreamPolicy
from project_master.dreams.scheduling import (
    AppDreamScheduler,
    EligibilityReason,
    ResourceSnapshot,
    ScheduleState,
)
from project_master.dreams.service import DreamExecution, DreamService
from project_master.dreams.snapshots import DreamSource, SnapshotPolicy
from project_master.dreams.store import (
    DreamExecutionStatus,
    DreamRunRecord,
    StoredDreamSchedule,
)
from project_master.orchestration.resource import (
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)
from project_master.team.models import CatalogModel

Clock = Callable[[], datetime]
ScheduleSourceProvider = Callable[[StoredDreamSchedule], Sequence[DreamSource]]
ModelProvider = Callable[[], Sequence[CatalogModel]]
ResourceProvider = Callable[[], ResourceSnapshot]
InteractiveProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class DreamBackgroundConfig:
    poll_interval_seconds: float = 15.0
    resource_key: str = LOCAL_GPU_INFERENCE_RESOURCE
    lease_ttl_seconds: int = 120
    preferred_lead: str | None = None

    def __post_init__(self) -> None:
        if not 0.1 <= self.poll_interval_seconds <= 3600:
            raise ValueError("Dream poll interval must be between 0.1 seconds and one hour")
        if not self.resource_key.strip():
            raise ValueError("Dream resource key must not be empty")
        if not 5 <= self.lease_ttl_seconds <= 86_400:
            raise ValueError("Dream lease TTL must be between 5 seconds and 24 hours")
        if self.lease_ttl_seconds <= self.poll_interval_seconds * 2:
            raise ValueError("Dream lease TTL must exceed two poll intervals")


@dataclass(frozen=True, slots=True)
class DreamTickReport:
    checked_at_utc: datetime
    started_run_ids: tuple[str, ...] = ()
    active_run_ids: tuple[str, ...] = ()
    recovered_run_ids: tuple[str, ...] = ()
    deferred: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "checked_at_utc": self.checked_at_utc.isoformat(),
            "started_run_ids": list(self.started_run_ids),
            "active_run_ids": list(self.active_run_ids),
            "recovered_run_ids": list(self.recovered_run_ids),
            "deferred": [
                {"schedule_id": schedule_id, "reason": reason}
                for schedule_id, reason in self.deferred
            ],
        }


@dataclass(slots=True)
class _ActiveRun:
    run_id: str
    owner: str
    token: CancellationToken
    thread: threading.Thread


class DreamBackgroundExecutor:
    """Run proposal-only Dream work off the request thread with durable claims."""

    def __init__(
        self,
        service: DreamService,
        governor: ResourceGovernor,
        *,
        source_provider: ScheduleSourceProvider,
        model_provider: ModelProvider,
        resource_provider: ResourceProvider,
        interactive_probe: InteractiveProbe | None = None,
        scheduler: AppDreamScheduler | None = None,
        config: DreamBackgroundConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.service = service
        self.governor = governor
        self.source_provider = source_provider
        self.model_provider = model_provider
        self.resource_provider = resource_provider
        self.interactive_probe = interactive_probe or (lambda: False)
        self.scheduler = scheduler or AppDreamScheduler()
        self.config = config or DreamBackgroundConfig()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._stop = threading.Event()
        self._interactive_busy = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._active: dict[str, _ActiveRun] = {}
        self._active_lock = threading.Lock()
        self._launch_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._last_schedule_reason: dict[str, EligibilityReason] = {}
        self._instance_id = uuid4().hex

    def start(self) -> bool:
        """Start one daemon supervisor. Calling start twice is idempotent."""
        with self._active_lock:
            if self._supervisor is not None and self._supervisor.is_alive():
                return False
            self._stop.clear()
            self._supervisor = threading.Thread(
                target=self._supervise,
                name="project-master-dream-supervisor",
                daemon=True,
            )
            self._supervisor.start()
        return True

    @property
    def running(self) -> bool:
        with self._active_lock:
            return self._supervisor is not None and self._supervisor.is_alive()

    @property
    def interactive_busy(self) -> bool:
        return self._interactive_requested()

    def shutdown(
        self,
        *,
        cancel_running: bool = True,
        timeout_seconds: float = 5.0,
    ) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        self._stop.set()
        with self._launch_lock:
            pass
        if cancel_running:
            self._cancel_active("Dream background executor is shutting down.")
        with self._active_lock:
            supervisor = self._supervisor
            workers = [active.thread for active in self._active.values()]
        deadline = monotonic_time.monotonic() + timeout_seconds
        if supervisor is not None:
            supervisor.join(max(deadline - monotonic_time.monotonic(), 0.0))
        for worker in workers:
            worker.join(max(deadline - monotonic_time.monotonic(), 0.0))
        with self._active_lock:
            supervisor_alive = self._supervisor is not None and self._supervisor.is_alive()
            workers_alive = any(active.thread.is_alive() for active in self._active.values())
        return not supervisor_alive and not workers_alive

    def set_interactive_busy(self, busy: bool) -> None:
        """Give foreground model/tool work priority over preemptible Dream work."""
        if busy:
            self._interactive_busy.set()
            self._cancel_active("Dream yielded to interactive work.")
        else:
            self._interactive_busy.clear()

    def submit_manual(
        self,
        *,
        recipe_id: str,
        request_id: str,
        sources: Sequence[DreamSource],
        preferred_lead: str | None = None,
        policy: DreamPolicy | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> DreamExecution:
        queued = self.service.queue_manual(
            recipe_id=recipe_id,
            request_id=request_id,
            sources=sources,
            preferred_lead=preferred_lead,
            policy=policy,
            snapshot_policy=snapshot_policy,
        )
        if (
            not queued.already_existed
            and not self._interactive_requested()
            and not self._has_active()
        ):
            self._launch(queued.run)
        return queued

    def cancel(self, run_id: str) -> DreamRunRecord:
        return self.service.cancel(run_id)

    def tick(self, *, now_utc: datetime | None = None) -> DreamTickReport:
        """Evaluate schedules once and dispatch at most one background run."""
        with self._tick_lock:
            checked_at = (now_utc or self.clock()).astimezone(UTC)
            self._renew_active(checked_at)
            if self._interactive_requested():
                self._cancel_active("Dream yielded to interactive work.")
                return DreamTickReport(
                    checked_at_utc=checked_at,
                    active_run_ids=self.active_run_ids(),
                    deferred=(("*", EligibilityReason.MODEL_BUSY.value),),
                )
            if self._has_active():
                return DreamTickReport(
                    checked_at_utc=checked_at,
                    active_run_ids=self.active_run_ids(),
                )

            claimed = self.service.store.list_claimed_runs(limit=1)
            if claimed:
                recovered = claimed[0]
                authorization_failure = self._recovered_authorization_failure(
                    recovered,
                )
                if authorization_failure is not None:
                    failure_kind, failure_reason = authorization_failure
                    if failure_kind == "error":
                        self.service.store.append_event(
                            "recovery_authorization_error",
                            run_id=recovered.run_id,
                            schedule_id=recovered.schedule_id,
                            window_key=recovered.window_key,
                            status=recovered.status.value,
                            message=(
                                "Claimed Dream schedule authorization could not "
                                "be rechecked; it remains claimed for retry."
                            ),
                            payload={"reason": failure_reason},
                            created_at_utc=checked_at,
                        )
                        return DreamTickReport(
                            checked_at_utc=checked_at,
                            recovered_run_ids=(recovered.run_id,),
                            deferred=((
                                recovered.schedule_id or "scheduled",
                                "authorization_error",
                            ),),
                        )
                    cancelled = self.service.cancel(recovered.run_id)
                    self.service.store.append_event(
                        "recovery_authorization_denied",
                        run_id=recovered.run_id,
                        schedule_id=recovered.schedule_id,
                        window_key=recovered.window_key,
                        status=cancelled.status.value,
                        message=(
                            "Claimed Dream schedule was cancelled before resume "
                            "because its source authorization changed."
                        ),
                        payload={"reason": failure_reason},
                        created_at_utc=checked_at,
                    )
                    return DreamTickReport(
                        checked_at_utc=checked_at,
                        recovered_run_ids=(recovered.run_id,),
                        deferred=((
                            recovered.schedule_id or "scheduled",
                            "authorization_revoked",
                        ),),
                    )
                started = self._launch(recovered)
                return DreamTickReport(
                    checked_at_utc=checked_at,
                    started_run_ids=(recovered.run_id,) if started else (),
                    active_run_ids=self.active_run_ids(),
                    recovered_run_ids=(recovered.run_id,),
                    deferred=(
                        ()
                        if started
                        else ((
                            recovered.schedule_id or "manual",
                            "resource_busy",
                        ),)
                    ),
                )

            resources = self.resource_provider()
            deferred: list[tuple[str, str]] = []
            policy = DreamPolicy(scheduled_enabled=True)
            for stored in self.service.list_schedules(enabled_only=True):
                evaluation = self.scheduler.evaluate_scheduled(
                    stored.schedule,
                    policy,
                    stored.resource_rules,
                    resources,
                    now_utc=checked_at,
                    state=ScheduleState(self.service.store.seen_window_keys()),
                    quiet_window=stored.quiet_window,
                )
                if not evaluation.eligible:
                    if evaluation.windows:
                        deferred.append(
                            (stored.schedule.schedule_id, evaluation.reason.value)
                        )
                        self._record_schedule_reason(
                            stored,
                            evaluation.reason,
                            checked_at,
                            evaluation.windows[0].window_key,
                        )
                    continue
                window = evaluation.windows[0]
                try:
                    sources = tuple(self.source_provider(stored))
                    queued = self.service.queue_scheduled(
                        window=window,
                        schedule_id=stored.schedule.schedule_id,
                        sources=sources,
                        policy=policy,
                    )
                except Exception as exc:
                    self.service.store.append_event(
                        "schedule_error",
                        schedule_id=stored.schedule.schedule_id,
                        window_key=window.window_key,
                        status="failed_to_claim",
                        message=(
                            "Dream schedule could not create a run: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        payload={"reason": type(exc).__name__},
                        created_at_utc=checked_at,
                    )
                    deferred.append((stored.schedule.schedule_id, "schedule_error"))
                    continue
                self._last_schedule_reason.pop(stored.schedule.schedule_id, None)
                started = self._launch(queued.run)
                return DreamTickReport(
                    checked_at_utc=checked_at,
                    started_run_ids=(queued.run.run_id,) if started else (),
                    active_run_ids=self.active_run_ids(),
                    recovered_run_ids=(
                        (queued.run.run_id,) if queued.already_existed else ()
                    ),
                    deferred=tuple(deferred)
                    + (
                        ()
                        if started
                        else ((stored.schedule.schedule_id, "resource_busy"),)
                    ),
                )
            return DreamTickReport(
                checked_at_utc=checked_at,
                active_run_ids=self.active_run_ids(),
                deferred=tuple(deferred),
            )

    def _recovered_authorization_failure(
        self,
        run: DreamRunRecord,
    ) -> tuple[str, str] | None:
        if run.schedule_id is None:
            return None
        try:
            schedule = self.service.get_schedule(run.schedule_id)
        except DreamInboxError:
            return ("denied", "schedule_unavailable")
        except Exception:
            return ("error", "schedule_lookup_failed")
        if not schedule.enabled:
            return ("denied", "schedule_disabled")
        if schedule.schedule.recipe_id != run.recipe_id:
            return ("denied", "schedule_recipe_changed")
        try:
            self.service.store.get_recipe(run.recipe_id)
        except DreamInboxError:
            return ("denied", "recipe_unavailable")
        except Exception:
            return ("error", "recipe_lookup_failed")
        try:
            current_sources = tuple(self.source_provider(schedule))
            current_snapshot = self.service.snapshot_builder.build(
                current_sources,
                policy=SnapshotPolicy(),
                captured_at_utc=self.clock(),
            )
            claimed_snapshot = self.service.store.snapshot_for_run(run.run_id)
        except Exception:
            return ("error", "source_resolution_failed")
        currently_authorized = {
            (entry.kind, entry.source_id, entry.content_sha256)
            for entry in current_snapshot.entries
        }
        claimed_sources = {
            (entry.kind, entry.source_id, entry.content_sha256)
            for entry in claimed_snapshot.entries
        }
        if not claimed_sources or not claimed_sources <= currently_authorized:
            return ("denied", "source_authorization_changed")
        return None

    def active_run_ids(self) -> tuple[str, ...]:
        with self._active_lock:
            return tuple(sorted(self._active))

    def wait_for_idle(self, timeout_seconds: float = 10.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        deadline = monotonic_time.monotonic() + timeout_seconds
        while monotonic_time.monotonic() < deadline:
            if not self._has_active():
                return True
            monotonic_time.sleep(0.01)
        return not self._has_active()

    def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.service.store.append_event(
                    "supervisor_error",
                    status="degraded",
                    message=f"Dream supervisor tick failed: {type(exc).__name__}: {exc}",
                    payload={"reason": type(exc).__name__},
                    created_at_utc=self.clock(),
                )
            self._stop.wait(self.config.poll_interval_seconds)

    def _launch(self, run: DreamRunRecord) -> bool:
        with self._launch_lock:
            if self._stop.is_set():
                return False
            if run.status is not DreamExecutionStatus.CLAIMED:
                return False
            if self._interactive_requested() or self._has_active():
                return False
            owner = f"dream-background:{self._instance_id}:{run.run_id}"
            if not self.governor.acquire(
                self.config.resource_key,
                owner,
                ttl_seconds=self.config.lease_ttl_seconds,
                metadata={
                    "kind": "dream",
                    "priority": "background",
                    "preemptible": True,
                    "run_id": run.run_id,
                },
            ):
                self.service.store.append_event(
                    "resource_deferred",
                    run_id=run.run_id,
                    schedule_id=run.schedule_id,
                    window_key=run.window_key,
                    status=run.status.value,
                    message="Dream is waiting for the shared inference resource.",
                    payload={"resource_key": self.config.resource_key},
                    created_at_utc=self.clock(),
                )
                return False
            token = CancellationToken()
            thread = threading.Thread(
                target=self._run,
                args=(run.run_id, owner, token),
                name=f"project-master-dream-{run.run_id[-8:]}",
                daemon=True,
            )
            with self._active_lock:
                if self._active:
                    self.governor.release(self.config.resource_key, owner)
                    return False
                self._active[run.run_id] = _ActiveRun(run.run_id, owner, token, thread)
            self.service.store.append_event(
                "background_dispatched",
                run_id=run.run_id,
                schedule_id=run.schedule_id,
                window_key=run.window_key,
                status=run.status.value,
                message="Dream run was dispatched to the background worker.",
                payload={
                    "resource_key": self.config.resource_key,
                    "proposal_only": True,
                },
                created_at_utc=self.clock(),
            )
            try:
                thread.start()
            except Exception:
                with self._active_lock:
                    self._active.pop(run.run_id, None)
                self.governor.release(self.config.resource_key, owner)
                raise
            return True

    def _run(self, run_id: str, owner: str, token: CancellationToken) -> None:
        try:
            models = tuple(self.model_provider())
            self.service.execute_queued(
                run_id,
                models=models,
                preferred_lead=self.config.preferred_lead,
                cancellation=token,
            )
        except Exception as exc:
            current = self.service.get_run(run_id)
            if current.status not in {
                DreamExecutionStatus.COMPLETE,
                DreamExecutionStatus.PARTIAL,
                DreamExecutionStatus.CANCELLED,
                DreamExecutionStatus.FAILED,
                DreamExecutionStatus.INTERRUPTED,
            }:
                self.service.store.finish_run(
                    run_id,
                    status=(
                        DreamExecutionStatus.CANCELLED
                        if token.cancelled
                        else DreamExecutionStatus.FAILED
                    ),
                    updated_at_utc=self.clock(),
                    error=f"{type(exc).__name__}: {exc}",
                )
        finally:
            self.governor.release(self.config.resource_key, owner)
            with self._active_lock:
                self._active.pop(run_id, None)

    def _renew_active(self, checked_at: datetime) -> None:
        with self._active_lock:
            active = tuple(self._active.values())
        for item in active:
            if self.governor.renew(
                self.config.resource_key,
                item.owner,
                ttl_seconds=self.config.lease_ttl_seconds,
            ):
                continue
            item.token.cancel()
            self.service.cancel(item.run_id)
            self.service.store.append_event(
                "resource_lease_lost",
                run_id=item.run_id,
                status=DreamExecutionStatus.RUNNING.value,
                message="Dream cancellation was requested because its resource lease was lost.",
                payload={"resource_key": self.config.resource_key},
                created_at_utc=checked_at,
            )

    def _cancel_active(self, message: str) -> None:
        with self._active_lock:
            active = tuple(self._active.values())
        for item in active:
            current = self.service.get_run(item.run_id)
            if current.cancel_requested:
                continue
            self.service.store.append_event(
                "interactive_preemption",
                run_id=item.run_id,
                schedule_id=current.schedule_id,
                window_key=current.window_key,
                status=current.status.value,
                message=message,
                payload={"resource_key": self.config.resource_key},
                created_at_utc=self.clock(),
            )
            self.service.cancel(item.run_id)
            item.token.cancel()

    def _record_schedule_reason(
        self,
        stored: StoredDreamSchedule,
        reason: EligibilityReason,
        checked_at: datetime,
        window_key: str,
    ) -> None:
        schedule_id = stored.schedule.schedule_id
        if self._last_schedule_reason.get(schedule_id) is reason:
            return
        self._last_schedule_reason[schedule_id] = reason
        self.service.store.append_event(
            "schedule_deferred",
            schedule_id=schedule_id,
            window_key=window_key,
            status="deferred",
            message=f"Dream schedule is waiting: {reason.value}.",
            payload={"reason": reason.value},
            created_at_utc=checked_at,
        )

    def _interactive_requested(self) -> bool:
        return self._interactive_busy.is_set() or bool(self.interactive_probe())

    def _has_active(self) -> bool:
        with self._active_lock:
            return bool(self._active)
