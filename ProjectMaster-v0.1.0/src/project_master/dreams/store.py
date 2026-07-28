from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from project_master.dreams.inbox import DreamInboxError, PromotionHandoff
from project_master.dreams.models import (
    DecisionKind,
    DreamDecision,
    DreamDisposition,
    DreamItem,
    DreamRecipe,
    DreamRecipeKind,
    DreamRunStatus,
    EpistemicLabel,
    PromotionTarget,
    RoleAngle,
    _utc,
)
from project_master.dreams.provenance import ProvenanceValidator
from project_master.dreams.scheduling import (
    CatchUpMode,
    DreamSchedule,
    QuietWindow,
    ResourceRules,
    ScheduleOwner,
)
from project_master.dreams.snapshots import (
    SnapshotEntry,
    SnapshotExclusion,
    SourceKind,
    SourceSnapshot,
)
from project_master.memory.store import SQLiteStore
from project_master.team.models import TeamRole


class DreamExecutionStatus(StrEnum):
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class StoredDreamSchedule:
    schedule: DreamSchedule
    resource_rules: ResourceRules
    quiet_window: QuietWindow | None
    version: int
    created_at_utc: datetime
    updated_at_utc: datetime

    @property
    def enabled(self) -> bool:
        return self.schedule.enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.schedule.to_dict(),
            "resource_rules": self.resource_rules.to_dict(),
            "quiet_window": self.quiet_window.to_dict() if self.quiet_window else None,
            "version": self.version,
            "updated_at_utc": self.updated_at_utc.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class StoredDreamRecipe:
    recipe: DreamRecipe
    version: int
    created_at_utc: datetime
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.recipe.to_dict(),
            "version": self.version,
            "created_at_utc": self.created_at_utc.isoformat(),
            "builtin": self.builtin,
        }


@dataclass(frozen=True, slots=True)
class DreamRunRecord:
    run_id: str
    recipe_id: str
    recipe_version: int
    window_key: str
    snapshot_id: str
    status: DreamExecutionStatus
    created_at_utc: datetime
    updated_at_utc: datetime
    council_run_id: str | None = None
    item_id: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    schedule_id: str | None = None
    origin: str = "manual"
    due_at_utc: datetime | None = None
    preferred_lead: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "window_key": self.window_key,
            "snapshot_id": self.snapshot_id,
            "status": self.status.value,
            "created_at_utc": self.created_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "council_run_id": self.council_run_id,
            "item_id": self.item_id,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "schedule_id": self.schedule_id,
            "origin": self.origin,
            "due_at_utc": self.due_at_utc.isoformat() if self.due_at_utc else None,
            "preferred_lead": self.preferred_lead,
        }


@dataclass(frozen=True, slots=True)
class DreamRunEvent:
    event_id: int
    event_type: str
    created_at_utc: datetime
    run_id: str | None = None
    schedule_id: str | None = None
    window_key: str | None = None
    status: str | None = None
    message: str = ""
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "created_at_utc": self.created_at_utc.isoformat(),
            "run_id": self.run_id,
            "schedule_id": self.schedule_id,
            "window_key": self.window_key,
            "status": self.status,
            "message": self.message,
            "payload": self.payload or {},
        }


