from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProjectStatus = Literal["active", "archived"]
RunStatus = Literal[
    "planned",
    "waiting_approval",
    "queued",
    "running",
    "blocked",
    "partial",
    "verifying",
    "complete",
    "failed",
    "cancelled",
    "interrupted",
]
TaskStatus = Literal[
    "pending",
    "ready",
    "waiting_approval",
    "running",
    "blocked",
    "complete",
    "failed",
    "cancelled",
    "skipped",
]
JobStatus = Literal[
    "pending",
    "waiting_approval",
    "queued",
    "running",
    "blocked",
    "complete",
    "failed",
    "cancelled",
    "interrupted",
    "unknown",
]
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled", "expired"]
VerificationVerdict = Literal["pass", "fail", "insufficient_evidence"]


@dataclass(slots=True)
class ProjectSpec:
    name: str
    root_path: str | None = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunSpec:
    project_id: str
    kind: str
    objective: str
    mode: str = "team"
    parent_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoleSpec:
    run_id: str
    role: str
    model: str
    assignment: str
    model_digest: str | None = None
    permissions: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskSpec:
    run_id: str
    title: str
    objective: str
    role_instance_id: str | None = None
    parent_task_id: str | None = None
    position: int = 0
    constraints: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArtifactSpec:
    project_id: str
    run_id: str
    kind: str
    name: str
    task_id: str | None = None
    producer_role_instance_id: str | None = None
    path: str | None = None
    mime_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    status: str = "produced"
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ApprovalSpec:
    run_id: str
    action_kind: str
    target: str
    request: dict[str, Any]
    task_id: str | None = None
    requesting_role_instance_id: str | None = None
    risk: str = "medium"
    reversible: bool = False
    rollback_plan: str = ""


@dataclass(slots=True)
class JobSpec:
    kind: str
    payload: dict[str, Any]
    project_id: str | None = None
    run_id: str | None = None
    priority: int = 100
    idempotency_key: str | None = None
    scheduled_at: str | None = None
