from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_master.dreams import (
    BUILTIN_RECIPES,
    DreamDisposition,
    DreamExecutionStatus,
    DreamInboxError,
    DreamRecipe,
    DreamRecipeKind,
    DreamService,
    DreamSource,
    DreamStore,
    PromotionTarget,
    SourceKind,
)
from project_master.dreams.snapshots import SnapshotBuilder, SnapshotPolicy
from project_master.memory.store import SQLiteStore
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

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.current = NOW

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


class StubRunner:
    def __init__(
        self,
        status: CouncilStatus = CouncilStatus.COMPLETE,
        *,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.calls = 0

    def run(self, plan, models, *, preferred_lead=None, cancellation=None) -> CouncilRun:
        self.calls += 1
        if self.error is not None:
            raise self.error
        lead = TeamMember(
            member_id="digest:lead",
            role=TeamRole.LEAD,
            model_tag="lead",
            aliases=("lead",),
            capabilities=frozenset({"completion", "tools"}),
            size_bytes=1,
        )
        failure = (
            ProviderFailure("cancelled", "Cancelled cooperatively.")
            if self.status is CouncilStatus.CANCELLED
            else None
        )
        return CouncilRun(
            events=(),
            result=CouncilResult(
                run_id=plan.request.run_id,
                status=self.status,
                final=(
                    "A bounded speculative proposal."
                    if self.status in {CouncilStatus.COMPLETE, CouncilStatus.PARTIAL}
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
        content="An observed constraint and a possible direction.",
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
        automatic_eligible=True,
        curated_purposes=frozenset({"dream"}),
    )


def _service(path: Path, runner: StubRunner, clock: Clock | None = None) -> DreamService:
    return DreamService(
        DreamStore(SQLiteStore(path)),
        runner,  # type: ignore[arg-type]
        clock=clock or Clock(),
    )


def test_manual_run_and_inbox_survive_restart_without_duplicate_execution(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    first_runner = StubRunner()
    first = _service(path, first_runner)

    execution = first.execute_manual(
        recipe_id="idea-garden",
        request_id="button-click-1",
        sources=[_source()],
        models=[_model()],
    )

    assert execution.run.status is DreamExecutionStatus.COMPLETE
    assert execution.item is not None
    assert first_runner.calls == 1

    second_runner = StubRunner()
    restarted = _service(path, second_runner)
    replay = restarted.execute_manual(
        recipe_id="idea-garden",
        request_id="button-click-1",
        sources=[_source()],
        models=[_model()],
    )

    assert replay.already_existed is True
    assert replay.run.run_id == execution.run.run_id
    assert replay.item == execution.item
    assert second_runner.calls == 0
    assert restarted.store.seen_window_keys() == {
        "dream:manual:idea-garden:button-click-1"
    }


def test_recipe_versions_are_durable_and_builtins_are_protected(tmp_path: Path) -> None:
    path = tmp_path / "master.db"
    service = _service(path, StubRunner())
    recipe = DreamRecipe(
        recipe_id="mikes-review",
        name="Mike's Review",
        kind=DreamRecipeKind.CUSTOM,
        objective="Propose the next reversible improvement.",
    )

    version_one = service.save_recipe(recipe, expected_version=0)
    version_two = service.save_recipe(
        DreamRecipe(
            recipe_id=recipe.recipe_id,
            name=recipe.name,
            kind=recipe.kind,
            objective="Propose two reversible improvements and their verification.",
        ),
        expected_version=1,
    )

    assert version_one.version == 1
    assert version_two.version == 2
    restarted = _service(path, StubRunner())
    assert restarted.store.get_recipe(recipe.recipe_id).version == 2
    with pytest.raises(DreamInboxError, match="version conflict"):
        restarted.save_recipe(recipe, expected_version=1)
    with pytest.raises(DreamInboxError, match="built-in"):
        restarted.delete_recipe("idea-garden")


def test_promotion_and_rejection_are_durable_terminal_review_decisions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    service = _service(path, StubRunner())
    first = service.execute_manual(
        recipe_id="idea-garden",
        request_id="promote-me",
        sources=[_source()],
        models=[_model()],
    )
    second = service.execute_manual(
        recipe_id="risk-scan",
        request_id="reject-me",
        sources=[_source()],
        models=[_model()],
    )
    assert first.item is not None
    assert second.item is not None

    promoted, handoff = service.promote(
        first.item.item_id,
        target=PromotionTarget.TASK_CANDIDATE,
        decided_by="mike",
        rationale="Make this a reviewed candidate, not an automatic task.",
    )
    rejected = service.reject(
        second.item.item_id,
        decided_by="mike",
        rationale="Not useful.",
    )

    restarted = _service(path, StubRunner())
    assert restarted.get_inbox_item(promoted.item_id).disposition is DreamDisposition.PROMOTED
    assert restarted.get_inbox_item(rejected.item_id).disposition is DreamDisposition.REJECTED
    assert handoff.target is PromotionTarget.TASK_CANDIDATE
    assert "candidate" in handoff.target.value
    with pytest.raises(DreamInboxError, match="already received"):
        restarted.reject(
            promoted.item_id,
            decided_by="mike",
            rationale="A final decision cannot be rewritten.",
        )


def test_runner_exception_is_persisted_as_failed_without_inbox_item(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    service = _service(path, StubRunner(error=RuntimeError("provider offline")))

    execution = service.execute_manual(
        recipe_id="risk-scan",
        request_id="failure",
        sources=[_source()],
        models=[_model()],
    )

    assert execution.run.status is DreamExecutionStatus.FAILED
    assert execution.run.error == "RuntimeError: provider offline"
    assert execution.item is None
    assert service.list_inbox() == ()
    restarted = _service(path, StubRunner())
    assert restarted.get_run(execution.run.run_id).status is DreamExecutionStatus.FAILED


def test_cooperatively_cancelled_result_is_terminal_without_a_proposal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    service = _service(path, StubRunner(CouncilStatus.CANCELLED))

    execution = service.execute_manual(
        recipe_id="creator-spark",
        request_id="cancelled",
        sources=[_source()],
        models=[_model()],
    )

    assert execution.run.status is DreamExecutionStatus.CANCELLED
    assert execution.run.error == "Cancelled cooperatively."
    assert execution.item is None
    assert service.list_inbox() == ()


def test_restart_marks_unfinished_run_interrupted_and_preserves_claim(
    tmp_path: Path,
) -> None:
    path = tmp_path / "master.db"
    clock = Clock()
    first_store = DreamStore(SQLiteStore(path))
    first_store.ensure_builtin_recipes(
        (BUILTIN_RECIPES[0],),
        created_at_utc=clock(),
    )
    recipe = first_store.get_recipe("idea-garden")
    snapshot = SnapshotBuilder().build(
        [_source()],
        policy=SnapshotPolicy(),
        captured_at_utc=clock(),
    )
    running, created = first_store.begin_run(
        recipe=recipe,
        window_key="dream:manual:idea-garden:crashed",
        snapshot=snapshot,
        created_at_utc=clock(),
    )
    assert created is True
    assert running.status is DreamExecutionStatus.RUNNING

    restarted = DreamStore(SQLiteStore(path))
    recovered = restarted.get_run(running.run_id)
    assert recovered.status is DreamExecutionStatus.INTERRUPTED
    assert "stopped before" in (recovered.error or "")
    assert restarted.seen_window_keys() == {
        "dream:manual:idea-garden:crashed"
    }