class DreamStore:
    """Durable Dream Lab state sharing Project Master's application database."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._initialize()
        self.recover_interrupted()

    def _initialize(self) -> None:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dream_recipes (
                    recipe_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    recipe_json TEXT NOT NULL,
                    builtin INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(recipe_id, version)
                );

                CREATE TABLE IF NOT EXISTS dream_window_claims (
                    window_key TEXT PRIMARY KEY,
                    recipe_id TEXT,
                    run_id TEXT,
                    schedule_id TEXT,
                    origin TEXT NOT NULL DEFAULT 'manual',
                    due_at TEXT,
                    status TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dream_runs (
                    run_id TEXT PRIMARY KEY,
                    recipe_id TEXT NOT NULL,
                    recipe_version INTEGER NOT NULL,
                    window_key TEXT NOT NULL UNIQUE,
                    snapshot_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    council_run_id TEXT,
                    council_result_json TEXT,
                    item_id TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    schedule_id TEXT,
                    origin TEXT NOT NULL DEFAULT 'manual',
                    due_at TEXT,
                    preferred_lead TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dream_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    recipe_id TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    resource_rules_json TEXT NOT NULL,
                    quiet_window_json TEXT,
                    enabled INTEGER NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dream_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT REFERENCES dream_runs(run_id) ON DELETE CASCADE,
                    schedule_id TEXT,
                    window_key TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dream_inbox_items (
                    item_id TEXT PRIMARY KEY,
                    window_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL UNIQUE REFERENCES dream_runs(run_id),
                    disposition TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dream_recipes_latest
                    ON dream_recipes(recipe_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_dream_runs_created
                    ON dream_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_dream_schedules_enabled
                    ON dream_schedules(enabled, schedule_id);
                CREATE INDEX IF NOT EXISTS idx_dream_run_events_run
                    ON dream_run_events(run_id, id);
                CREATE INDEX IF NOT EXISTS idx_dream_run_events_schedule
                    ON dream_run_events(schedule_id, id);
                CREATE INDEX IF NOT EXISTS idx_dream_inbox_disposition
                    ON dream_inbox_items(disposition, created_at DESC);
                """
            )
            _ensure_column(conn, "dream_window_claims", "schedule_id", "TEXT")
            _ensure_column(
                conn,
                "dream_window_claims",
                "origin",
                "TEXT NOT NULL DEFAULT 'manual'",
            )
            _ensure_column(conn, "dream_window_claims", "due_at", "TEXT")
            _ensure_column(conn, "dream_runs", "schedule_id", "TEXT")
            _ensure_column(
                conn,
                "dream_runs",
                "origin",
                "TEXT NOT NULL DEFAULT 'manual'",
            )
            _ensure_column(conn, "dream_runs", "due_at", "TEXT")
            _ensure_column(conn, "dream_runs", "preferred_lead", "TEXT")

    def ensure_builtin_recipes(
        self,
        recipes: tuple[DreamRecipe, ...],
        *,
        created_at_utc: datetime,
    ) -> None:
        now = _iso(created_at_utc)
        with self.store.connection() as conn:
            for recipe in recipes:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dream_recipes(
                        recipe_id, version, recipe_json, builtin, created_at
                    ) VALUES (?, 1, ?, 1, ?)
                    """,
                    (recipe.recipe_id, _json(recipe.to_dict()), now),
                )

    def save_recipe(
        self,
        recipe: DreamRecipe,
        *,
        created_at_utc: datetime,
        expected_version: int | None = None,
    ) -> StoredDreamRecipe:
        now = _utc(created_at_utc, "created_at_utc")
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            latest = conn.execute(
                """
                SELECT version, builtin FROM dream_recipes
                WHERE recipe_id = ? AND deleted = 0
                ORDER BY version DESC LIMIT 1
                """,
                (recipe.recipe_id,),
            ).fetchone()
            if latest is not None and bool(latest["builtin"]):
                raise DreamInboxError("built-in dream recipes are immutable")
            current = int(latest["version"]) if latest is not None else 0
            if expected_version is not None and expected_version != current:
                raise DreamInboxError(
                    f"dream recipe version conflict: expected {expected_version}, found {current}"
                )
            version = current + 1
            conn.execute(
                """
                INSERT INTO dream_recipes(
                    recipe_id, version, recipe_json, builtin, created_at
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (recipe.recipe_id, version, _json(recipe.to_dict()), now.isoformat()),
            )
        return StoredDreamRecipe(recipe, version, now)

    def get_recipe(
        self,
        recipe_id: str,
        version: int | None = None,
    ) -> StoredDreamRecipe:
        version_clause = "AND version = ?" if version is not None else ""
        active_clause = "" if version is not None else "AND deleted = 0"
        params: tuple[object, ...] = (
            (recipe_id, version) if version is not None else (recipe_id,)
        )
        with self.store.connection() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM dream_recipes
                WHERE recipe_id = ? {active_clause} {version_clause}
                ORDER BY version DESC LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            raise DreamInboxError(f"unknown dream recipe: {recipe_id}")
        return _stored_recipe(row)

    def list_recipes(self) -> tuple[StoredDreamRecipe, ...]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT recipe_id, MAX(version) AS version
                FROM dream_recipes
                WHERE deleted = 0
                GROUP BY recipe_id
                ORDER BY recipe_id
                """
            ).fetchall()
        return tuple(
            self.get_recipe(str(row["recipe_id"]), int(row["version"])) for row in rows
        )

    def delete_recipe(self, recipe_id: str) -> None:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT builtin FROM dream_recipes
                WHERE recipe_id = ? AND deleted = 0
                ORDER BY version DESC LIMIT 1
                """,
                (recipe_id,),
            ).fetchone()
            if row is None:
                raise DreamInboxError(f"unknown dream recipe: {recipe_id}")
            if bool(row["builtin"]):
                raise DreamInboxError("built-in dream recipes cannot be deleted")
            conn.execute(
                "UPDATE dream_recipes SET deleted = 1 WHERE recipe_id = ?",
                (recipe_id,),
            )

    def save_schedule(
        self,
        schedule: DreamSchedule,
        *,
        resource_rules: ResourceRules | None = None,
        quiet_window: QuietWindow | None = None,
        updated_at_utc: datetime,
        expected_version: int | None = None,
    ) -> StoredDreamSchedule:
        recipe = self.get_recipe(schedule.recipe_id)
        if schedule.enabled and not recipe.recipe.source_scopes:
            raise ValueError(
                "Enabled Dream schedules require a recipe with explicit source_scopes."
            )
        now = _utc(updated_at_utc, "updated_at_utc")
        rules = resource_rules or ResourceRules()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT version, created_at FROM dream_schedules WHERE schedule_id = ?",
                (schedule.schedule_id,),
            ).fetchone()
            current_version = int(current["version"]) if current is not None else 0
            if expected_version is not None and expected_version != current_version:
                raise DreamInboxError(
                    "dream schedule version conflict: "
                    f"expected {expected_version}, found {current_version}"
                )
            version = current_version + 1
            created_at = (
                str(current["created_at"])
                if current is not None
                else schedule.created_at_utc.isoformat()
            )
            conn.execute(
                """
                INSERT INTO dream_schedules(
                    schedule_id, recipe_id, schedule_json, resource_rules_json,
                    quiet_window_json, enabled, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    recipe_id = excluded.recipe_id,
                    schedule_json = excluded.schedule_json,
                    resource_rules_json = excluded.resource_rules_json,
                    quiet_window_json = excluded.quiet_window_json,
                    enabled = excluded.enabled,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    schedule.schedule_id,
                    schedule.recipe_id,
                    _json(schedule.to_dict()),
                    _json(rules.to_dict()),
                    _json(quiet_window.to_dict()) if quiet_window else None,
                    int(schedule.enabled),
                    version,
                    created_at,
                    now.isoformat(),
                ),
            )
        return StoredDreamSchedule(
            schedule=schedule,
            resource_rules=rules,
            quiet_window=quiet_window,
            version=version,
            created_at_utc=_datetime(created_at),
            updated_at_utc=now,
        )

    def get_schedule(self, schedule_id: str) -> StoredDreamSchedule:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dream_schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        if row is None:
            raise DreamInboxError(f"unknown dream schedule: {schedule_id}")
        return _stored_schedule(row)

    def list_schedules(self, *, enabled_only: bool = False) -> tuple[StoredDreamSchedule, ...]:
        sql = "SELECT * FROM dream_schedules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY schedule_id"
        with self.store.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return tuple(_stored_schedule(row) for row in rows)

    def set_schedule_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        *,
        updated_at_utc: datetime,
    ) -> StoredDreamSchedule:
        current = self.get_schedule(schedule_id)
        if not enabled:
            now = _utc(updated_at_utc, "updated_at_utc")
            schedule = replace(current.schedule, enabled=False)
            with self.store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """
                    UPDATE dream_schedules
                    SET schedule_json = ?, enabled = 0, version = ?, updated_at = ?
                    WHERE schedule_id = ? AND version = ?
                    """,
                    (
                        _json(schedule.to_dict()),
                        current.version + 1,
                        now.isoformat(),
                        schedule_id,
                        current.version,
                    ),
                )
            if cursor.rowcount != 1:
                raise DreamInboxError("dream schedule changed while it was being paused")
            return StoredDreamSchedule(
                schedule=schedule,
                resource_rules=current.resource_rules,
                quiet_window=current.quiet_window,
                version=current.version + 1,
                created_at_utc=current.created_at_utc,
                updated_at_utc=now,
            )
        return self.save_schedule(
            replace(current.schedule, enabled=enabled),
            resource_rules=current.resource_rules,
            quiet_window=current.quiet_window,
            updated_at_utc=updated_at_utc,
            expected_version=current.version,
        )

    def delete_schedule(self, schedule_id: str) -> None:
        with self.store.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM dream_schedules WHERE schedule_id = ?",
                (schedule_id,),
            )
        if cursor.rowcount != 1:
            raise DreamInboxError(f"unknown dream schedule: {schedule_id}")

    def seen_window_keys(self) -> frozenset[str]:
        with self.store.connection() as conn:
            rows = conn.execute("SELECT window_key FROM dream_window_claims").fetchall()
        return frozenset(str(row["window_key"]) for row in rows)

    def claim(self, window_key: str, claimed_at_utc: datetime) -> bool:
        now = _iso(claimed_at_utc)
        with self.store.connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO dream_window_claims(
                    window_key, status, claimed_at, updated_at
                ) VALUES (?, 'claimed', ?, ?)
                """,
                (window_key, now, now),
            )
        return cursor.rowcount == 1

    def begin_run(
        self,
        *,
        recipe: StoredDreamRecipe,
        window_key: str,
        snapshot: SourceSnapshot,
        created_at_utc: datetime,
        initial_status: DreamExecutionStatus = DreamExecutionStatus.RUNNING,
        schedule_id: str | None = None,
        origin: str = "manual",
        due_at_utc: datetime | None = None,
        preferred_lead: str | None = None,
    ) -> tuple[DreamRunRecord, bool]:
        if initial_status not in {
            DreamExecutionStatus.CLAIMED,
            DreamExecutionStatus.RUNNING,
        }:
            raise ValueError("new dream runs must start claimed or running")
        if origin not in {"manual", "scheduled", "catch_up"}:
            raise ValueError("dream run origin must be manual, scheduled, or catch_up")
        if schedule_id is not None and not schedule_id.strip():
            raise ValueError("schedule_id must not be empty")
        if preferred_lead is not None and not preferred_lead.strip():
            raise ValueError("preferred_lead must not be empty")
        now = _utc(created_at_utc, "created_at_utc")
        due_at = _utc(due_at_utc, "due_at_utc") if due_at_utc else None
        run_id = f"dreamrun_{uuid4().hex}"
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM dream_runs WHERE window_key = ?",
                (window_key,),
            ).fetchone()
            if existing is not None:
                return _run_record(existing), False
            claimed = conn.execute(
                "SELECT run_id FROM dream_window_claims WHERE window_key = ?",
                (window_key,),
            ).fetchone()
            if claimed is not None:
                linked = claimed["run_id"]
                if linked:
                    row = conn.execute(
                        "SELECT * FROM dream_runs WHERE run_id = ?",
                        (linked,),
                    ).fetchone()
                    if row is not None:
                        return _run_record(row), False
                    raise DreamInboxError("dream window claim references a missing run")
            conn.execute(
                """
                INSERT INTO dream_runs(
                    run_id, recipe_id, recipe_version, window_key, snapshot_id,
                    snapshot_json, status, schedule_id, origin, due_at, preferred_lead,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    recipe.recipe.recipe_id,
                    recipe.version,
                    window_key,
                    snapshot.snapshot_id,
                    _json(snapshot.to_dict()),
                    initial_status.value,
                    schedule_id,
                    origin,
                    due_at.isoformat() if due_at else None,
                    preferred_lead,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            if claimed is None:
                conn.execute(
                    """
                    INSERT INTO dream_window_claims(
                        window_key, recipe_id, run_id, schedule_id, origin, due_at,
                        status, claimed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        window_key,
                        recipe.recipe.recipe_id,
                        run_id,
                        schedule_id,
                        origin,
                        due_at.isoformat() if due_at else None,
                        initial_status.value,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE dream_window_claims SET
                        recipe_id = ?, run_id = ?, schedule_id = ?, origin = ?,
                        due_at = ?, status = ?, updated_at = ?
                    WHERE window_key = ? AND run_id IS NULL
                    """,
                    (
                        recipe.recipe.recipe_id,
                        run_id,
                        schedule_id,
                        origin,
                        due_at.isoformat() if due_at else None,
                        initial_status.value,
                        now.isoformat(),
                        window_key,
                    ),
                )
            _insert_event(
                conn,
                run_id=run_id,
                schedule_id=schedule_id,
                window_key=window_key,
                event_type=(
                    "run_claimed"
                    if initial_status is DreamExecutionStatus.CLAIMED
                    else "run_started"
                ),
                status=initial_status.value,
                message=(
                    "Dream run claimed for background execution."
                    if initial_status is DreamExecutionStatus.CLAIMED
                    else "Dream run started."
                ),
                payload={"origin": origin},
                created_at=now.isoformat(),
            )
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to read newly created dream run")
        return _run_record(row), True

    def start_run(
        self,
        run_id: str,
        *,
        started_at_utc: datetime,
    ) -> tuple[DreamRunRecord, bool]:
        now = _iso(started_at_utc)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise DreamInboxError(f"unknown dream run: {run_id}")
            current = DreamExecutionStatus(str(row["status"]))
            if current is not DreamExecutionStatus.CLAIMED:
                return _run_record(row), False
            if bool(row["cancel_requested"]):
                conn.execute(
                    """
                    UPDATE dream_runs
                    SET status = 'cancelled', updated_at = ?
                    WHERE run_id = ? AND status = 'claimed'
                    """,
                    (now, run_id),
                )
                conn.execute(
                    """
                    UPDATE dream_window_claims
                    SET status = 'cancelled', updated_at = ?
                    WHERE window_key = ?
                    """,
                    (now, str(row["window_key"])),
                )
                _insert_event(
                    conn,
                    run_id=run_id,
                    schedule_id=(
                        str(row["schedule_id"]) if row["schedule_id"] else None
                    ),
                    window_key=str(row["window_key"]),
                    event_type="run_cancelled",
                    status=DreamExecutionStatus.CANCELLED.value,
                    message="Dream run was cancelled before background execution started.",
                    payload={},
                    created_at=now,
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE dream_runs
                    SET status = 'running', updated_at = ?
                    WHERE run_id = ? AND status = 'claimed'
                    """,
                    (now, run_id),
                )
                if cursor.rowcount != 1:
                    latest = conn.execute(
                        "SELECT * FROM dream_runs WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    if latest is None:
                        raise RuntimeError("dream run disappeared while starting")
                    return _run_record(latest), False
                conn.execute(
                    """
                    UPDATE dream_window_claims
                    SET status = 'running', updated_at = ?
                    WHERE window_key = ?
                    """,
                    (now, str(row["window_key"])),
                )
                _insert_event(
                    conn,
                    run_id=run_id,
                    schedule_id=(
                        str(row["schedule_id"]) if row["schedule_id"] else None
                    ),
                    window_key=str(row["window_key"]),
                    event_type="run_started",
                    status=DreamExecutionStatus.RUNNING.value,
                    message="Dream background execution started.",
                    payload={"origin": str(row["origin"])},
                    created_at=now,
                )
            updated = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("failed to read started dream run")
        record = _run_record(updated)
        return record, record.status is DreamExecutionStatus.RUNNING

    def finish_run(
        self,
        run_id: str,
        *,
        status: DreamExecutionStatus,
        updated_at_utc: datetime,
        council_run_id: str | None = None,
        council_result: dict[str, Any] | None = None,
        item: DreamItem | None = None,
        error: str | None = None,
    ) -> DreamRunRecord:
        if status not in {
            DreamExecutionStatus.COMPLETE,
            DreamExecutionStatus.PARTIAL,
            DreamExecutionStatus.CANCELLED,
            DreamExecutionStatus.FAILED,
            DreamExecutionStatus.INTERRUPTED,
        }:
            raise ValueError("finish_run requires a terminal status")
        now = _iso(updated_at_utc)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise DreamInboxError(f"unknown dream run: {run_id}")
            current = DreamExecutionStatus(str(row["status"]))
            if current in _TERMINAL_EXECUTION_STATUSES:
                return _run_record(row)
            if bool(row["cancel_requested"]):
                status = DreamExecutionStatus.CANCELLED
                item = None
            if item is not None:
                if status not in {
                    DreamExecutionStatus.COMPLETE,
                    DreamExecutionStatus.PARTIAL,
                }:
                    raise DreamInboxError("only successful dream runs may create inbox items")
                self._insert_item(conn, run_id, item, now)
            conn.execute(
                """
                UPDATE dream_runs SET
                    status = ?, council_run_id = ?, council_result_json = ?,
                    item_id = ?, error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    council_run_id,
                    _json(council_result) if council_result is not None else None,
                    item.item_id if item else None,
                    _bound_error(error),
                    now,
                    run_id,
                ),
            )
            conn.execute(
                """
                UPDATE dream_window_claims SET status = ?, updated_at = ?
                WHERE window_key = ?
                """,
                (status.value, now, str(row["window_key"])),
            )
            _insert_event(
                conn,
                run_id=run_id,
                schedule_id=str(row["schedule_id"]) if row["schedule_id"] else None,
                window_key=str(row["window_key"]),
                event_type=f"run_{status.value}",
                status=status.value,
                message=_terminal_message(status, error),
                payload={
                    "council_run_id": council_run_id,
                    "item_id": item.item_id if item else None,
                    "proposal_pending_review": item is not None,
                },
                created_at=now,
            )
            updated = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("failed to read finished dream run")
        return _run_record(updated)

    def get_run(self, run_id: str) -> DreamRunRecord:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise DreamInboxError(f"unknown dream run: {run_id}")
        return _run_record(row)

    def get_run_by_window(self, window_key: str) -> DreamRunRecord | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE window_key = ?",
                (window_key,),
            ).fetchone()
        return _run_record(row) if row is not None else None

    def list_runs(self, limit: int = 100) -> tuple[DreamRunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dream_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(_run_record(row) for row in rows)

    def list_claimed_runs(self, limit: int = 100) -> tuple[DreamRunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dream_runs
                WHERE status = 'claimed'
                ORDER BY created_at, run_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(_run_record(row) for row in rows)

    def snapshot_for_run(self, run_id: str) -> SourceSnapshot:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise DreamInboxError(f"unknown dream run: {run_id}")
        return _snapshot(json.loads(str(row["snapshot_json"])))

    def request_cancel(self, run_id: str, *, requested_at_utc: datetime) -> DreamRunRecord:
        now = _iso(requested_at_utc)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise DreamInboxError(f"unknown dream run: {run_id}")
            current = DreamExecutionStatus(str(row["status"]))
            if current in _TERMINAL_EXECUTION_STATUSES:
                return _run_record(row)
            conn.execute(
                """
                UPDATE dream_runs
                SET cancel_requested = 1, updated_at = ?
                WHERE run_id = ?
                """,
                (now, run_id),
            )
            _insert_event(
                conn,
                run_id=run_id,
                schedule_id=str(row["schedule_id"]) if row["schedule_id"] else None,
                window_key=str(row["window_key"]),
                event_type="cancel_requested",
                status=current.value,
                message="Dream cancellation was requested.",
                payload={},
                created_at=now,
            )
            if current is DreamExecutionStatus.CLAIMED:
                conn.execute(
                    """
                    UPDATE dream_runs
                    SET status = 'cancelled', updated_at = ?
                    WHERE run_id = ? AND status = 'claimed'
                    """,
                    (now, run_id),
                )
                conn.execute(
                    """
                    UPDATE dream_window_claims
                    SET status = 'cancelled', updated_at = ?
                    WHERE window_key = ?
                    """,
                    (now, str(row["window_key"])),
                )
                _insert_event(
                    conn,
                    run_id=run_id,
                    schedule_id=(
                        str(row["schedule_id"]) if row["schedule_id"] else None
                    ),
                    window_key=str(row["window_key"]),
                    event_type="run_cancelled",
                    status=DreamExecutionStatus.CANCELLED.value,
                    message="Dream run was cancelled before it started.",
                    payload={},
                    created_at=now,
                )
            updated = conn.execute(
                "SELECT * FROM dream_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("failed to read cancelled dream run")
        return _run_record(updated)

    def recover_interrupted(self, *, recovered_at_utc: datetime | None = None) -> int:
        now = _iso(recovered_at_utc or datetime.now(UTC))
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT run_id, window_key FROM dream_runs
                WHERE status = 'running'
                """
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE dream_runs SET
                        status = 'interrupted',
                        error = COALESCE(
                            error,
                            'The app stopped before this run reached a terminal result.'
                        ),
                        updated_at = ?
                    WHERE run_id = ?
                    """,
                    (now, str(row["run_id"])),
                )
                conn.execute(
                    """
                    UPDATE dream_window_claims
                    SET status = 'interrupted', updated_at = ?
                    WHERE window_key = ?
                    """,
                    (now, str(row["window_key"])),
                )
                current = conn.execute(
                    "SELECT schedule_id FROM dream_runs WHERE run_id = ?",
                    (str(row["run_id"]),),
                ).fetchone()
                _insert_event(
                    conn,
                    run_id=str(row["run_id"]),
                    schedule_id=(
                        str(current["schedule_id"])
                        if current is not None and current["schedule_id"]
                        else None
                    ),
                    window_key=str(row["window_key"]),
                    event_type="run_interrupted",
                    status=DreamExecutionStatus.INTERRUPTED.value,
                    message=(
                        "The app stopped before this dream run reached a terminal result."
                    ),
                    payload={"recovered_on_startup": True},
                    created_at=now,
                )
        return len(rows)

    def append_event(
        self,
        event_type: str,
        *,
        created_at_utc: datetime,
        run_id: str | None = None,
        schedule_id: str | None = None,
        window_key: str | None = None,
        status: str | None = None,
        message: str = "",
        payload: dict[str, Any] | None = None,
    ) -> DreamRunEvent:
        if not event_type.strip():
            raise ValueError("event_type must not be empty")
        now = _iso(created_at_utc)
        with self.store.connection() as conn:
            event_id = _insert_event(
                conn,
                run_id=run_id,
                schedule_id=schedule_id,
                window_key=window_key,
                event_type=event_type,
                status=status,
                message=message,
                payload=payload or {},
                created_at=now,
            )
            row = conn.execute(
                "SELECT * FROM dream_run_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to read appended dream event")
        return _event_record(row)

    def list_events(
        self,
        *,
        run_id: str | None = None,
        schedule_id: str | None = None,
        limit: int = 200,
    ) -> tuple[DreamRunEvent, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        params: list[object] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if schedule_id is not None:
            clauses.append("schedule_id = ?")
            params.append(schedule_id)
        sql = "SELECT * FROM dream_run_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.store.connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(_event_record(row) for row in reversed(rows))

    def add_inbox_item(self, run_id: str, item: DreamItem) -> DreamItem:
        now = item.created_at_utc.isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_item(conn, run_id, item, now)
        return item

    def get_item(self, item_id: str) -> DreamItem:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT item_json FROM dream_inbox_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        if row is None:
            raise DreamInboxError(f"unknown dream item: {item_id}")
        return _item(json.loads(str(row["item_json"])))

    def list_items(
        self,
        disposition: DreamDisposition | None = None,
    ) -> tuple[DreamItem, ...]:
        sql = "SELECT item_json FROM dream_inbox_items"
        params: tuple[str, ...] = ()
        if disposition is not None:
            sql += " WHERE disposition = ?"
            params = (disposition.value,)
        sql += " ORDER BY created_at DESC, item_id"
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(_item(json.loads(str(row["item_json"]))) for row in rows)

    def promote(
        self,
        item_id: str,
        *,
        target: PromotionTarget,
        decided_by: str,
        decided_at_utc: datetime,
        rationale: str,
    ) -> tuple[DreamItem, PromotionHandoff]:
        decision = DreamDecision(
            kind=DecisionKind.PROMOTE,
            target=target,
            decided_by=decided_by,
            decided_at_utc=decided_at_utc,
            rationale=rationale,
        )
        updated = self._decide(item_id, DreamDisposition.PROMOTED, decision)
        return updated, PromotionHandoff(
            item_id=updated.item_id,
            target=target,
            proposal_text=updated.proposal_text,
            epistemic_label=updated.epistemic_label,
            source_refs=updated.source_refs,
            snapshot_id=updated.snapshot_id,
            approved_by=decision.decided_by,
            approved_at_utc=decision.decided_at_utc,
        )

    def reject(
        self,
        item_id: str,
        *,
        decided_by: str,
        decided_at_utc: datetime,
        rationale: str,
    ) -> DreamItem:
        decision = DreamDecision(
            kind=DecisionKind.REJECT,
            decided_by=decided_by,
            decided_at_utc=decided_at_utc,
            rationale=rationale,
        )
        return self._decide(item_id, DreamDisposition.REJECTED, decision)

    def _decide(
        self,
        item_id: str,
        disposition: DreamDisposition,
        decision: DreamDecision,
    ) -> DreamItem:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dream_inbox_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise DreamInboxError(f"unknown dream item: {item_id}")
            current = _item(json.loads(str(row["item_json"])))
            if current.disposition is not DreamDisposition.PENDING:
                raise DreamInboxError("dream item has already received a final review decision")
            updated = replace(current, disposition=disposition, decision=decision)
            cursor = conn.execute(
                """
                UPDATE dream_inbox_items
                SET disposition = ?, item_json = ?, updated_at = ?
                WHERE item_id = ? AND disposition = 'pending'
                """,
                (
                    disposition.value,
                    _json(updated.to_dict()),
                    decision.decided_at_utc.isoformat(),
                    item_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DreamInboxError("dream item has already received a final review decision")
        return updated

    @staticmethod
    def _insert_item(
        conn: sqlite3.Connection,
        run_id: str,
        item: DreamItem,
        now: str,
    ) -> None:
        if item.run_status not in {DreamRunStatus.COMPLETE, DreamRunStatus.PARTIAL}:
            raise DreamInboxError("only complete or partial proposals can enter the Dream Inbox")
        if item.disposition is not DreamDisposition.PENDING:
            raise DreamInboxError("new Dream Inbox items must be pending review")
        run = conn.execute(
            "SELECT snapshot_json, window_key FROM dream_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise DreamInboxError(f"unknown dream run: {run_id}")
        if str(run["window_key"]) != item.window_key:
            raise DreamInboxError("dream item window does not match its run")
        snapshot = _snapshot(json.loads(str(run["snapshot_json"])))
        report = ProvenanceValidator().validate_item(item, snapshot)
        if not report.ok:
            codes = ", ".join(finding.code for finding in report.findings)
            raise DreamInboxError(f"dream provenance validation failed: {codes}")
        existing = conn.execute(
            """
            SELECT item_json FROM dream_inbox_items
            WHERE item_id = ? OR window_key = ? OR run_id = ?
            """,
            (item.item_id, item.window_key, run_id),
        ).fetchone()
        if existing is not None:
            persisted = _item(json.loads(str(existing["item_json"])))
            if persisted == item:
                return
            raise DreamInboxError("dream run or window already has a different Inbox item")
        conn.execute(
            """
            INSERT INTO dream_inbox_items(
                item_id, window_key, run_id, disposition, item_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.item_id,
                item.window_key,
                run_id,
                item.disposition.value,
                _json(item.to_dict()),
                item.created_at_utc.isoformat(),
                now,
            ),
        )


_TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        DreamExecutionStatus.COMPLETE,
        DreamExecutionStatus.PARTIAL,
        DreamExecutionStatus.CANCELLED,
        DreamExecutionStatus.FAILED,
        DreamExecutionStatus.INTERRUPTED,
    }
)


