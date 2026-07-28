from __future__ import annotations

import hashlib
import mimetypes
import re
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from project_master.knowledge.models import KnowledgeDocument, KnowledgeHit
from project_master.memory.store import SQLiteStore

_WORD = re.compile(r"[\w-]{2,}", re.UNICODE)
_QUERY_STOP_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "answer",
    "are",
    "at",
    "be",
    "been",
    "being",
    "but",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "give",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "my",
    "of",
    "on",
    "only",
    "or",
    "our",
    "please",
    "project",
    "reply",
    "respond",
    "should",
    "show",
    "tell",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "your",
}


class KnowledgeStore:
    """Versioned local documents and bounded lexical retrieval backed by SQLite FTS5."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.fts_available = self._initialize()

    def _initialize(self) -> bool:
        with self.store.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    root_path TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(project_id, relative_path, version)
                );

                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL
                        REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    UNIQUE(document_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_documents_project_active
                    ON knowledge_documents(project_id, active, relative_path);
                CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
                    ON knowledge_chunks(document_id, chunk_index);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
                    USING fts5(
                        chunk_id UNINDEXED,
                        project_id UNINDEXED,
                        relative_path,
                        content,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
            except sqlite3.OperationalError:
                return False
        return True

    def active_document(
        self,
        project_id: str,
        relative_path: str,
    ) -> KnowledgeDocument | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE project_id = ? AND relative_path = ? AND active = 1
                ORDER BY version DESC LIMIT 1
                """,
                (project_id, relative_path),
            ).fetchone()
        return _document(row) if row is not None else None

    def index_document(
        self,
        *,
        project_id: str,
        root_path: Path,
        relative_path: str,
        content: str,
        size_bytes: int,
    ) -> tuple[KnowledgeDocument, bool]:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self.active_document(project_id, relative_path)
        if existing is not None and existing.content_sha256 == digest:
            return existing, False
        now = datetime.now(UTC).isoformat()
        document_id = f"knowledge-document-{uuid.uuid4().hex}"
        version = 1 if existing is None else existing.version + 1
        mime_type = mimetypes.guess_type(relative_path)[0] or "text/plain"
        chunks = _chunk_text(content)
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE knowledge_documents
                SET active = 0
                WHERE project_id = ? AND relative_path = ? AND active = 1
                """,
                (project_id, relative_path),
            )
            conn.execute(
                """
                INSERT INTO knowledge_documents(
                    id, project_id, root_path, relative_path, content_sha256,
                    version, mime_type, size_bytes, indexed_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    document_id,
                    project_id,
                    str(root_path),
                    relative_path,
                    digest,
                    version,
                    mime_type,
                    size_bytes,
                    now,
                ),
            )
            for chunk_index, (line_start, line_end, chunk_content) in enumerate(chunks):
                chunk_id = f"knowledge-chunk-{uuid.uuid4().hex}"
                chunk_digest = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        id, document_id, project_id, relative_path, chunk_index,
                        line_start, line_end, content, content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        project_id,
                        relative_path,
                        chunk_index,
                        line_start,
                        line_end,
                        chunk_content,
                        chunk_digest,
                    ),
                )
                if self.fts_available:
                    conn.execute(
                        """
                        INSERT INTO knowledge_chunks_fts(
                            chunk_id, project_id, relative_path, content
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (chunk_id, project_id, relative_path, chunk_content),
                    )
        document = KnowledgeDocument(
            id=document_id,
            project_id=project_id,
            root_path=str(root_path),
            relative_path=relative_path,
            content_sha256=digest,
            version=version,
            mime_type=mime_type,
            size_bytes=size_bytes,
            indexed_at=now,
        )
        return document, True

    def archive_missing(self, project_id: str, present_paths: Iterable[str]) -> int:
        present = tuple(sorted(set(present_paths)))
        with self.store.connection() as conn:
            active_rows = conn.execute(
                """
                SELECT id, relative_path FROM knowledge_documents
                WHERE project_id = ? AND active = 1
                """,
                (project_id,),
            ).fetchall()
            missing_ids = [
                str(row["id"])
                for row in active_rows
                if str(row["relative_path"]) not in present
            ]
            if not missing_ids:
                return 0
            placeholders = ",".join("?" for _item in missing_ids)
            conn.execute(
                f"UPDATE knowledge_documents SET active = 0 WHERE id IN ({placeholders})",
                missing_ids,
            )
        return len(missing_ids)

    def list_documents(
        self,
        project_id: str,
        *,
        include_history: bool = False,
        limit: int = 1_000,
    ) -> list[KnowledgeDocument]:
        sql = "SELECT * FROM knowledge_documents WHERE project_id = ?"
        params: list[Any] = [project_id]
        if not include_history:
            sql += " AND active = 1"
        sql += " ORDER BY relative_path, version DESC LIMIT ?"
        params.append(min(max(limit, 1), 10_000))
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_document(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 8,
    ) -> list[KnowledgeHit]:
        terms = _query_terms(query)
        if not terms:
            return []
        selected_limit = min(max(limit, 1), 50)
        if self.fts_available:
            return self._search_fts(terms, project_id, selected_limit)
        return self._search_like(terms, project_id, selected_limit)

    def _search_fts(
        self,
        terms: tuple[str, ...],
        project_id: str | None,
        limit: int,
    ) -> list[KnowledgeHit]:
        expression = " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        sql = """
            SELECT c.*, d.version, d.content_sha256 AS document_sha256,
                   bm25(knowledge_chunks_fts, 0.0, 1.0, 2.0) AS rank
            FROM knowledge_chunks_fts
            JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.chunk_id
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE knowledge_chunks_fts MATCH ? AND d.active = 1
        """
        params: list[Any] = [expression]
        if project_id is not None:
            sql += " AND c.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY rank ASC, c.relative_path, c.chunk_index LIMIT ?"
        params.append(min(max(limit * 8, limit), 200))
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        ranked = sorted(
            rows,
            key=lambda row: (
                -_term_match_count(row, terms),
                float(row["rank"]),
                str(row["relative_path"]),
                int(row["chunk_index"]),
            ),
        )
        return [
            _hit(
                row,
                score=float(_term_match_count(row, terms))
                + max(0.0, -float(row["rank"])),
            )
            for row in ranked[:limit]
        ]

    def _search_like(
        self,
        terms: tuple[str, ...],
        project_id: str | None,
        limit: int,
    ) -> list[KnowledgeHit]:
        clauses = ["LOWER(c.content) LIKE ?" for _term in terms]
        sql = f"""
            SELECT c.*, d.version, d.content_sha256 AS document_sha256
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE d.active = 1 AND ({' AND '.join(clauses)})
        """
        params: list[Any] = [f"%{term.lower()}%" for term in terms]
        if project_id is not None:
            sql += " AND c.project_id = ?"
            params.append(project_id)
        sql += " ORDER BY c.relative_path, c.chunk_index LIMIT ?"
        params.append(min(max(limit * 8, limit), 200))
        with self.store.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            _hit(
                row,
                score=float(_term_match_count(row, terms)),
            )
            for row in sorted(
                rows,
                key=lambda row: (
                    -_term_match_count(row, terms),
                    str(row["relative_path"]),
                    int(row["chunk_index"]),
                ),
            )[:limit]
        ]


def _document(row: sqlite3.Row) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        root_path=str(row["root_path"]),
        relative_path=str(row["relative_path"]),
        content_sha256=str(row["content_sha256"]),
        version=int(row["version"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        indexed_at=str(row["indexed_at"]),
        active=bool(row["active"]),
    )


def _hit(row: sqlite3.Row, score: float) -> KnowledgeHit:
    return KnowledgeHit(
        chunk_id=str(row["id"]),
        document_id=str(row["document_id"]),
        project_id=str(row["project_id"]),
        relative_path=str(row["relative_path"]),
        line_start=int(row["line_start"]),
        line_end=int(row["line_end"]),
        content=str(row["content"]),
        score=score,
        content_sha256=str(row["document_sha256"]),
        document_version=int(row["version"]),
    )


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term
            for match in _WORD.finditer(query)
            if (term := match.group(0).casefold()) not in _QUERY_STOP_WORDS
        )
    )[:16]


def _term_match_count(row: sqlite3.Row, terms: tuple[str, ...]) -> int:
    haystack = f"{row['relative_path']}\n{row['content']}".casefold()
    return sum(term in haystack for term in terms)


def _chunk_text(
    content: str,
    *,
    target_chars: int = 2_800,
    overlap_lines: int = 3,
) -> list[tuple[int, int, str]]:
    lines = content.splitlines()
    if not lines:
        return [(1, 1, "")]
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(lines):
        end = start
        char_count = 0
        while end < len(lines):
            added = len(lines[end]) + 1
            if end > start and char_count + added > target_chars:
                break
            char_count += added
            end += 1
        chunks.append((start + 1, end, "\n".join(lines[start:end])))
        if end >= len(lines):
            break
        start = max(start + 1, end - overlap_lines)
    return chunks
