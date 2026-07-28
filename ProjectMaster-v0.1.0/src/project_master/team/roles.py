from __future__ import annotations

from collections.abc import Sequence

from project_master.team.models import (
    CatalogModel,
    ExcludedModel,
    TeamMember,
    TeamPlan,
    TeamRole,
)


class CapabilityAwareRoleAssigner:
    """Assign one role per physical conversational model, with a tool-capable lead."""

    _GENERALIST_ROLES = (
        TeamRole.RESEARCHER,
        TeamRole.CREATOR,
        TeamRole.CRITIC,
        TeamRole.VERIFIER,
        TeamRole.BUILDER,
    )

    def assign(
        self,
        models: Sequence[CatalogModel],
        preferred_lead: str | None = None,
    ) -> TeamPlan:
        eligible: list[CatalogModel] = []
        excluded: list[ExcludedModel] = []
        seen_physical_ids: set[str] = set()
        for model in models:
            if model.physical_id in seen_physical_ids:
                excluded.append(
                    ExcludedModel(model=model, reason="duplicate physical model identity")
                )
                continue
            seen_physical_ids.add(model.physical_id)
            if not model.supports_completion:
                excluded.append(
                    ExcludedModel(
                        model=model,
                        reason="model does not report a conversational completion capability",
                    )
                )
                continue
            eligible.append(model)

        if not eligible:
            return TeamPlan(lead=None, workers=(), excluded=tuple(excluded))

        lead_model = self._choose_lead(eligible, preferred_lead)
        lead = self._member(lead_model, TeamRole.LEAD, preferred_lead)
        workers: list[TeamMember] = []
        role_counts = {role: 0 for role in TeamRole}
        for model in sorted(
            (item for item in eligible if item.physical_id != lead_model.physical_id),
            key=lambda item: item.primary_tag.casefold(),
        ):
            role = self._choose_worker_role(model, role_counts)
            role_counts[role] += 1
            workers.append(self._member(model, role))
        return TeamPlan(lead=lead, workers=tuple(workers), excluded=tuple(excluded))

    @staticmethod
    def _choose_lead(
        eligible: Sequence[CatalogModel],
        preferred_lead: str | None,
    ) -> CatalogModel:
        tool_capable = [model for model in eligible if model.supports_tools]
        pool = tool_capable or list(eligible)
        preferred = preferred_lead.casefold() if preferred_lead else None

        def score(model: CatalogModel) -> tuple[int, int, int, str]:
            preferred_match = int(
                preferred is not None and any(tag.casefold() == preferred for tag in model.tags)
            )
            thinking = int(model.has_capability("thinking"))
            return (-preferred_match, -thinking, -model.size_bytes, model.primary_tag.casefold())

        return min(pool, key=score)

    def _choose_worker_role(
        self,
        model: CatalogModel,
        role_counts: dict[TeamRole, int],
    ) -> TeamRole:
        if model.has_capability("vision") and role_counts[TeamRole.VISUAL_ANALYST] == 0:
            return TeamRole.VISUAL_ANALYST
        if model.has_capability("thinking"):
            if role_counts[TeamRole.VERIFIER] == 0:
                return TeamRole.VERIFIER
            if role_counts[TeamRole.CRITIC] == 0:
                return TeamRole.CRITIC
        if model.supports_tools and role_counts[TeamRole.BUILDER] == 0:
            return TeamRole.BUILDER
        return min(
            self._GENERALIST_ROLES,
            key=lambda role: (role_counts[role], self._GENERALIST_ROLES.index(role)),
        )

    @staticmethod
    def _member(
        model: CatalogModel,
        role: TeamRole,
        preferred_tag: str | None = None,
    ) -> TeamMember:
        return TeamMember(
            member_id=model.physical_id,
            role=role,
            model_tag=model.tag_for(preferred_tag),
            aliases=model.tags,
            capabilities=model.capabilities,
            size_bytes=model.size_bytes,
        )