def _stored_schedule(row: sqlite3.Row) -> StoredDreamSchedule:
    return StoredDreamSchedule(
        schedule=_schedule(json.loads(str(row["schedule_json"]))),
        resource_rules=_resource_rules(json.loads(str(row["resource_rules_json"]))),
        quiet_window=(
            _quiet_window(json.loads(str(row["quiet_window_json"])))
            if row["quiet_window_json"]
            else None
        ),
        version=int(row["version"]),
        created_at_utc=_datetime(str(row["created_at"])),
        updated_at_utc=_datetime(str(row["updated_at"])),
    )


def _schedule(payload: dict[str, Any]) -> DreamSchedule:
    return DreamSchedule(
        schedule_id=str(payload["schedule_id"]),
        recipe_id=str(payload["recipe_id"]),
        timezone=str(payload["timezone"]),
        local_time=time.fromisoformat(str(payload["local_time"])),
        created_at_utc=_datetime(str(payload["created_at_utc"])),
        owner=ScheduleOwner(str(payload.get("owner", ScheduleOwner.APP.value))),
        enabled=bool(payload.get("enabled", True)),
        catch_up=CatchUpMode(str(payload.get("catch_up", CatchUpMode.LATEST.value))),
        on_time_grace=timedelta(
            seconds=float(payload.get("on_time_grace_seconds", 900.0))
        ),
        max_lookback_days=int(payload.get("max_lookback_days", 7)),
        max_catch_up_windows=int(payload.get("max_catch_up_windows", 3)),
    )


