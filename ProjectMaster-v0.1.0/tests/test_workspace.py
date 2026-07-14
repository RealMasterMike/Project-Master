from pathlib import Path

from project_master.memory.store import SQLiteStore
from project_master.tools.builtin import build_registry


def test_workspace_blocks_escape(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    workspace = tmp_path / "workspace"
    registry = build_registry(store, workspace, allow_file_writes=True)
    ok, result = registry.execute("workspace_read", {"path": "../outside.txt"})
    assert not ok
    assert "escapes" in result


def test_workspace_write_and_read(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    workspace = tmp_path / "workspace"
    registry = build_registry(store, workspace, allow_file_writes=True)
    ok, _ = registry.execute("workspace_write", {"path": "notes/a.txt", "content": "hello"})
    assert ok
    ok, result = registry.execute("workspace_read", {"path": "notes/a.txt"})
    assert ok
    assert "hello" in result
