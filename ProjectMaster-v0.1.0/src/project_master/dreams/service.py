from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from project_master.core.cancellation import CancellationToken
from project_master.dreams.inbox import DreamItemFactory, PromotionHandoff
from project_master.dreams.models import (
    DreamDisposition,
    DreamItem,
    DreamPolicy,
    DreamRecipe,
    DreamRecipeKind,
    PromotionTarget,
)
from project_master.dreams.planning import DreamCouncilRequestBuilder, DreamCouncilRunner
from project_master.dreams.scheduling import (
    DreamSchedule,
    QuietWindow,
    ResourceRules,
    ScheduleWindow,
)
from project_master.dreams.snapshots import (
    DreamSource,
    SnapshotBuilder,
    SnapshotPolicy,
)
from project_master.dreams.sources import enforce_recipe_source_scopes
from project_master.dreams.store import (
    DreamExecutionStatus,
    DreamRunEvent,
    DreamRunRecord,
    DreamStore,
    StoredDreamRecipe,
    StoredDreamSchedule,
)
from project_master.team.models import CatalogModel, CouncilStatus

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class DreamExecution:
    run: DreamRunRecord
    item: DreamItem | None
    already_existed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "run": self.run.to_dict(),
            "item": self.item.to_dict() if self.item else None,
            "already_existed": self.already_existed,
        }


BUILTIN_RECIPES = (
    DreamRecipe(
        recipe_id="idea-garden",
        name="Idea Garden",
        kind=DreamRecipeKind.IDEA_GARDEN,
        objective=(
            "Find promising connections, opportunities, and reversible experiments in "
            "the selected project material."
        ),
    ),
    DreamRecipe(
        recipe_id="memory-gardener",
        name="Memory Gardener",
        kind=DreamRecipeKind.MEMORY_GARDENER,
        objective=(
            "Propose memory candidates, contradictions, and stale notes for explicit "
            "human review; do not change memory."
        ),
    ),
    DreamRecipe(
        recipe_id="project-retrospective",
        name="Project Retrospective",
        kind=DreamRecipeKind.PROJECT_RETROSPECTIVE,
        objective=(
            "Extract lessons, unfinished work, risks, and next-step candidates from "
            "project evidence."
        ),
    ),
    DreamRecipe(
        recipe_id="risk-scan",
        name="Risk Scan",
        kind=DreamRecipeKind.RISK_SCAN,
        objective="Identify assumptions, failure modes, privacy risks, and verification needs.",
    ),
    DreamRecipe(
        recipe_id="creator-spark",
        name="Creator Spark",
        kind=DreamRecipeKind.CREATOR_SPARK,
        objective=(
            "Generate original media or product directions as speculative briefs for "
            "human review."
        ),
    ),
)


