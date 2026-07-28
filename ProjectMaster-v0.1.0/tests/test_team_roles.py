from __future__ import annotations

from project_master.team.models import CatalogModel, ModelDetails, TeamRole
from project_master.team.roles import CapabilityAwareRoleAssigner


def _model(
    tag: str,
    *,
    digest: str,
    capabilities: set[str],
    size: int,
    aliases: tuple[str, ...] | None = None,
) -> CatalogModel:
    return CatalogModel(
        physical_id=f"digest:{digest}",
        tags=aliases or (tag,),
        digest=digest,
        size_bytes=size,
        capabilities=frozenset(capabilities),
        details=ModelDetails(family="test"),
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
