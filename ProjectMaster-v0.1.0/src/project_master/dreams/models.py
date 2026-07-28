from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from project_master.team.models import TeamRole

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _require_identifier(value: str, name: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(
            f"{name} must be 1-128 characters using letters, numbers, '.', '_', or '-'"
        )
    return normalized


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


class DreamRecipeKind(StrEnum):
    IDEA_GARDEN = "idea_garden"
    MEMORY_GARDENER = "memory_gardener"
    PROJECT_RETROSPECTIVE = "project_retrospective"
    RISK_SCAN = "risk_scan"
    CREATOR_SPARK = "creator_spark"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class RoleAngle:
    role: TeamRole
    instruction: str

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("role angle instruction must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role.value, "instruction": self.instruction}


@dataclass(frozen=True, slots=True)
class DreamRecipe:
    recipe_id: str
    name: str
    kind: DreamRecipeKind
    objective: str
    role_angles: tuple[RoleAngle, ...] = ()
    source_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe_id", _require_identifier(self.recipe_id, "recipe_id"))
        if not self.name.strip():
            raise ValueError("recipe name must not be empty")
        if not self.objective.strip():
            raise ValueError("recipe objective must not be empty")
        roles = [angle.role for angle in self.role_angles]
        if len(roles) != len(set(roles)):
            raise ValueError("a dream recipe may define only one angle per team role")
        scopes = tuple(scope.strip() for scope in self.source_scopes)
        if any(not scope for scope in scopes):
            raise ValueError("dream recipe source scopes must not be empty")
        if len(scopes) != len(set(scopes)):
            raise ValueError("dream recipe source scopes must be unique")
        object.__setattr__(self, "source_scopes", scopes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "name": self.name,
            "kind": self.kind.value,
            "objective": self.objective,
            "role_angles": [item.to_dict() for item in self.role_angles],
            "source_scopes": list(self.source_scopes),
        }


@dataclass(frozen=True, slots=True)
class DreamPolicy:
    """Safety and availability policy for manual and app-owned dream runs."""

    manual_enabled: bool = True
    scheduled_enabled: bool = False
    manual_bypasses_idle: bool = True
    proposal_only: bool = True
    auto_memory: bool = False
    auto_actions: bool = False
    auto_media: bool = False

    def __post_init__(self) -> None:
        if not self.proposal_only:
            raise ValueError("Dream Lab is proposal-only")
        if self.auto_memory or self.auto_actions or self.auto_media:
            raise ValueError(
                "Dream Lab cannot automatically write memory, perform actions, or enqueue media"
            )

    def to_dict(self) -> dict[str, bool]:
        return {
            "manual_enabled": self.manual_enabled,
            "scheduled_enabled": self.scheduled_enabled,
            "manual_bypasses_idle": self.manual_bypasses_idle,
            "proposal_only": self.proposal_only,
            "auto_memory": self.auto_memory,
            "auto_actions": self.auto_actions,
            "auto_media": self.auto_media,
        }


class DreamRunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DreamDisposition(StrEnum):
    PENDING = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class EpistemicLabel(StrEnum):
    SPECULATION = "speculation"
    INFERENCE = "inference"
    SOURCE_CLAIM = "source_claim"
    VERIFIED = "verified"


class PromotionTarget(StrEnum):
    PROJECT_IDEA_CANDIDATE = "project_idea_candidate"
    MEMORY_CANDIDATE = "memory_candidate"
    TASK_CANDIDATE = "task_candidate"
    MEDIA_BRIEF_CANDIDATE = "media_brief_candidate"


class DecisionKind(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DreamDecision:
    kind: DecisionKind
    decided_by: str
    decided_at_utc: datetime
    rationale: str
    target: PromotionTarget | None = None

    def __post_init__(self) -> None:
        if not self.decided_by.strip():
            raise ValueError("decided_by must not be empty")
        if not self.rationale.strip():
            raise ValueError("decision rationale must not be empty")
        object.__setattr__(
            self,
            "decided_at_utc",
            _utc(self.decided_at_utc, "decided_at_utc"),
        )
        if self.kind is DecisionKind.PROMOTE and self.target is None:
            raise ValueError("promotion decisions require a candidate target")
        if self.kind is DecisionKind.REJECT and self.target is not None:
            raise ValueError("rejection decisions cannot have a promotion target")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "decided_by": self.decided_by,
            "decided_at_utc": self.decided_at_utc.isoformat(),
            "rationale": self.rationale,
            "target": self.target.value if self.target else None,
        }


@dataclass(frozen=True, slots=True)
class DreamItem:
    item_id: str
    recipe_id: str
    window_key: str
    council_run_id: str
    snapshot_id: str
    proposal_text: str
    run_status: DreamRunStatus
    epistemic_label: EpistemicLabel
    source_refs: tuple[str, ...]
    created_at_utc: datetime
    partial_reason: str | None = None
    disposition: DreamDisposition = DreamDisposition.PENDING
    decision: DreamDecision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _require_identifier(self.item_id, "item_id"))
        object.__setattr__(self, "recipe_id", _require_identifier(self.recipe_id, "recipe_id"))
        if not self.window_key.strip():
            raise ValueError("window_key must not be empty")
        if not self.council_run_id.strip():
            raise ValueError("council_run_id must not be empty")
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id must not be empty")
        object.__setattr__(
            self,
            "created_at_utc",
            _utc(self.created_at_utc, "created_at_utc"),
        )
        if self.run_status in {DreamRunStatus.COMPLETE, DreamRunStatus.PARTIAL}:
            if not self.proposal_text.strip():
                raise ValueError("complete or partial dream items require proposal text")
        if self.run_status is DreamRunStatus.PARTIAL and not self.partial_reason:
            raise ValueError("partial dream items require a partial_reason")
        if self.disposition is DreamDisposition.PENDING and self.decision is not None:
            raise ValueError("pending dream items cannot contain a decision")
        if self.disposition is DreamDisposition.PROMOTED:
            if self.decision is None or self.decision.kind is not DecisionKind.PROMOTE:
                raise ValueError("promoted dream items require a promotion decision")
        if self.disposition is DreamDisposition.REJECTED:
            if self.decision is None or self.decision.kind is not DecisionKind.REJECT:
                raise ValueError("rejected dream items require a rejection decision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "recipe_id": self.recipe_id,
            "window_key": self.window_key,
            "council_run_id": self.council_run_id,
            "snapshot_id": self.snapshot_id,
            "proposal_text": self.proposal_text,
            "run_status": self.run_status.value,
            "epistemic_label": self.epistemic_label.value,
            "source_refs": list(self.source_refs),
            "created_at_utc": self.created_at_utc.isoformat(),
            "partial_reason": self.partial_reason,
            "disposition": self.disposition.value,
            "decision": self.decision.to_dict() if self.decision else None,
        }
