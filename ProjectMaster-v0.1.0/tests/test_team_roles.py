from __future__ import annotations

from project_master.team.models import CatalogModel, ModelDetails, TeamRole
from project_master.team.roles import (
    CapabilityAwareRoleAssigner,
    recommend_conversational_model,
)


def _model(
    tag: str,
    *,
    digest: str,
    capabilities: set[str],
    size: int,
    aliases: tuple[str, ...] | None = None,
    automatic_eligible: bool = True,
) -> CatalogModel:
    return CatalogModel(
        physical_id=f"digest:{digest}",
        tags=aliases or (tag,),
        digest=digest,
        size_bytes=size,
        capabilities=frozenset(capabilities),
        details=ModelDetails(family="test"),
        automatic_eligible=automatic_eligible,
        curated_purposes=(
            frozenset({"chat", "team", "dream"})
            if automatic_eligible
            else frozenset()
        ),
    )


def test_role_assignment_prefers_tool_capable_lead_and_uses_requested_alias() -> None:
    models = [
        _model(
            "operator:12b",
            digest="operator",
            capabilities={"completion", "tools"},
            size=12_000,
            aliases=("operator:12b", "operator:latest"),
        ),
        _model(
            "preferred:20b",
            digest="preferred",
            capabilities={"completion", "thinking"},
            size=20_000,
        ),
        _model(
            "vision:7b",
            digest="vision",
            capabilities={"completion", "vision"},
            size=7_000,
        ),
        _model(
            "embed:latest",
            digest="embed",
            capabilities={"embedding"},
            size=1_000,
        ),
    ]

    plan = CapabilityAwareRoleAssigner().assign(
        models,
        preferred_lead="operator:latest",
    )

    assert plan.lead is not None
    assert plan.lead.model_tag == "operator:latest"
    assert plan.lead.aliases == ("operator:12b", "operator:latest")
    roles = {worker.model_tag: worker.role for worker in plan.workers}
    assert roles["preferred:20b"] is TeamRole.VERIFIER
    assert roles["vision:7b"] is TeamRole.VISUAL_ANALYST
    assert [item.model.primary_tag for item in plan.excluded] == ["embed:latest"]


def test_role_assignment_uses_largest_thinking_tool_model_when_no_preference_matches() -> None:
    models = [
        _model(
            "small-tools",
            digest="small",
            capabilities={"completion", "tools"},
            size=5,
        ),
        _model(
            "large-tools",
            digest="large",
            capabilities={"completion", "tools", "thinking"},
            size=10,
        ),
        _model(
            "larger-no-tools",
            digest="larger",
            capabilities={"completion", "thinking"},
            size=20,
        ),
    ]

    plan = CapabilityAwareRoleAssigner().assign(models, preferred_lead="not-installed")

    assert plan.lead is not None
    assert plan.lead.model_tag == "large-tools"


def test_role_assignment_returns_an_explicit_empty_plan_for_non_chat_models() -> None:
    embedding = _model(
        "embed",
        digest="embed",
        capabilities={"embedding"},
        size=1,
    )

    plan = CapabilityAwareRoleAssigner().assign([embedding])

    assert plan.lead is None
    assert plan.workers == ()
    assert plan.excluded[0].reason.startswith("model does not report")


def test_role_assignment_excludes_manual_unverified_models() -> None:
    curated = _model(
        "curated",
        digest="curated",
        capabilities={"completion", "tools"},
        size=1,
    )
    manual = _model(
        "looks-uncensored",
        digest="manual",
        capabilities={"completion", "tools"},
        size=100,
        automatic_eligible=False,
    )

    plan = CapabilityAwareRoleAssigner().assign(
        [manual, curated],
        preferred_lead="looks-uncensored",
    )

    assert plan.lead is not None
    assert plan.lead.model_tag == "curated"
    assert plan.workers == ()
    assert plan.excluded[0].model.primary_tag == "looks-uncensored"
    assert "not curated for automatic team use" in plan.excluded[0].reason


def test_role_assignment_enforces_the_requested_curated_purpose() -> None:
    team_only = _model(
        "team-only",
        digest="team-only",
        capabilities={"completion", "tools"},
        size=1,
    )
    dream_only = CatalogModel(
        physical_id="digest:dream-only",
        tags=("dream-only",),
        digest="dream-only",
        size_bytes=2,
        capabilities=frozenset({"completion", "tools"}),
        details=ModelDetails(family="test"),
        automatic_eligible=True,
        curated_purposes=frozenset({"dream"}),
    )

    team_plan = CapabilityAwareRoleAssigner().assign([dream_only, team_only])
    dream_plan = CapabilityAwareRoleAssigner().assign(
        [team_only, dream_only],
        required_purpose="dream",
    )

    assert team_plan.lead is not None
    assert team_plan.lead.model_tag == "team-only"
    assert dream_plan.lead is not None
    assert dream_plan.lead.model_tag == "dream-only"


def test_model_recommendation_preserves_valid_config_then_uses_lead_policy() -> None:
    models = [
        _model(
            "configured-chat",
            digest="configured",
            capabilities={"completion"},
            size=20,
        ),
        _model(
            "small-tools",
            digest="small",
            capabilities={"completion", "tools"},
            size=5,
        ),
        _model(
            "large-thinking-tools",
            digest="large",
            capabilities={"completion", "tools", "thinking"},
            size=10,
        ),
        _model(
            "embedding",
            digest="embedding",
            capabilities={"embedding"},
            size=100,
        ),
        _model(
            "manual-unverified",
            digest="manual",
            capabilities={"completion", "tools", "thinking"},
            size=1_000,
            automatic_eligible=False,
        ),
    ]

    assert recommend_conversational_model(models, "configured-chat") == "configured-chat"
    assert (
        recommend_conversational_model(models, "missing-config")
        == "large-thinking-tools"
    )
    assert (
        recommend_conversational_model(models, "manual-unverified")
        == "large-thinking-tools"
    )
    assert recommend_conversational_model([models[-2]], "embedding") is None
