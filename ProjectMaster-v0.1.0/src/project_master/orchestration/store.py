from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import (
    ApprovalSpec,
    ArtifactSpec,
    JobSpec,
    ProjectSpec,
    RoleSpec,
    RunSpec,
    TaskSpec,
)

_RUN_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"waiting_approval", "queued", "running", "cancelled"},
    "waiting_approval": {"queued", "cancelled", "blocked"},
    "queued": {"running", "cancelled", "blocked"},
    "running": {
        "blocked",
        "partial",
        "verifying",
        "complete",
        "failed",
        "cancelled",
        "interrupted",
    },
    "blocked": {"queued", "running", "failed", "cancelled"},
    "partial": {"queued", "verifying", "complete", "failed", "cancelled"},
    "verifying": {"complete", "failed", "partial", "cancelled"},
    "interrupted": {"queued", "failed", "cancelled"},
    "complete": set(),
    "failed": {"queued"},
    "cancelled": set(),
}

_TASK_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "waiting_approval", "blocked", "cancelled", "skipped"},
    "ready": {"waiting_approval", "running", "blocked", "cancelled", "skipped"},
    "waiting_approval": {"ready", "running", "blocked", "cancelled"},
    "running": {"blocked", "complete", "failed", "cancelled"},
    "blocked": {"ready", "running", "failed", "cancelled", "skipped"},
    "failed": {"ready", "cancelled"},
    "complete": set(),
    "cancelled": set(),
    "skipped": set(),
}

_JOB_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"waiting_approval", "queued", "cancelled"},
    "waiting_approval": {"queued", "cancelled", "blocked"},
    "queued": {"running", "cancelled", "blocked"},
    "running": {"complete", "failed", "cancelled", "interrupted", "unknown"},
    "blocked": {"queued", "cancelled", "failed"},
    "interrupted": {"queued", "failed", "cancelled"},
    "unknown": {"complete", "failed", "cancelled", "queued"},
    "failed": {"queued"},
    "complete": set(),
    "cancelled": set(),
}

_PROJECT_TYPES = frozenset({"general", "creator"})


