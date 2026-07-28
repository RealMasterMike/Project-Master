from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from project_master.integrations.comfyui.jobs import (
    ComfyJob,
    JobConflictError,
    JobNotFoundError,
    JobRepository,
)
from project_master.integrations.comfyui.profiles import ComfyUIProfile
from project_master.integrations.comfyui.workflow import WorkflowRevision
from project_master.memory.store import SQLiteStore


class StoredWorkflow(BaseModel):
    """An immutable workflow revision plus its explicit local trust decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: WorkflowRevision
    trust_state: Literal["pending", "approved", "rejected"] = "pending"
    decision_note: str = ""
    decided_at: datetime | None = None


class SQLiteComfyStore(JobRepository):
    """Durable ComfyUI profiles, workflows, and optimistic job state."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS comfy_profiles (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comfy_workflows (
                    id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL,
                    name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    trust_state TEXT NOT NULL DEFAULT 'pending',
                    decision_note TEXT NOT NULL DEFAULT '',
                    decided_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comfy_jobs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    workflow_revision_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_comfy_jobs_created
                    ON comfy_jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_comfy_jobs_profile
                    ON comfy_jobs(profile_id, created_at DESC);
                """
            )

    def upsert_profile(self, profile: ComfyUIProfile) -> ComfyUIProfile:
        validated = ComfyUIProfile.model_validate(profile.model_dump())
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO comfy_profiles(id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.id,
                    validated.model_dump_json(),
                    _now(),
                ),
            )
        return validated.model_copy(deep=True)

    def get_profile(self, profile_id: str) -> ComfyUIProfile:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM comfy_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown ComfyUI profile: {profile_id}")
        return ComfyUIProfile.model_validate_json(str(row["payload_json"]))

    def list_profiles(self) -> tuple[ComfyUIProfile, ...]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT payload_json FROM comfy_profiles ORDER BY id").fetchall()
        return tuple(ComfyUIProfile.model_validate_json(str(row["payload_json"])) for row in rows)

    def save_workflow(self, revision: WorkflowRevision) -> StoredWorkflow:
        validated = WorkflowRevision.model_validate(revision.model_dump())
        with self.store.connection() as conn:
            existing = conn.execute(
                "SELECT digest FROM comfy_workflows WHERE id = ?",
                (validated.id,),
            ).fetchone()
            if existing is not None and str(existing["digest"]) != validated.digest:
                raise ValueError("ComfyUI workflow revision ID collision.")
            conn.execute(
                """
                INSERT INTO comfy_workflows(
                    id, digest, name, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    validated.id,
                    validated.digest,
                    validated.name,
                    validated.model_dump_json(),
                    validated.created_at.isoformat(),
                ),
            )
        return self.get_workflow(validated.id)

    def get_workflow(self, revision_id: str) -> StoredWorkflow:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT payload_json, trust_state, decision_note, decided_at
                FROM comfy_workflows
                WHERE id = ?
                """,
                (revision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown ComfyUI workflow revision: {revision_id}")
        decided_at = (
            datetime.fromisoformat(str(row["decided_at"]))
            if row["decided_at"] is not None
            else None
        )
        return StoredWorkflow(
            revision=WorkflowRevision.model_validate_json(str(row["payload_json"])),
            trust_state=str(row["trust_state"]),
            decision_note=str(row["decision_note"]),
            decided_at=decided_at,
        )

    def list_workflows(self) -> tuple[StoredWorkflow, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT id FROM comfy_workflows ORDER BY created_at DESC, id"
            ).fetchall()
        return tuple(self.get_workflow(str(row["id"])) for row in rows)

    def decide_workflow(
        self,
        revision_id: str,
        trust_state: Literal["approved", "rejected"],
        note: str = "",
    ) -> StoredWorkflow:
        with self.store.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE comfy_workflows
                SET trust_state = ?, decision_note = ?, decided_at = ?
                WHERE id = ?
                """,
                (trust_state, note[:4_000], _now(), revision_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown ComfyUI workflow revision: {revision_id}")
        return self.get_workflow(revision_id)

    def create(self, job: ComfyJob) -> ComfyJob:
        validated = ComfyJob.model_validate(job.model_dump())
        try:
            with self.store.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO comfy_jobs(
                        id, profile_id, workflow_revision_id, status, version,
                        payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        validated.id,
                        validated.profile_id,
                        validated.workflow_revision_id,
                        validated.status.value,
                        validated.version,
                        validated.model_dump_json(),
                        validated.created_at.isoformat(),
                        validated.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise JobConflictError(f"ComfyUI job {validated.id!r} already exists.") from exc
        return validated.model_copy(deep=True)

    def get(self, job_id: str) -> ComfyJob:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM comfy_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(f"ComfyUI job {job_id!r} does not exist.")
        return ComfyJob.model_validate_json(str(row["payload_json"]))

    def save(self, job: ComfyJob, *, expected_version: int) -> ComfyJob:
        validated = ComfyJob.model_validate(job.model_dump())
        if validated.version != expected_version:
            raise JobConflictError(f"ComfyUI job {validated.id!r} changed during this operation.")
        saved = ComfyJob.model_validate(
            validated.model_copy(
                update={"version": expected_version + 1},
                deep=True,
            ).model_dump()
        )
        with self.store.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE comfy_jobs
                SET status = ?, version = ?, payload_json = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    saved.status.value,
                    saved.version,
                    saved.model_dump_json(),
                    saved.updated_at.isoformat(),
                    saved.id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "SELECT 1 FROM comfy_jobs WHERE id = ?",
                    (saved.id,),
                ).fetchone()
                if exists is None:
                    raise JobNotFoundError(f"ComfyUI job {saved.id!r} does not exist.")
                raise JobConflictError(f"ComfyUI job {saved.id!r} changed during this operation.")
        return saved.model_copy(deep=True)

    def list(self, *, profile_id: str | None = None) -> tuple[ComfyJob, ...]:
        sql = "SELECT payload_json FROM comfy_jobs"
        params: tuple[Any, ...] = ()
        if profile_id is not None:
            sql += " WHERE profile_id = ?"
            params = (profile_id,)
        sql += " ORDER BY created_at DESC, id"
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(ComfyJob.model_validate_json(str(row["payload_json"])) for row in rows)


def _now() -> str:
    return datetime.now(UTC).isoformat()
