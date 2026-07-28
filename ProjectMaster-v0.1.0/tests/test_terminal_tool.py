from __future__ import annotations

import json
import platform
from pathlib import Path

import pytest

from project_master.tools.base import ToolRegistry
from project_master.tools.terminal import (
    TerminalPolicy,
    WorkspaceTerminal,
    register_terminal_tool,
)


def _terminal(tmp_path: Path, *, enabled: bool = True) -> WorkspaceTerminal:
    return WorkspaceTerminal(
        TerminalPolicy(
            workspace_root=tmp_path / "workspace",
            enabled=enabled,
            network_enabled=False,
            max_timeout_seconds=5,
        )
    )


def test_workspace_terminal_runs_argv_without_a_shell(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path)
    (terminal.root / "hello.txt").write_text("hello sandbox\n", encoding="utf-8")

    result = terminal.run(["cat", "hello.txt"])

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello sandbox\n"
    assert result["network"] is False
    assert result["sandbox"] in {"bubblewrap", "read-only-command-allowlist"}


def test_workspace_terminal_cannot_escape_cwd(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        terminal.run(["pwd"], cwd="../")


def test_workspace_terminal_rejects_shell_and_host_executables(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path)

    with pytest.raises(ValueError, match="Relative executable"):
        terminal.run(["./script.sh"])
    with pytest.raises(ValueError, match="Absolute executables"):
        terminal.run(["/tmp/script.sh"])


def test_workspace_terminal_is_disabled_unless_explicitly_enabled(
    tmp_path: Path,
) -> None:
    terminal = _terminal(tmp_path, enabled=False)

    with pytest.raises(PermissionError, match="disabled"):
        terminal.run(["pwd"])


def test_workspace_terminal_rejects_network_without_separate_policy(
    tmp_path: Path,
) -> None:
    terminal = _terminal(tmp_path)

    with pytest.raises(PermissionError, match="Network access is disabled"):
        terminal.run(["pwd"], network=True)


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux bubblewrap isolation test")
def test_bubblewrap_hides_host_home(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path)
    if terminal.bwrap is None:
        pytest.skip("bubblewrap is not installed")

    result = terminal.run(["ls", "/home"])

    assert result["exit_code"] == 0
    assert "mike" not in result["stdout"]


def test_terminal_tool_returns_structured_result(tmp_path: Path) -> None:
    terminal = _terminal(tmp_path)
    registry = ToolRegistry()
    register_terminal_tool(registry, terminal)

    with registry.mutation_scope(True):
        ok, payload = registry.execute("terminal_run", {"argv": ["pwd"]})

    assert ok is True
    parsed = json.loads(payload)
    assert parsed["exit_code"] == 0
    assert parsed["cwd"] == "."