class DreamService:
    """Cohesive proposal-only execution service with durable idempotency."""

    def __init__(
        self,
        store: DreamStore,
        runner: DreamCouncilRunner,
        *,
        clock: Clock | None = None,
        snapshot_builder: SnapshotBuilder | None = None,
        request_builder: DreamCouncilRequestBuilder | None = None,
        item_factory: DreamItemFactory | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(UTC))
        self.snapshot_builder = snapshot_builder or SnapshotBuilder()
        self.request_builder = request_builder or DreamCouncilRequestBuilder()
        self.item_factory = item_factory or DreamItemFactory()
        self._active: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()
        self.store.ensure_builtin_recipes(
            BUILTIN_RECIPES,
            created_at_utc=self.clock(),
        )

    def list_recipes(self) -> tuple[StoredDreamRecipe, ...]:
        return self.store.list_recipes()

    def save_recipe(
        self,
        recipe: DreamRecipe,
        *,
        expected_version: int | None = None,
    ) -> StoredDreamRecipe:
        return self.store.save_recipe(
            recipe,
            created_at_utc=self.clock(),
            expected_version=expected_version,
        )

    def delete_recipe(self, recipe_id: str) -> None:
        self.store.delete_recipe(recipe_id)

    def save_schedule(
        self,
        schedule: DreamSchedule,
        *,
        resource_rules: ResourceRules | None = None,
        quiet_window: QuietWindow | None = None,
        expected_version: int | None = None,
    ) -> StoredDreamSchedule:
        return self.store.save_schedule(
            schedule,
            resource_rules=resource_rules,
            quiet_window=quiet_window,
            updated_at_utc=self.clock(),
            expected_version=expected_version,
        )

    def list_schedules(self, *, enabled_only: bool = False) -> tuple[StoredDreamSchedule, ...]:
        return self.store.list_schedules(enabled_only=enabled_only)

    def get_schedule(self, schedule_id: str) -> StoredDreamSchedule:
        return self.store.get_schedule(schedule_id)

    def set_schedule_enabled(
        self,
        schedule_id: str,
        enabled: bool,
    ) -> StoredDreamSchedule:
        return self.store.set_schedule_enabled(
            schedule_id,
            enabled,
            updated_at_utc=self.clock(),
        )

    def delete_schedule(self, schedule_id: str) -> None:
        self.store.delete_schedule(schedule_id)

    def queue_manual(
        self,
        *,
        recipe_id: str,
        request_id: str,
        sources: Sequence[DreamSource],
        preferred_lead: str | None = None,
        policy: DreamPolicy | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> DreamExecution:
        effective_policy = policy or DreamPolicy()
        if not effective_policy.manual_enabled:
            raise ValueError("manual Dream Lab runs are disabled by policy")
        return self._queue(
            recipe_id=recipe_id,
            window_key=f"dream:manual:{recipe_id}:{request_id}",
            sources=sources,
            snapshot_policy=snapshot_policy,
            schedule_id=None,
            origin="manual",
            due_at_utc=self.clock(),
            preferred_lead=preferred_lead,
        )

    def queue_scheduled(
        self,
        *,
        window: ScheduleWindow,
        schedule_id: str,
        sources: Sequence[DreamSource],
        policy: DreamPolicy | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
    ) -> DreamExecution:
        effective_policy = policy or DreamPolicy(scheduled_enabled=True)
        if not effective_policy.scheduled_enabled:
            raise ValueError("scheduled Dream Lab runs are disabled by policy")
        return self._queue(
            recipe_id=window.recipe_id,
            window_key=window.window_key,
            sources=sources,
            snapshot_policy=snapshot_policy,
            schedule_id=schedule_id,
            origin=window.origin.value,
            due_at_utc=window.due_at_utc,
            preferred_lead=None,
        )

    def execute_manual(
        self,
        *,
        recipe_id: str,
        request_id: str,
        sources: Sequence[DreamSource],
        models: Sequence[CatalogModel],
        preferred_lead: str | None = None,
        policy: DreamPolicy | None = None,
        snapshot_policy: SnapshotPolicy | None = None,
        cancellation: CancellationToken | None = None,
    ) -> DreamExecution:
        queued = self.queue_manual(
            recipe_id=recipe_id,
            request_id=request_id,
            sources=sources,
            preferred_lead=preferred_lead,
            policy=policy,
            snapshot_policy=snapshot_policy,
        )
        if (
            queued.already_existed
            and queued.run.status is not DreamExecutionStatus.CLAIMED
        ):
            return queued
        return self.execute_queued(
            queued.run.run_id,
            models=models,
            preferred_lead=preferred_lead,
            cancellation=cancellation,
        )

    def execute_queued(
        self,
        run_id: str,
        *,
        models: Sequence[CatalogModel],
        preferred_lead: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> DreamExecution:
        record, started = self.store.start_run(
            run_id,
            started_at_utc=self.clock(),
        )
        if not started:
            return DreamExecution(
                run=record,
                item=self._item_or_none(record.item_id),
                already_existed=True,
            )
        recipe = self.store.get_recipe(record.recipe_id, record.recipe_version)
        snapshot = self.store.snapshot_for_run(record.run_id)
        token = cancellation or CancellationToken()
        with self._lock:
            self._active[record.run_id] = token
        try:
            plan = self.request_builder.build(
                recipe.recipe,
                snapshot,
                window_key=record.window_key,
                council_run_id=record.run_id,
            )
            council = self.runner.run(
                plan,
                models,
                preferred_lead=record.preferred_lead or preferred_lead,
                cancellation=token,
            )
            result = council.result
            status = (
                DreamExecutionStatus.CANCELLED
                if token.cancelled
                else _execution_status(result.status)
            )
            item: DreamItem | None = None
            if status in {
                DreamExecutionStatus.COMPLETE,
                DreamExecutionStatus.PARTIAL,
            }:
                item = self.item_factory.from_council(
                    recipe_id=record.recipe_id,
                    window_key=record.window_key,
                    snapshot=snapshot,
                    result=result,
                    created_at_utc=self.clock(),
                )
            finished = self.store.finish_run(
                record.run_id,
                status=status,
                updated_at_utc=self.clock(),
                council_run_id=result.run_id,
                council_result=result.to_dict(),
                item=item,
                error=result.failure.message if result.failure else None,
            )
            return DreamExecution(
                run=finished,
                item=self._item_or_none(finished.item_id),
            )
        except Exception as exc:
            status = (
                DreamExecutionStatus.CANCELLED
                if token.cancelled
                else DreamExecutionStatus.FAILED
            )
            failed = self.store.finish_run(
                record.run_id,
                status=status,
                updated_at_utc=self.clock(),
                error=f"{type(exc).__name__}: {exc}",
            )
            return DreamExecution(run=failed, item=None)
        finally:
            with self._lock:
                self._active.pop(record.run_id, None)

    def list_events(
        self,
        *,
        run_id: str | None = None,
        schedule_id: str | None = None,
        limit: int = 200,
    ) -> tuple[DreamRunEvent, ...]:
        return self.store.list_events(
            run_id=run_id,
            schedule_id=schedule_id,
            limit=limit,
        )

    def cancel(self, run_id: str) -> DreamRunRecord:
        requested = self.store.request_cancel(
            run_id,
            requested_at_utc=self.clock(),
        )
        with self._lock:
            token = self._active.get(run_id)
        if token is not None:
            token.cancel()
        return requested

    def list_runs(self, limit: int = 100) -> tuple[DreamRunRecord, ...]:
        return self.store.list_runs(limit)

    def get_run(self, run_id: str) -> DreamRunRecord:
        return self.store.get_run(run_id)

    def list_inbox(
        self,
        disposition: DreamDisposition | None = None,
    ) -> tuple[DreamItem, ...]:
        return self.store.list_items(disposition)

    def get_inbox_item(self, item_id: str) -> DreamItem:
        return self.store.get_item(item_id)

    def promote(
        self,
        item_id: str,
        *,
        target: PromotionTarget,
        decided_by: str,
        rationale: str,
    ) -> tuple[DreamItem, PromotionHandoff]:
        return self.store.promote(
            item_id,
            target=target,
            decided_by=decided_by,
            decided_at_utc=self.clock(),
            rationale=rationale,
        )

    def reject(
        self,
        item_id: str,
        *,
        decided_by: str,
        rationale: str,
    ) -> DreamItem:
        return self.store.reject(
            item_id,
            decided_by=decided_by,
            decided_at_utc=self.clock(),
            rationale=rationale,
        )

    def _item_or_none(self, item_id: str | None) -> DreamItem | None:
        return self.store.get_item(item_id) if item_id else None

    def _queue(
        self,
        *,
        recipe_id: str,
        window_key: str,
        sources: Sequence[DreamSource],
        snapshot_policy: SnapshotPolicy | None,
        schedule_id: str | None,
        origin: str,
        due_at_utc: datetime,
        preferred_lead: str | None,
    ) -> DreamExecution:
        recipe = self.store.get_recipe(recipe_id)
        if schedule_id is not None and not recipe.recipe.source_scopes:
            raise ValueError(
                "Scheduled Dream runs require a recipe with explicit source_scopes."
            )
        enforce_recipe_source_scopes(recipe.recipe, sources)
        now = self.clock()
        snapshot = self.snapshot_builder.build(
            tuple(sources),
            policy=snapshot_policy or SnapshotPolicy(),
            captured_at_utc=now,
        )
        if schedule_id is not None and not snapshot.entries:
            raise ValueError(
                "Scheduled Dream runs require at least one consented source "
                "after source policy filtering."
            )
        record, created = self.store.begin_run(
            recipe=recipe,
            window_key=window_key,
            snapshot=snapshot,
            created_at_utc=now,
            initial_status=DreamExecutionStatus.CLAIMED,
            schedule_id=schedule_id,
            origin=origin,
            due_at_utc=due_at_utc,
            preferred_lead=preferred_lead,
        )
        return DreamExecution(
            run=record,
            item=self._item_or_none(record.item_id),
            already_existed=not created,
        )


def _execution_status(status: CouncilStatus) -> DreamExecutionStatus:
    return {
        CouncilStatus.COMPLETE: DreamExecutionStatus.COMPLETE,
        CouncilStatus.PARTIAL: DreamExecutionStatus.PARTIAL,
        CouncilStatus.CANCELLED: DreamExecutionStatus.CANCELLED,
        CouncilStatus.FAILED: DreamExecutionStatus.FAILED,
    }[status]
