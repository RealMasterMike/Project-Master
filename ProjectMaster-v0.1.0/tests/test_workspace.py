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
    default_schema_names = {
        schema["function"]["name"] for schema in registry.schemas()
    }
    assert "workspace_read" in default_schema_names
    assert "workspace_write" not in default_schema_names

    ok, denied = registry.execute(
        "workspace_write",
        {"path": "notes/a.txt", "content": "blocked"},
    )
    assert not ok
    assert "explicit authorization" in denied
    assert not (workspace / "notes" / "a.txt").exists()

    with registry.mutation_scope(True):
        authorized_schema_names = {
            schema["function"]["name"] for schema in registry.schemas()
        }
        assert "workspace_write" in authorized_schema_names
        ok, _ = registry.execute(
            "workspace_write",
            {"path": "notes/a.txt", "content": "hello"},
        )
    assert ok
    assert "workspace_write" not in {
        schema["function"]["name"] for schema in registry.schemas()
    }
    ok, result = registry.execute("workspace_read", {"path": "notes/a.txt"})
    assert ok
    assert "hello" in result