def _resource_rules(payload: dict[str, Any]) -> ResourceRules:
    return ResourceRules(
        min_idle_seconds=float(payload.get("min_idle_seconds", 300.0)),
        max_cpu_percent=float(payload.get("max_cpu_percent", 60.0)),
        min_available_memory_bytes=int(
            payload.get("min_available_memory_bytes", 2 * 1024**3)
        ),
        min_gpu_free_bytes=(
            int(payload["min_gpu_free_bytes"])
            if payload.get("min_gpu_free_bytes") is not None
            else None
        ),
        require_no_model_jobs=bool(payload.get("require_no_model_jobs", True)),
        require_ac_power=bool(payload.get("require_ac_power", False)),
    )


def _quiet_window(payload: dict[str, Any]) -> QuietWindow:
    return QuietWindow(
        timezone=str(payload["timezone"]),
        start_local=time.fromisoformat(str(payload["start_local"])),
        end_local=time.fromisoformat(str(payload["end_local"])),
        weekdays=tuple(int(item) for item in payload.get("weekdays", range(7))),
    )


def _stored_recipe(row: sqlite3.Row) -> StoredDreamRecipe:
    payload = json.loads(str(row["recipe_json"]))
    return StoredDreamRecipe(
        recipe=_recipe(payload),
        version=int(row["version"]),
        created_at_utc=_datetime(str(row["created_at"])),
        builtin=bool(row["builtin"]),
    )