class OrchestrationStore:
    """Project-scoped, append-friendly state layered on the existing SQLite database."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_path TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    parent_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    stop_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS role_instances (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    model TEXT NOT NULL,
                    model_digest TEXT,
                    assignment TEXT NOT NULL,
                    permissions_json TEXT NOT NULL DEFAULT '[]',
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'waiting',
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS run_tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    parent_task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    position INTEGER NOT NULL DEFAULT 0,
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    required_tools_json TEXT NOT NULL DEFAULT '[]',
                    completion_criteria_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS context_packets (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    objective TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    from_role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    to_role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    packet_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    producer_role_instance_id TEXT REFERENCES role_instances(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT,
                    mime_type TEXT,
                    sha256 TEXT,
                    size_bytes INTEGER,
                    status TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    requesting_role_instance_id TEXT
                        REFERENCES role_instances(id) ON DELETE SET NULL,
                    action_kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    reversible INTEGER NOT NULL,
                    rollback_plan TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    decision_note TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS verifications (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES run_tasks(id) ON DELETE SET NULL,
                    verifier_role_instance_id TEXT
                        REFERENCES role_instances(id) ON DELETE SET NULL,
                    verdict TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,
                    findings_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS durable_jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    scheduled_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS resource_leases (
                    resource_key TEXT PRIMARY KEY,
                    job_id TEXT REFERENCES durable_jobs(id) ON DELETE CASCADE,
                    owner TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_runs_project_created
                    ON runs(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_run_position
                    ON run_tasks(run_id, position, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_run_id
                    ON run_events(run_id, id);
                CREATE INDEX IF NOT EXISTS idx_jobs_state_priority
                    ON durable_jobs(state, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_approvals_status
                    ON approvals(status, created_at);
                """
            )

    def create_project(self, spec: ProjectSpec) -> str:
        if spec.project_type not in _PROJECT_TYPES:
            raise ValueError(f"Unsupported project type: {spec.project_type}")
        project_id = _id("project")
        now = _now()
        metadata = dict(spec.metadata)
        metadata["project_type"] = spec.project_type
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO projects(
                    id, name, root_path, description, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    spec.name,
                    spec.root_path,
                    spec.description,
                    _json(metadata),
                    now,
                    now,
                ),
            )
        return project_id

    def get_or_create_project(self, spec: ProjectSpec) -> str:
        if spec.project_type not in _PROJECT_TYPES:
            raise ValueError(f"Unsupported project type: {spec.project_type}")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, metadata_json FROM projects
                WHERE name = ? AND COALESCE(root_path, '') = COALESCE(?, '')
                ORDER BY created_at
                """,
                (spec.name, spec.root_path),
            ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"]))
            except (json.JSONDecodeError, TypeError):
                metadata = {}
            project_type = (
                metadata.get("project_type")
                if isinstance(metadata, dict)
                else None
            )
            if (project_type or "general") == spec.project_type:
                return str(row["id"])
        return self.create_project(spec)

    def list_projects(
        self,
        include_archived: bool = False,
        project_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if project_type is not None and project_type not in _PROJECT_TYPES:
            raise ValueError(f"Unsupported project type: {project_type}")
        sql = "SELECT * FROM projects"
        params: tuple[Any, ...] = ()
        if not include_archived:
            sql += " WHERE status = ?"
            params = ("active",)
        sql += " ORDER BY updated_at DESC"
        projects = [_with_project_type(item) for item in self._rows(sql, params)]
        if project_type is not None:
            projects = [
                item for item in projects if item["project_type"] == project_type
            ]
        return projects

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        project = self._row("SELECT * FROM projects WHERE id = ?", (project_id,))
        return _with_project_type(project) if project is not None else None

    def set_project_dreaming(
        self,
        project_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Set explicit Dream source consent without replacing unrelated metadata."""
        if not isinstance(enabled, bool):
            raise TypeError("Project Dream consent must be a boolean.")
        now = _now()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT metadata_json FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown project: {project_id}")
            try:
                metadata = json.loads(str(row["metadata_json"]))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("Project metadata is not valid JSON.") from exc
            if not isinstance(metadata, dict):
                raise ValueError("Project metadata must be a JSON object.")
            metadata["allow_dreaming"] = enabled
            conn.execute(
                """
                UPDATE projects
                SET metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_json(metadata), now, project_id),
            )
            updated = conn.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if updated is None:  # pragma: no cover - protected by the write transaction.
            raise RuntimeError("Project Dream consent update was not persisted.")
        return _with_project_type(_decode_row(dict(updated)))

    def create_run(self, spec: RunSpec) -> str:
        self._require_project(spec.project_id)
        run_id = _id("run")
        now = _now()
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                    id, project_id, parent_run_id, kind, objective, mode, status,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)
                """,
                (
                    run_id,
                    spec.project_id,
                    spec.parent_run_id,
                    spec.kind,
                    spec.objective,
                    spec.mode,
                    _json(spec.metadata),
                    now,
                    now,
                ),
            )
        self.append_event(run_id, "run_created", spec.objective, {"mode": spec.mode})
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM runs WHERE id = ?", (run_id,))

    def list_runs(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, min(max(limit, 1), 500)),
        )

    def set_run_status(self, run_id: str, status: str, reason: str | None = None) -> None:
        current = self._require_status("runs", run_id)
        _validate_transition("run", current, status, _RUN_TRANSITIONS)
        now = _now()
        started_at = now if status == "running" and current != "running" else None
        terminal = status in {"complete", "failed", "cancelled", "interrupted"}
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, stop_reason = COALESCE(?, stop_reason), updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (status, reason, now, started_at, int(terminal), now, run_id),
            )
        self.append_event(
            run_id,
            "run_status",
            f"{current} → {status}",
            {"from": current, "to": status, "reason": reason},
        )

    def add_role(self, spec: RoleSpec) -> str:
        self._require_run(spec.run_id)
        role_id = _id("role")
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO role_instances(
                    id, run_id, role, model, model_digest, assignment,
                    permissions_json, budget_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role_id,
                    spec.run_id,
                    spec.role,
                    spec.model,
                    spec.model_digest,
                    spec.assignment,
                    _json(spec.permissions),
                    _json(spec.budget),
                ),
            )
        self.append_event(
            spec.run_id,
            "role_added",
            f"{spec.role}: {spec.assignment}",
            {"role_instance_id": role_id, "model": spec.model},
            role_instance_id=role_id,
        )
        return role_id

    def set_role_status(self, role_id: str, status: str) -> None:
        now = _now()
        started = now if status == "working" else None
        complete = now if status in {"done", "failed", "cancelled"} else None
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT run_id FROM role_instances WHERE id = ?", (role_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown role instance: {role_id}")
            conn.execute(
                """
                UPDATE role_instances
                SET status = ?, started_at = COALESCE(started_at, ?),
                    completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status, started, complete, role_id),
            )
            run_id = str(row["run_id"])
        self.append_event(
            run_id,
            "role_status",
            status,
            {"role_instance_id": role_id, "status": status},
            role_instance_id=role_id,
        )

    def list_roles(self, run_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM role_instances WHERE run_id = ? ORDER BY rowid", (run_id,)
        )

    def create_task(self, spec: TaskSpec) -> str:
        self._require_run(spec.run_id)
        task_id = _id("task")
        now = _now()
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO run_tasks(
                    id, run_id, parent_task_id, role_instance_id, title, objective,
                    position, constraints_json, required_tools_json,
                    completion_criteria_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    spec.run_id,
                    spec.parent_task_id,
                    spec.role_instance_id,
                    spec.title,
                    spec.objective,
                    spec.position,
                    _json(spec.constraints),
                    _json(spec.required_tools),
                    _json(spec.completion_criteria),
                    now,
                    now,
                ),
            )
        self.append_event(
            spec.run_id,
            "task_created",
            spec.title,
            {"task_id": task_id, "objective": spec.objective},
            task_id=task_id,
            role_instance_id=spec.role_instance_id,
        )
        return task_id

    def set_task_status(
        self, task_id: str, status: str, result: dict[str, Any] | None = None
    ) -> None:
        row = self._row("SELECT run_id, status FROM run_tasks WHERE id = ?", (task_id,))
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        current = str(row["status"])
        _validate_transition("task", current, status, _TASK_TRANSITIONS)
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE run_tasks
                SET status = ?, result_json = COALESCE(?, result_json), updated_at = ?
                WHERE id = ?
                """,
                (status, _json(result) if result is not None else None, _now(), task_id),
            )
        self.append_event(
            str(row["run_id"]),
            "task_status",
            f"{current} → {status}",
            {"task_id": task_id, "from": current, "to": status},
            task_id=task_id,
        )

    def list_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM run_tasks WHERE run_id = ? ORDER BY position, created_at", (run_id,)
        )

    def append_event(
        self,
        run_id: str,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        role_instance_id: str | None = None,
    ) -> int:
        with self.store.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO run_events(
                    run_id, task_id, role_instance_id, event_type,
                    summary, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    role_instance_id,
                    event_type,
                    summary,
                    _json(payload or {}),
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(self, run_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT * FROM run_events
            WHERE run_id = ? AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (run_id, max(after_id, 0), min(max(limit, 1), 2_000)),
        )

    def save_context_packet(
        self,
        run_id: str,
        objective: str,
        context: dict[str, Any],
        source_refs: list[dict[str, Any]],
        *,
        task_id: str | None = None,
        role_instance_id: str | None = None,
    ) -> str:
        packet_id = _id("context")
        canonical = _json({"context": context, "source_refs": source_refs})
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO context_packets(
                    id, run_id, task_id, role_instance_id, objective,
                    context_json, source_refs_json, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet_id,
                    run_id,
                    task_id,
                    role_instance_id,
                    objective,
                    _json(context),
                    _json(source_refs),
                    digest,
                    _now(),
                ),
            )
        return packet_id

    def add_handoff(
        self,
        run_id: str,
        packet: dict[str, Any],
        *,
        task_id: str | None = None,
        from_role_instance_id: str | None = None,
        to_role_instance_id: str | None = None,
    ) -> str:
        handoff_id = _id("handoff")
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO handoffs(
                    id, run_id, task_id, from_role_instance_id,
                    to_role_instance_id, packet_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    run_id,
                    task_id,
                    from_role_instance_id,
                    to_role_instance_id,
                    _json(packet),
                    _now(),
                ),
            )
        self.append_event(
            run_id,
            "handoff",
            str(packet.get("objective", "Structured handoff")),
            {"handoff_id": handoff_id},
            task_id=task_id,
            role_instance_id=from_role_instance_id,
        )
        return handoff_id

    def add_artifact(self, spec: ArtifactSpec) -> str:
        artifact_id = _id("artifact")
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(
                    id, project_id, run_id, task_id, producer_role_instance_id,
                    kind, name, path, mime_type, sha256, size_bytes, status,
                    provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    spec.project_id,
                    spec.run_id,
                    spec.task_id,
                    spec.producer_role_instance_id,
                    spec.kind,
                    spec.name,
                    spec.path,
                    spec.mime_type,
                    spec.sha256,
                    spec.size_bytes,
                    spec.status,
                    _json(spec.provenance),
                    _now(),
                ),
            )
        self.append_event(
            spec.run_id,
            "artifact",
            spec.name,
            {"artifact_id": artifact_id, "kind": spec.kind, "status": spec.status},
            task_id=spec.task_id,
            role_instance_id=spec.producer_role_instance_id,
        )
        return artifact_id

    def list_artifacts(
        self, project_id: str, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        if run_id:
            return self._rows(
                """
                SELECT * FROM artifacts
                WHERE project_id = ? AND run_id = ?
                ORDER BY created_at DESC
                """,
                (project_id, run_id),
            )
        return self._rows(
            "SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )

    def request_approval(self, spec: ApprovalSpec) -> str:
        approval_id = _id("approval")
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO approvals(
                    id, run_id, task_id, requesting_role_instance_id,
                    action_kind, target, request_json, risk, reversible,
                    rollback_plan, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    spec.run_id,
                    spec.task_id,
                    spec.requesting_role_instance_id,
                    spec.action_kind,
                    spec.target,
                    _json(spec.request),
                    spec.risk,
                    int(spec.reversible),
                    spec.rollback_plan,
                    _now(),
                ),
            )
        self.append_event(
            spec.run_id,
            "approval_requested",
            f"{spec.action_kind}: {spec.target}",
            {"approval_id": approval_id, "risk": spec.risk},
            task_id=spec.task_id,
            role_instance_id=spec.requesting_role_instance_id,
        )
        return approval_id

    def resolve_approval(self, approval_id: str, status: str, note: str = "") -> None:
        if status not in {"approved", "rejected", "cancelled", "expired"}:
            raise ValueError(f"Invalid approval resolution: {status}")
        row = self._row("SELECT run_id, status FROM approvals WHERE id = ?", (approval_id,))
        if row is None:
            raise KeyError(f"Unknown approval: {approval_id}")
        if row["status"] != "pending":
            raise ValueError(f"Approval {approval_id} is already {row['status']}")
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET status = ?, decision_note = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, note, _now(), approval_id),
            )
        self.append_event(
            str(row["run_id"]),
            "approval_resolved",
            status,
            {"approval_id": approval_id, "status": status, "note": note},
        )

    def list_approvals(
        self, status: str | None = "pending", run_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        sql = "SELECT * FROM approvals"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at"
        return self._rows(sql, tuple(params))

    def add_verification(
        self,
        run_id: str,
        verdict: str,
        criteria: list[str],
        findings: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        *,
        task_id: str | None = None,
        verifier_role_instance_id: str | None = None,
    ) -> str:
        if verdict not in {"pass", "fail", "insufficient_evidence"}:
            raise ValueError(f"Invalid verification verdict: {verdict}")
        verification_id = _id("verification")
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO verifications(
                    id, run_id, task_id, verifier_role_instance_id, verdict,
                    criteria_json, findings_json, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    run_id,
                    task_id,
                    verifier_role_instance_id,
                    verdict,
                    _json(criteria),
                    _json(findings),
                    _json(evidence),
                    _now(),
                ),
            )
        self.append_event(
            run_id,
            "verification",
            verdict,
            {"verification_id": verification_id, "verdict": verdict},
            task_id=task_id,
            role_instance_id=verifier_role_instance_id,
        )
        return verification_id

    def enqueue_job(self, spec: JobSpec) -> str:
        if spec.idempotency_key:
            existing = self._row(
                "SELECT id FROM durable_jobs WHERE idempotency_key = ?",
                (spec.idempotency_key,),
            )
            if existing is not None:
                return str(existing["id"])
        job_id = _id("job")
        now = _now()
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO durable_jobs(
                    id, project_id, run_id, kind, state, priority, idempotency_key,
                    payload_json, scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    spec.project_id,
                    spec.run_id,
                    spec.kind,
                    spec.priority,
                    spec.idempotency_key,
                    _json(spec.payload),
                    spec.scheduled_at,
                    now,
                    now,
                ),
            )
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM durable_jobs WHERE id = ?", (job_id,))

    def list_jobs(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if state is None:
            return self._rows(
                "SELECT * FROM durable_jobs ORDER BY priority, created_at LIMIT ?",
                (min(max(limit, 1), 500),),
            )
        return self._rows(
            """
            SELECT * FROM durable_jobs
            WHERE state = ?
            ORDER BY priority, created_at
            LIMIT ?
            """,
            (state, min(max(limit, 1), 500)),
        )

    def set_job_state(
        self,
        job_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        current = self._require_status("durable_jobs", job_id, column="state")
        _validate_transition("job", current, state, _JOB_TRANSITIONS)
        now = _now()
        started = now if state == "running" else None
        terminal = state in {"complete", "failed", "cancelled"}
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE durable_jobs
                SET state = ?, result_json = COALESCE(?, result_json),
                    error_json = COALESCE(?, error_json), updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
                    attempt = attempt + CASE WHEN ? = 'running' THEN 1 ELSE 0 END
                WHERE id = ?
                """,
                (
                    state,
                    _json(result) if result is not None else None,
                    _json(error) if error is not None else None,
                    now,
                    started,
                    int(terminal),
                    now,
                    state,
                    job_id,
                ),
            )

    def _require_project(self, project_id: str) -> None:
        if self.get_project(project_id) is None:
            raise KeyError(f"Unknown project: {project_id}")

    def _require_run(self, run_id: str) -> None:
        if self.get_run(run_id) is None:
            raise KeyError(f"Unknown run: {run_id}")

    def _require_status(self, table: str, item_id: str, column: str = "status") -> str:
        allowed = {"runs", "run_tasks", "durable_jobs"}
        if table not in allowed or column not in {"status", "state"}:
            raise ValueError("Invalid status lookup")
        row = self._row(f"SELECT {column} FROM {table} WHERE id = ?", (item_id,))
        if row is None:
            raise KeyError(f"Unknown {table.rstrip('s')}: {item_id}")
        return str(row[column])

    def _row(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return _decode_row(dict(row)) if row is not None else None

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode_row(dict(row)) for row in rows]


def _decode_row(item: dict[str, Any]) -> dict[str, Any]:
    for key in tuple(item):
        if key.endswith("_json") and item[key] is not None:
            raw = item.pop(key)
            try:
                item[key.removesuffix("_json")] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[key.removesuffix("_json")] = raw
    if "reversible" in item:
        item["reversible"] = bool(item["reversible"])
    return item


def _with_project_type(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    raw_type = metadata.get("project_type") if isinstance(metadata, dict) else None
    item["project_type"] = raw_type if raw_type in _PROJECT_TYPES else "general"
    return item


def _validate_transition(
    kind: str, current: str, target: str, transitions: dict[str, set[str]]
) -> None:
    if current == target:
        return
    if current not in transitions:
        raise ValueError(f"Unknown {kind} state: {current}")
    if target not in transitions[current]:
        raise ValueError(f"Invalid {kind} transition: {current} → {target}")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _now() -> str:
    return datetime.now(UTC).isoformat()
