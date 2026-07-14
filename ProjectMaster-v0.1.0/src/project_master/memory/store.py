from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(namespace, key)
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    title TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    statement TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    assessment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                    stance TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    reliability REAL NOT NULL CHECK(reliability >= 0 AND reliability <= 1),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def remember(
        self,
        namespace: str,
        key: str,
        value: Any,
        source: str = "user",
        confidence: float = 0.8,
    ) -> None:
        now = _now()
        payload = json.dumps(value, ensure_ascii=False)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO memories(
                    namespace, key, value_json, source, confidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (namespace, key, payload, source, confidence, now, now),
            )

    def recall(
        self, query: str = "", namespace: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if namespace:
            sql += " AND namespace = ?"
            params.append(namespace)
        if query:
            sql += " AND (key LIKE ? OR value_json LIKE ?)"
            pattern = f"%{query}%"
            params.extend([pattern, pattern])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_memory_row(row) for row in rows]

    def create_session(self, title: str | None = None) -> str:
        session_id = str(uuid.uuid4())
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions(id, started_at, title) VALUES (?, ?, ?)",
                (session_id, _now(), title),
            )
        return session_id

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )

    def recent_messages(self, session_id: str, limit: int = 30) -> list[dict[str, str]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT sessions.id, sessions.started_at, sessions.title,
                       COUNT(messages.id) AS message_count
                FROM sessions
                LEFT JOIN messages ON messages.session_id = sessions.id
                GROUP BY sessions.id
                ORDER BY sessions.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def session_exists(self, session_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    def record_claim(
        self,
        statement: str,
        status: str = "unverified",
        confidence: float = 0.0,
        assessment: str = "",
    ) -> int:
        now = _now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO claims(
                    statement, status, confidence, assessment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(statement) DO UPDATE SET
                    status = excluded.status,
                    confidence = excluded.confidence,
                    assessment = CASE
                        WHEN excluded.assessment = '' THEN claims.assessment
                        ELSE excluded.assessment
                    END,
                    updated_at = excluded.updated_at
                """,
                (statement, status, confidence, assessment, now, now),
            )
            row = conn.execute("SELECT id FROM claims WHERE statement = ?", (statement,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to retrieve claim after insert")
        return int(row["id"])

    def add_evidence(
        self,
        claim_id: int,
        stance: str,
        summary: str,
        source_ref: str = "",
        source_type: str = "user_supplied",
        reliability: float = 0.5,
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO evidence(
                    claim_id, stance, source_type, source_ref, summary, reliability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (claim_id, stance, source_type, source_ref, summary, reliability, _now()),
            )
            return int(cursor.lastrowid)

    def list_claims(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM claims"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            claim_rows = conn.execute(sql, params).fetchall()
            result: list[dict[str, Any]] = []
            for claim in claim_rows:
                evidence_rows = conn.execute(
                    "SELECT * FROM evidence WHERE claim_id = ? ORDER BY id",
                    (claim["id"],),
                ).fetchall()
                item = dict(claim)
                item["evidence"] = [dict(row) for row in evidence_rows]
                result.append(item)
        return result

    def save_profile(self, profile_id: str, data: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO profiles(id, data_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (profile_id, json.dumps(data), _now()),
            )

    def load_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT data_json FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["data_json"])
        return value if isinstance(value, dict) else None


def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["value"] = json.loads(item.pop("value_json"))
    except json.JSONDecodeError:
        item["value"] = item.pop("value_json")
    return item


def _now() -> str:
    return datetime.now(UTC).isoformat()