def _recipe(payload: dict[str, Any]) -> DreamRecipe:
    return DreamRecipe(
        recipe_id=str(payload["recipe_id"]),
        name=str(payload["name"]),
        kind=DreamRecipeKind(str(payload["kind"])),
        objective=str(payload["objective"]),
        role_angles=tuple(
            RoleAngle(
                role=TeamRole(str(item["role"])),
                instruction=str(item["instruction"]),
            )
            for item in payload.get("role_angles", ())
        ),
        source_scopes=tuple(str(item) for item in payload.get("source_scopes", ())),
    )


def _run_record(row: sqlite3.Row) -> DreamRunRecord:
    return DreamRunRecord(
        run_id=str(row["run_id"]),
        recipe_id=str(row["recipe_id"]),
        recipe_version=int(row["recipe_version"]),
        window_key=str(row["window_key"]),
        snapshot_id=str(row["snapshot_id"]),
        status=DreamExecutionStatus(str(row["status"])),
        created_at_utc=_datetime(str(row["created_at"])),
        updated_at_utc=_datetime(str(row["updated_at"])),
        council_run_id=str(row["council_run_id"]) if row["council_run_id"] else None,
        item_id=str(row["item_id"]) if row["item_id"] else None,
        error=str(row["error"]) if row["error"] else None,
        cancel_requested=bool(row["cancel_requested"]),
        schedule_id=str(row["schedule_id"]) if row["schedule_id"] else None,
        origin=str(row["origin"]),
        due_at_utc=_datetime(str(row["due_at"])) if row["due_at"] else None,
        preferred_lead=(
            str(row["preferred_lead"]) if row["preferred_lead"] else None
        ),
    )


