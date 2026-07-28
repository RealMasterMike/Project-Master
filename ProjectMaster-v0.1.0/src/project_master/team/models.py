from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_COMPLETION_CAPABILITIES = frozenset({"chat", "completion", "generate"})
_TOOL_CAPABILITIES = frozenset({"tool", "tools", "tool_calling"})


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    """Bound user/model-controlled text while making truncation observable."""
    if len(value) <= limit:
        return value, False
    marker = "\n[truncated]"
    if limit <= len(marker):
        return marker[:limit], True
    return f"{value[: limit - len(marker)]}{marker}", True


@dataclass(frozen=True, slots=True)
class ModelDetails:
    family: str = ""
    families: tuple[str, ...] = ()
    parameter_size: str = ""
    quantization_level: str = ""
    format: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "families": list(self.families),
            "parameter_size": self.parameter_size,
            "quantization_level": self.quantization_level,
            "format": self.format,
        }


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One physical Ollama model with every installed tag that resolves to it."""

    physical_id: str
    tags: tuple[str, ...]
    digest: str | None
    size_bytes: int
    capabilities: frozenset[str] = field(default_factory=frozenset)
    details: ModelDetails = field(default_factory=ModelDetails)
    modified_at: str | None = None
    inspection_error: str | None = None

    def __post_init__(self) -> None:
        if not self.tags:
            raise ValueError("catalog models must preserve at least one tag")
        if self.size_bytes < 0:
            raise ValueError("model size must not be negative")

    @property
    def primary_tag(self) -> str:
        return self.tags[0]

    @property
    def supports_completion(self) -> bool:
        normalized = {item.casefold() for item in self.capabilities}
        if normalized & _COMPLETION_CAPABILITIES:
            return True
        if normalized:
            return False
        # Older Ollama versions did not report capabilities. Preserve compatibility while
        # preventing a known embedding family from being treated as a chat worker.
        family_tokens = {
            self.details.family.casefold(),
            *(item.casefold() for item in self.details.families),
        }
        return not any("embed" in item for item in family_tokens)

    @property
    def supports_tools(self) -> bool:
        normalized = {item.casefold() for item in self.capabilities}
        return bool(normalized & _TOOL_CAPABILITIES)

    def has_capability(self, capability: str) -> bool:
        expected = capability.casefold()
        return any(item.casefold() == expected for item in self.capabilities)

    def tag_for(self, preferred: str | None = None) -> str:
        if preferred:
            expected = preferred.casefold()
            for tag in self.tags:
                if tag.casefold() == expected:
                    return tag
        return self.primary_tag

    def to_dict(self) -> dict[str, Any]:
        return {
            "physical_id": self.physical_id,
            "tags": list(self.tags),
            "primary_tag": self.primary_tag,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "capabilities": sorted(self.capabilities),
            "details": self.details.to_dict(),
            "modified_at": self.modified_at,
            "inspection_error": self.inspection_error,
        }


class TeamRole(StrEnum):
    LEAD = "lead"
    RESEARCHER = "researcher"
    BUILDER = "builder"
    CREATOR = "creator"
    CRITIC = "critic"
    VERIFIER = "verifier"
    VISUAL_ANALYST = "visual_analyst"


@dataclass(frozen=True, slots=True)
class TeamMember:
    member_id: str
    role: TeamRole
    model_tag: str
    aliases: tuple[str, ...]
    capabilities: frozenset[str]
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role": self.role.value,
            "model": self.model_tag,
            "aliases": list(self.aliases),
            "capabilities": sorted(self.capabilities),
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ExcludedModel:
    model: CatalogModel
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model.to_dict(), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class TeamPlan:
    lead: TeamMember | None
    workers: tuple[TeamMember, ...]
    excluded: tuple[ExcludedModel, ...] = ()

    @property
    def members(self) -> tuple[TeamMember, ...]:
        if self.lead is None:
            return self.workers
        return (self.lead, *self.workers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead": self.lead.to_dict() if self.lead else None,
            "workers": [item.to_dict() for item in self.workers],
            "excluded": [item.to_dict() for item in self.excluded],
        }


class WorkerStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class WorkerResult:
    member: TeamMember
    status: WorkerStatus
    output: str = ""
    output_truncated: bool = False
    failure: ProviderFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "member": self.member.to_dict(),
            "status": self.status.value,
            "output": self.output,
            "output_truncated": self.output_truncated,
            "failure": self.failure.to_dict() if self.failure else None,
        }


class CouncilStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CouncilResult:
    run_id: str
    status: CouncilStatus
    final: str
    final_truncated: bool
    plan: TeamPlan
    workers: tuple[WorkerResult, ...]
    failure: ProviderFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "final": self.final,
            "final_truncated": self.final_truncated,
            "plan": self.plan.to_dict(),
            "workers": [item.to_dict() for item in self.workers],
            "failure": self.failure.to_dict() if self.failure else None,
        }


class ActivityKind(StrEnum):
    COUNCIL_STARTED = "council_started"
    MODEL_SKIPPED = "model_skipped"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    WORKER_FAILED = "worker_failed"
    WORKER_CANCELLED = "worker_cancelled"
    SYNTHESIS_STARTED = "synthesis_started"
    SYNTHESIS_COMPLETED = "synthesis_completed"
    SYNTHESIS_FAILED = "synthesis_failed"
    COUNCIL_COMPLETED = "council_completed"
    COUNCIL_CANCELLED = "council_cancelled"


@dataclass(frozen=True, slots=True)
class TeamActivityEvent:
    """A stable, UI-safe activity event. It never contains token-level private reasoning."""

    run_id: str
    sequence: int
    kind: ActivityKind
    message: str
    member: TeamMember | None = None
    worker: WorkerResult | None = None
    result: CouncilResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "type": self.kind.value,
            "message": self.message,
            "member": self.member.to_dict() if self.member else None,
            "worker": self.worker.to_dict() if self.worker else None,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass(frozen=True, slots=True)
class CouncilLimits:
    max_models: int = 64
    max_request_chars: int = 16_000
    max_context_chars: int = 16_000
    max_worker_output_chars: int = 4_000
    max_synthesis_context_chars: int = 32_000
    max_final_output_chars: int = 12_000
    max_error_chars: int = 500

    def __post_init__(self) -> None:
        for name in (
            "max_models",
            "max_request_chars",
            "max_context_chars",
            "max_worker_output_chars",
            "max_synthesis_context_chars",
            "max_final_output_chars",
            "max_error_chars",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class CouncilRequest:
    prompt: str
    context: str = ""
    run_id: str | None = None

    def bounded(self, limits: CouncilLimits) -> tuple[str, str, bool]:
        prompt, prompt_truncated = _bounded_text(self.prompt.strip(), limits.max_request_chars)
        context, context_truncated = _bounded_text(self.context.strip(), limits.max_context_chars)
        return prompt, context, prompt_truncated or context_truncated


@dataclass(frozen=True, slots=True)
class CouncilRun:
    events: tuple[TeamActivityEvent, ...]
    result: CouncilResult
