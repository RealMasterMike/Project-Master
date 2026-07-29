from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from project_master.memory.store import SQLiteStore

LOCAL_GPU_INFERENCE_RESOURCE = "local-gpu-inference"
INTERACTIVE_CHAT_OWNER_PREFIX = "interactive-chat:"


class ResourceGovernor:
    """A durable single-owner lease used to serialize heavy local inference."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def acquire(
        self,
        resource_key: str,
        owner: str,
        *,
        job_id: str | None = None,
        ttl_seconds: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if ttl_seconds < 5 or ttl_seconds > 86_400:
            raise ValueError("Lease TTL must be between 5 seconds and 24 hours")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, expires_at FROM resource_leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            if row is not None:
                current_expiry = datetime.fromisoformat(str(row["expires_at"]))
                if current_expiry > now and str(row["owner"]) != owner:
                    return False
            conn.execute(
                """
                INSERT INTO resource_leases(
                    resource_key, job_id, owner, expires_at, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_key) DO UPDATE SET
                    job_id = excluded.job_id,
                    owner = excluded.owner,
                    expires_at = excluded.expires_at,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resource_key,
                    job_id,
                    owner,
                    expires.isoformat(),
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    now.isoformat(),
                ),
            )
        return True

    def renew(self, resource_key: str, owner: str, ttl_seconds: int = 300) -> bool:
        if ttl_seconds < 5 or ttl_seconds > 86_400:
            raise ValueError("Lease TTL must be between 5 seconds and 24 hours")
        now = datetime.now(UTC)
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT owner, expires_at FROM resource_leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            if row is None or str(row["owner"]) != owner:
                return False
            if datetime.fromisoformat(str(row["expires_at"])) <= now:
                return False
            conn.execute(
                """
                UPDATE resource_leases
                SET expires_at = ?, updated_at = ?
                WHERE resource_key = ? AND owner = ?
                """,
                (
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                    now.isoformat(),
                    resource_key,
                    owner,
                ),
            )
        return True

    def release(self, resource_key: str, owner: str) -> bool:
        with self.store.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM resource_leases WHERE resource_key = ? AND owner = ?",
                (resource_key, owner),
            )
            return cursor.rowcount > 0

    def release_process_scoped(self, owner_prefix: str) -> int:
        """Drop leases whose owner cannot outlive the process that took them.

        Interactive chat and voice-render leases are released in a `finally`,
        so they only survive if the backend was killed mid-request (SIGKILL
        skips `finally`). Such a lease is orphaned by definition, but its TTL
        keeps blocking other consumers until it expires.
        """
        with self.store.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM resource_leases WHERE owner LIKE ?",
                (f"{owner_prefix}%",),
            )
            return cursor.rowcount

    def status(self, resource_key: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM resource_leases WHERE resource_key = ?", (resource_key,)
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            if datetime.fromisoformat(str(item["expires_at"])) <= now:
                conn.execute(
                    "DELETE FROM resource_leases WHERE resource_key = ?", (resource_key,)
                )
                return None
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item
