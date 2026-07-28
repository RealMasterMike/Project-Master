from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: str
    project_id: str
    root_path: str
    relative_path: str
    content_sha256: str
    version: int
    mime_type: str
    size_bytes: int
    indexed_at: str
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "root_path": self.root_path,
            "relative_path": self.relative_path,
            "content_sha256": self.content_sha256,
            "version": self.version,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "indexed_at": self.indexed_at,
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    chunk_id: str
    document_id: str
    project_id: str
    relative_path: str
    line_start: int
    line_end: int
    content: str
    score: float
    content_sha256: str
    document_version: int

    @property
    def citation(self) -> str:
        if self.line_start == self.line_end:
            return f"{self.relative_path}:{self.line_start}"
        return f"{self.relative_path}:{self.line_start}-{self.line_end}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "project_id": self.project_id,
            "relative_path": self.relative_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "content": self.content,
            "score": self.score,
            "citation": self.citation,
            "content_sha256": self.content_sha256,
            "document_version": self.document_version,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeIndexResult:
    project_id: str
    root_path: str
    indexed: int
    unchanged: int
    skipped: int
    archived: int
    errors: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "root_path": self.root_path,
            "indexed": self.indexed,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "archived": self.archived,
            "errors": list(self.errors),
        }
