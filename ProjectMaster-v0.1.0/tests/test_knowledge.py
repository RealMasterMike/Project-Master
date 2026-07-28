from __future__ import annotations

from pathlib import Path

import pytest

from project_master.knowledge import KnowledgeService, KnowledgeStore
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ProjectSpec
from project_master.orchestration.store import OrchestrationStore
from project_master.tools.base import ToolRegistry
from project_master.tools.knowledge import register_knowledge_tools


def _service(tmp_path: Path) -> tuple[KnowledgeService, str, Path]:
    root = tmp_path / "project"
    root.mkdir()
    sqlite = SQLiteStore(tmp_path / "master.db")
    orchestration = OrchestrationStore(sqlite)
    project_id = orchestration.create_project(
        ProjectSpec(name="Binder", root_path=str(root))
    )
    return (
        KnowledgeService(KnowledgeStore(sqlite), orchestration),
        project_id,
        root,
    )


def test_indexes_searches_and_cites_local_documents(tmp_path: Path) -> None:
    service, project_id, root = _service(tmp_path)
    (root / "PROJECT.md").write_text(
        "# Mission\nBuild a local multi-agent command center.\n",
        encoding="utf-8",
    )
    (root / "notes.txt").write_text(
        "ComfyUI should remain optional and work offline.\n",
        encoding="utf-8",
    )

    result = service.index_project(project_id)
    hits = service.search("multi-agent command", project_id=project_id)

    assert result.indexed == 2
    assert hits
    assert hits[0].relative_path == "PROJECT.md"
    assert hits[0].citation.startswith("PROJECT.md:")
    assert len(hits[0].content_sha256) == 64


def test_natural_language_question_retrieves_relevant_binder_excerpt(
    tmp_path: Path,
) -> None:
    service, project_id, root = _service(tmp_path)
    (root / "RELEASE.md").write_text(
        "The release-candidate verification phrase is ORBITAL-PINE-731.\n",
        encoding="utf-8",
    )
    service.index_project(project_id)

    hits = service.search(
        "What is the release-candidate verification phrase in this project? "
        "Reply with only the phrase.",
        project_id=project_id,
    )

    assert hits
    assert hits[0].relative_path == "RELEASE.md"
    assert "ORBITAL-PINE-731" in hits[0].content


def test_query_containing_only_conversational_stop_words_returns_no_hits(
    tmp_path: Path,
) -> None:
    service, project_id, root = _service(tmp_path)
    (root / "notes.md").write_text("Unrelated binder material.\n", encoding="utf-8")
    service.index_project(project_id)

    assert service.search("What is this about?", project_id=project_id) == []


def test_versioning_is_immutable_and_unchanged_files_are_not_duplicated(
    tmp_path: Path,
) -> None:
    service, project_id, root = _service(tmp_path)
    document = root / "ideas.md"
    document.write_text("First creator idea.\n", encoding="utf-8")
    assert service.index_project(project_id).indexed == 1
    assert service.index_project(project_id).unchanged == 1

    document.write_text("Second creator idea.\n", encoding="utf-8")
    assert service.index_project(project_id).indexed == 1
    history = service.store.list_documents(project_id, include_history=True)

    assert [item.version for item in history] == [2, 1]
    assert [item.active for item in history] == [True, False]
    assert service.search("First creator", project_id=project_id) == []
    assert service.search("Second creator", project_id=project_id)


def test_full_sync_archives_removed_documents(tmp_path: Path) -> None:
    service, project_id, root = _service(tmp_path)
    document = root / "tasks.md"
    document.write_text("Ship the Fedora build.\n", encoding="utf-8")
    service.index_project(project_id)
    document.unlink()

    result = service.index_project(project_id)

    assert result.archived == 1
    assert service.list_documents(project_id) == []


def test_ignores_build_trees_binary_files_and_external_symlinks(tmp_path: Path) -> None:
    service, project_id, root = _service(tmp_path)
    ignored = root / "node_modules"
    ignored.mkdir()
    (ignored / "noise.js").write_text("forbidden package noise", encoding="utf-8")
    (root / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    outside = tmp_path / "outside.md"
    outside.write_text("private outside content", encoding="utf-8")
    (root / "outside-link.md").symlink_to(outside)
    (root / "README").write_text("Visible binder content", encoding="utf-8")

    result = service.index_project(project_id)

    assert result.indexed == 1
    assert service.search("forbidden", project_id=project_id) == []
    assert service.search("private outside", project_id=project_id) == []
    assert service.search("Visible binder", project_id=project_id)


def test_ignores_common_secret_and_private_key_files(tmp_path: Path) -> None:
    service, project_id, root = _service(tmp_path)
    (root / ".env").write_text("SERVICE_TOKEN=do-not-index", encoding="utf-8")
    (root / ".env.local").write_text("LOCAL_SECRET=do-not-index", encoding="utf-8")
    (root / "client.pem").write_text("PRIVATE KEY do-not-index", encoding="utf-8")
    (root / "credentials.json").write_text(
        '{"password":"do-not-index"}',
        encoding="utf-8",
    )
    (root / "notes.md").write_text("Safe project decision.", encoding="utf-8")

    result = service.index_project(project_id)

    assert result.indexed == 1
    assert service.search("do-not-index", project_id=project_id) == []
    assert service.search("Safe project decision", project_id=project_id)


def test_rejects_index_path_traversal(tmp_path: Path) -> None:
    service, project_id, _root = _service(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        service.index_project(project_id, relative_path="../")


def test_agent_tool_returns_provenance_and_has_no_index_mutation(
    tmp_path: Path,
) -> None:
    service, project_id, root = _service(tmp_path)
    (root / "DECISIONS.md").write_text(
        "All model work stays on this machine.",
        encoding="utf-8",
    )
    service.index_project(project_id)
    registry = ToolRegistry()
    register_knowledge_tools(registry, service)

    ok, payload = registry.execute(
        "knowledge_search",
        {"query": "stays on this machine", "project_id": project_id},
    )

    assert ok is True
    assert "DECISIONS.md" in payload
    assert "provenance_note" in payload
    assert registry.names() == ["knowledge_search"]