def _event_record(row: sqlite3.Row) -> DreamRunEvent:
    payload = json.loads(str(row["payload_json"]))
    return DreamRunEvent(
        event_id=int(row["id"]),
        event_type=str(row["event_type"]),
        created_at_utc=_datetime(str(row["created_at"])),
        run_id=str(row["run_id"]) if row["run_id"] else None,
        schedule_id=str(row["schedule_id"]) if row["schedule_id"] else None,
        window_key=str(row["window_key"]) if row["window_key"] else None,
        status=str(row["status"]) if row["status"] else None,
        message=str(row["message"]),
        payload=payload if isinstance(payload, dict) else {"value": payload},
    )


def _snapshot(payload: dict[str, Any]) -> SourceSnapshot:
    return SourceSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        captured_at_utc=_datetime(str(payload["captured_at_utc"])),
        entries=tuple(
            SnapshotEntry(
                source_id=str(item["source_id"]),
                kind=SourceKind(str(item["kind"])),
                locator=str(item["locator"]),
                source_captured_at_utc=_datetime(str(item["source_captured_at_utc"])),
                content=str(item["content"]),
                content_sha256=str(item["content_sha256"]),
                redactions=tuple(str(value) for value in item.get("redactions", ())),
                truncated=bool(item.get("truncated", False)),
            )
            for item in payload.get("entries", ())
        ),
        exclusions=tuple(
            SnapshotExclusion(
                source_id=str(item["source_id"]),
                kind=SourceKind(str(item["kind"])),
                reason=str(item["reason"]),
            )
            for item in payload.get("exclusions", ())
        ),
    )


