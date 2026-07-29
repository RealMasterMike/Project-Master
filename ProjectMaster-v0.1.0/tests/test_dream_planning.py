from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.dreams.models import (
    DreamPolicy,
    DreamRecipe,
    DreamRecipeKind,
    RoleAngle,
)
from project_master.dreams.planning import DreamCouncilRequestBuilder, DreamCouncilRunner
from project_master.dreams.snapshots import (
    DreamSource,
    SnapshotBuilder,
    SnapshotPolicy,
    SourceKind,
    SourceSnapshot,
)
from project_master.team.council import SequentialCouncil
from project_master.team.models import (
    CatalogModel,
    CouncilRequest,
    CouncilStatus,
    ModelDetails,
    TeamRole,
)

CAPTURED = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _snapshot() -> SourceSnapshot:
    return SnapshotBuilder().build(
        [
            DreamSource(
                source_id="project-note",
                kind=SourceKind.PROJECT,
                locator="project://notes",
                content="A possible workflow. password=do-not-leak",
                captured_at_utc=CAPTURED,
            )
        ],
        policy=SnapshotPolicy(),
        captured_at_utc=CAPTURED,
    )


def test_council_plan_uses_all_models_role_angles_and_proposal_only_prompt() -> None:
    snapshot = _snapshot()
    recipe = DreamRecipe(
        recipe_id="creator-spark",
        name="Creator Spark",
        kind=DreamRecipeKind.CREATOR_SPARK,
        objective="Find useful original directions.",
        role_angles=(
            RoleAngle(TeamRole.CREATOR, "Combine distant ideas into three candidates."),
        ),
    )

    plan = DreamCouncilRequestBuilder().build(
        recipe,
        snapshot,
        window_key="dream:manual:creator-spark:click-1",
    )

    assert isinstance(plan.request, CouncilRequest)
    assert plan.use_all_conversational_models is True
    assert plan.proposal_only is True
    assert len(plan.role_angles) == len(TeamRole)
    creator = next(item for item in plan.role_angles if item.role is TeamRole.CREATOR)
    assert creator.instruction == "Combine distant ideas into three candidates."
    assert "Every output remains speculation" in plan.request.prompt
    assert "Do not write memory, perform actions, enqueue media, call tools" in plan.request.prompt
    assert "[SOURCE project-note]" in plan.request.context
    assert "do-not-leak" not in plan.request.context
    assert "[REDACTED:credential]" in plan.request.context
    assert plan.source_refs == ("project-note",)


def test_council_plan_rejects_tampered_snapshot() -> None:
    snapshot = _snapshot()
    tampered = replace(snapshot, snapshot_id="snap_wrong")
    recipe = DreamRecipe(
        recipe_id="risk-scan",
        name="Risk Scan",
        kind=DreamRecipeKind.RISK_SCAN,
        objective="Find risks.",
    )

    with pytest.raises(ValueError, match="invalid provenance"):
        DreamCouncilRequestBuilder().build(
            recipe,
            tampered,
            window_key="dream:manual:risk-scan:1",
        )


def test_dream_runner_passes_every_physical_model_to_sequential_council() -> None:
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, model: str) -> None:
            self.model = model

        def chat_stream(
            self,
            _messages: list[Message],
            tools: list[dict[str, object]] | None = None,
            cancellation: CancellationToken | None = None,
        ) -> Iterator[Message]:
            assert tools is None
            assert cancellation is None
            calls.append(self.model)
            yield Message(role="assistant", content=f"{self.model} proposal")

    models = [
        CatalogModel(
            physical_id="digest:lead",
            tags=("lead",),
            digest="lead",
            size_bytes=12,
            capabilities=frozenset({"completion", "tools"}),
            details=ModelDetails(family="test"),
            automatic_eligible=True,
            curated_purposes=frozenset({"dream"}),
        ),
        CatalogModel(
            physical_id="digest:worker",
            tags=("worker",),
            digest="worker",
            size_bytes=7,
            capabilities=frozenset({"completion"}),
            details=ModelDetails(family="test"),
            automatic_eligible=True,
            curated_purposes=frozenset({"dream"}),
        ),
    ]
    recipe = DreamRecipe(
        recipe_id="all-models",
        name="All Models",
        kind=DreamRecipeKind.IDEA_GARDEN,
        objective="Collect every model's proposal.",
    )
    plan = DreamCouncilRequestBuilder().build(
        recipe,
        _snapshot(),
        window_key="dream:manual:all-models:1",
    )
    runner = DreamCouncilRunner(SequentialCouncil(FakeProvider))

    run = runner.run(plan, models, preferred_lead="lead")

    assert calls == ["worker", "lead"]
    assert {member.member_id for member in run.result.plan.members} == {
        "digest:lead",
        "digest:worker",
    }


def test_dream_runner_forwards_cancellation_without_starting_a_model() -> None:
    calls: list[str] = []

    class UnusedProvider:
        def __init__(self, model: str) -> None:
            calls.append(model)

        def chat_stream(
            self,
            _messages: list[Message],
            tools: list[dict[str, object]] | None = None,
            cancellation: CancellationToken | None = None,
        ) -> Iterator[Message]:
            raise AssertionError("cancelled dream should not start a provider")

    model = CatalogModel(
        physical_id="digest:lead",
        tags=("lead",),
        digest="lead",
        size_bytes=12,
        capabilities=frozenset({"completion", "tools"}),
        details=ModelDetails(family="test"),
        automatic_eligible=True,
        curated_purposes=frozenset({"dream"}),
    )
    recipe = DreamRecipe(
        recipe_id="cancelled",
        name="Cancelled",
        kind=DreamRecipeKind.IDEA_GARDEN,
        objective="Do not run after cancellation.",
    )
    plan = DreamCouncilRequestBuilder().build(
        recipe,
        _snapshot(),
        window_key="dream:manual:cancelled:1",
    )
    token = CancellationToken()
    token.cancel()

    run = DreamCouncilRunner(SequentialCouncil(UnusedProvider)).run(
        plan,
        [model],
        cancellation=token,
    )

    assert calls == []
    assert run.result.status is CouncilStatus.CANCELLED


def test_recipe_rejects_duplicate_role_angles() -> None:
    with pytest.raises(ValueError, match="one angle"):
        DreamRecipe(
            recipe_id="bad",
            name="Bad",
            kind=DreamRecipeKind.CUSTOM,
            objective="Test validation.",
            role_angles=(
                RoleAngle(TeamRole.CRITIC, "First"),
                RoleAngle(TeamRole.CRITIC, "Second"),
            ),
        )


@pytest.mark.parametrize("field", ["auto_memory", "auto_actions", "auto_media"])
def test_dream_policy_cannot_enable_automatic_side_effects(field: str) -> None:
    with pytest.raises(ValueError, match="cannot automatically"):
        DreamPolicy(**{field: True})


def test_dream_policy_cannot_disable_proposal_only_boundary() -> None:
    with pytest.raises(ValueError, match="proposal-only"):
        DreamPolicy(proposal_only=False)
