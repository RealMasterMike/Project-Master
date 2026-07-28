from __future__ import annotations

from pathlib import Path

from project_master.knowledge.models import (
    KnowledgeDocument,
    KnowledgeHit,
    KnowledgeIndexResult,
)
from project_master.knowledge.store import KnowledgeStore
from project_master.orchestration.store import OrchestrationStore

_TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".gd",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_TEXT_NAMES = {
    "AGENTS.md",
    "CHANGELOG",
    "DECISIONS.md",
    "LICENSE",
    "PROJECT.md",
    "README",
    "approvals.md",
    "ideas.md",
    "tasks.md",
}
_IGNORED_PARTS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-linux",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}
_SENSITIVE_PARTS = {
    ".aws",
    ".azure",
    ".gnupg",
    ".kube",
    ".ssh",
}
_SENSITIVE_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {
    ".der",
    ".jks",
    ".key",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
}


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        orchestration: OrchestrationStore,
        *,
        max_file_bytes: int = 2_000_000,
        max_files: int = 2_000,
        max_total_bytes: int = 50_000_000,
    ) -> None:
        self.store = store
        self.orchestration = orchestration
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes

    def index_project(
        self,
        project_id: str,
        *,
        relative_path: str = ".",
        prune: bool = True,
    ) -> KnowledgeIndexResult:
        project = self.orchestration.get_project(project_id)
        if project is None:
            raise KeyError(f"Unknown project: {project_id}")
        raw_root = project.get("root_path")
        if not raw_root:
            raise ValueError("The project does not have a local root path.")
        root = Path(str(raw_root)).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(root)
        target = _safe_path(root, relative_path)
        candidates = [target] if target.is_file() else sorted(target.rglob("*"))
        indexed = 0
        unchanged = 0
        skipped = 0
        total_bytes = 0
        errors: list[dict[str, str]] = []
        present_paths: set[str] = set()
        inspected = 0
        for candidate in candidates:
            if inspected >= self.max_files:
                skipped += 1
                break
            if not candidate.is_file() or _ignored(root, candidate):
                continue
            inspected += 1
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root)
                relative = resolved.relative_to(root).as_posix()
                if not _is_text_file(resolved):
                    skipped += 1
                    continue
                size = resolved.stat().st_size
                if size > self.max_file_bytes or total_bytes + size > self.max_total_bytes:
                    skipped += 1
                    continue
                content = resolved.read_text(encoding="utf-8")
                total_bytes += size
                present_paths.add(relative)
                _document, changed = self.store.index_document(
                    project_id=project_id,
                    root_path=root,
                    relative_path=relative,
                    content=content,
                    size_bytes=size,
                )
                if changed:
                    indexed += 1
                else:
                    unchanged += 1
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(
                    {
                        "path": str(candidate),
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
        archived = 0
        if prune and target == root:
            archived = self.store.archive_missing(project_id, present_paths)
        return KnowledgeIndexResult(
            project_id=project_id,
            root_path=str(root),
            indexed=indexed,
            unchanged=unchanged,
            skipped=skipped,
            archived=archived,
            errors=tuple(errors),
        )

    def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 8,
    ) -> list[KnowledgeHit]:
        if project_id is not None and self.orchestration.get_project(project_id) is None:
            raise KeyError(f"Unknown project: {project_id}")
        return self.store.search(query, project_id=project_id, limit=limit)

    def list_documents(
        self,
        project_id: str,
        *,
        include_history: bool = False,
    ) -> list[KnowledgeDocument]:
        if self.orchestration.get_project(project_id) is None:
            raise KeyError(f"Unknown project: {project_id}")
        return self.store.list_documents(
            project_id,
            include_history=include_history,
        )


def _safe_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError("Knowledge index paths must be relative to the project root.")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Knowledge index path escapes the project root.") from exc
    if not target.exists():
        raise FileNotFoundError(target)
    return target


def _ignored(root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    if any(part in _IGNORED_PARTS for part in relative.parts):
        return True
    if any(part.casefold() in _SENSITIVE_PARTS for part in relative.parts):
        return True
    name = relative.name.casefold()
    if (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith(".env")
        or name in _SENSITIVE_NAMES
        or name.startswith("credentials.")
        or name.startswith("secret.")
        or name.startswith("secrets.")
        or Path(name).suffix in _SENSITIVE_SUFFIXES
    ):
        return True
    return False


def _is_text_file(path: Path) -> bool:
    return path.name in _TEXT_NAMES or path.suffix.lower() in _TEXT_SUFFIXES