def _item(payload: dict[str, Any]) -> DreamItem:
    decision_payload = payload.get("decision")
    decision = (
        DreamDecision(
            kind=DecisionKind(str(decision_payload["kind"])),
            decided_by=str(decision_payload["decided_by"]),
            decided_at_utc=_datetime(str(decision_payload["decided_at_utc"])),
            rationale=str(decision_payload["rationale"]),
            target=(
                PromotionTarget(str(decision_payload["target"]))
                if decision_payload.get("target")
                else None
            ),
        )
        if decision_payload
        else None
    )
    return DreamItem(
        item_id=str(payload["item_id"]),
        recipe_id=str(payload["recipe_id"]),
        window_key=str(payload["window_key"]),
        council_run_id=str(payload["council_run_id"]),
        snapshot_id=str(payload["snapshot_id"]),
        proposal_text=str(payload["proposal_text"]),
        run_status=DreamRunStatus(str(payload["run_status"])),
        epistemic_label=EpistemicLabel(str(payload["epistemic_label"])),
        source_refs=tuple(str(item) for item in payload.get("source_refs", ())),
        created_at_utc=_datetime(str(payload["created_at_utc"])),
        partial_reason=(
            str(payload["partial_reason"]) if payload.get("partial_reason") else None
        ),
        disposition=DreamDisposition(str(payload["disposition"])),
        decision=decision,
    )


def _datetime(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value), "persisted datetime")


def _iso(value: datetime) -> str:
    return _utc(value, "datetime").isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bound_error(error: str | None) -> str | None:
    if error is None:
        return None
    normalized = " ".join(error.split())
    return normalized[:1000]


def _terminal_message(status: DreamExecutionStatus, error: str | None) -> str:
    if status is DreamExecutionStatus.COMPLETE:
        return "Dream run completed and produced a proposal pending human review."
    if status is DreamExecutionStatus.PARTIAL:
        return "Dream run partially completed; its proposal remains pending human review."
    if status is DreamExecutionStatus.CANCELLED:
        return "Dream run was cancelled."
    if status is DreamExecutionStatus.INTERRUPTED:
        return "Dream run was interrupted before completion."
    detail = _bound_error(error)
    return f"Dream run failed: {detail}" if detail else "Dream run failed."


def _insert_event(
    conn: sqlite3.Connection,
    *,
    run_id: str | None,
    schedule_id: str | None,
    window_key: str | None,
    event_type: str,
    status: str | None,
    message: str,
    payload: dict[str, Any],
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO dream_run_events(
            run_id, schedule_id, window_key, event_type, status,
            message, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            schedule_id,
            window_key,
            event_type,
            status,
            _bound_error(message) or "",
            _json(payload),
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
