from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_master.tools.base import Tool, ToolRegistry

if os.name == "posix":
    import resource
else:  # pragma: no cover - exercised by Windows packaging smoke tests.
    resource = None  # type: ignore[assignment]

_MAX_OUTPUT_BYTES = 200_000
_READ_ONLY_PROGRAMS = {
    "basename",
    "cat",
    "cut",
    "date",
    "dirname",
    "du",
    "env",
    "head",
    "id",
    "ls",
    "md5sum",
    "nproc",
    "od",
    "pwd",
    "rg",
    "sha256sum",
    "sort",
    "stat",
    "tail",
    "uname",
    "uniq",
    "wc",
    "which",
}
_READ_ONLY_GIT_SUBCOMMANDS = {
    "branch",
    "diff",
    "grep",
    "log",
    "rev-parse",
    "show",
    "status",
}


@dataclass(frozen=True, slots=True)
class TerminalPolicy:
    workspace_root: Path
    enabled: bool = False
    network_enabled: bool = False
    max_timeout_seconds: float = 120.0


class WorkspaceTerminal:
    """Run argv-only commands in a bounded workspace sandbox."""

    def __init__(self, policy: TerminalPolicy) -> None:
        self.policy = policy
        self.root = policy.workspace_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        candidate = shutil.which("bwrap") if platform.system() == "Linux" else None
        self.bwrap = candidate if candidate and _bubblewrap_usable(candidate) else None

    @property
    def sandbox_kind(self) -> str:
        return "bubblewrap" if self.bwrap else "read-only-command-allowlist"

    def run(
        self,
        argv: list[str],
        *,
        cwd: str = ".",
        timeout_seconds: float = 30.0,
        network: bool = False,
        workspace_root: Path | None = None,
    ) -> dict[str, Any]:
        if not self.policy.enabled:
            raise PermissionError(
                "The workspace terminal is disabled. Enable it in Project Master settings."
            )
        clean_argv = _validate_argv(argv)
        root = (workspace_root or self.root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        target = self._safe_cwd(cwd, root)
        selected_timeout = min(
            max(float(timeout_seconds), 0.1),
            self.policy.max_timeout_seconds,
        )
        if network and not self.policy.network_enabled:
            raise PermissionError(
                "Network access is disabled for the workspace terminal. Use an explicitly "
                "enabled network integration instead."
            )
        if self.bwrap:
            command = self._bubblewrap_command(
                clean_argv,
                target,
                root,
                network=network,
            )
            command_cwd = root
        else:
            _validate_read_only_fallback(clean_argv)
            command = clean_argv
            command_cwd = target
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=command_cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_minimal_environment(root),
            start_new_session=True,
            text=False,
            preexec_fn=_limit_process if os.name == "posix" else None,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=selected_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
        stdout_text, stdout_truncated = _bounded_decode(stdout)
        stderr_text, stderr_truncated = _bounded_decode(stderr)
        return {
            "argv": clean_argv,
            "cwd": target.relative_to(root).as_posix() or ".",
            "exit_code": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_truncated": stdout_truncated or stderr_truncated,
            "sandbox": self.sandbox_kind,
            "network": network,
        }

    def _safe_cwd(self, value: str, root: Path) -> Path:
        relative = Path(value)
        if relative.is_absolute():
            raise ValueError("Terminal cwd must be relative to the workspace.")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Terminal cwd escapes the workspace.") from exc
        if not target.is_dir():
            raise FileNotFoundError(target)
        return target

    def _bubblewrap_command(
        self,
        argv: list[str],
        cwd: Path,
        root: Path,
        *,
        network: bool,
    ) -> list[str]:
        assert self.bwrap is not None
        relative_cwd = cwd.relative_to(root).as_posix()
        sandbox_cwd = "/workspace" if relative_cwd == "." else f"/workspace/{relative_cwd}"
        command = [
            self.bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
        ]
        if network:
            command.append("--share-net")
        command.extend(
            [
                "--ro-bind",
                "/usr",
                "/usr",
                "--ro-bind",
                "/etc",
                "/etc",
                "--symlink",
                "usr/bin",
                "/bin",
                "--symlink",
                "usr/lib",
                "/lib",
                "--symlink",
                "usr/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/run",
                "--dir",
                "/home",
                "--bind",
                str(root),
                "/workspace",
                "--chdir",
                sandbox_cwd,
                "--setenv",
                "HOME",
                "/workspace",
                "--setenv",
                "PATH",
                "/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--",
                *argv,
            ]
        )
        return command


def register_terminal_tool(
    registry: ToolRegistry,
    terminal: WorkspaceTerminal,
) -> None:
    registry.register(
        Tool(
            name="terminal_run",
            mutating=True,
            description=(
                "Run one argv-only command inside the Project Master workspace sandbox. Shell "
                "operators, host paths, background processes, and network access are unavailable."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 4_096},
                        "minItems": 1,
                        "maxItems": 64,
                        "description": (
                            "Executable and arguments as separate strings; do not include a shell."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "default": ".",
                        "description": "Working directory relative to the workspace root.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": terminal.policy.max_timeout_seconds,
                        "default": 30,
                    },
                    "network": {
                        "type": "boolean",
                        "default": False,
                        "description": "Requires a separately enabled network-terminal policy.",
                    },
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            handler=lambda args: terminal.run(
                [str(item) for item in args["argv"]],
                cwd=str(args.get("cwd", ".")),
                timeout_seconds=float(args.get("timeout_seconds", 30)),
                network=bool(args.get("network", False)),
                workspace_root=registry.workspace_root,
            ),
        )
    )


def _validate_argv(argv: list[str]) -> list[str]:
    if not argv or len(argv) > 64:
        raise ValueError("Terminal argv must contain between 1 and 64 items.")
    clean: list[str] = []
    for item in argv:
        value = str(item)
        if not value or len(value) > 4_096 or "\x00" in value:
            raise ValueError("Terminal arguments must be non-empty strings of at most 4096 bytes.")
        clean.append(value)
    executable = Path(clean[0])
    allowed_absolute_prefixes = ("/usr/bin/", "/usr/local/bin/")
    if executable.is_absolute() and not str(executable).startswith(
        allowed_absolute_prefixes
    ):
        raise ValueError("Absolute executables must be under /usr/bin or /usr/local/bin.")
    if "/" in clean[0] and not executable.is_absolute():
        raise ValueError("Relative executable paths are not accepted; use a command on PATH.")
    return clean


def _validate_read_only_fallback(argv: list[str]) -> None:
    program = Path(argv[0]).name
    if program in _READ_ONLY_PROGRAMS:
        if program in {"sed", "find"}:
            raise PermissionError("This command is unavailable without the Linux sandbox.")
        return
    if program == "git" and len(argv) >= 2 and argv[1] in _READ_ONLY_GIT_SUBCOMMANDS:
        if argv[1] == "branch" and "--show-current" not in argv:
            raise PermissionError("Only `git branch --show-current` is read-only here.")
        return
    raise PermissionError(
        "This platform has no supported process sandbox, so only explicitly read-only commands "
        "are available."
    )


def _minimal_environment(workspace: Path) -> dict[str, str]:
    return {
        "HOME": str(workspace),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", ""),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "TMPDIR": "/tmp",
    }


def _bounded_decode(value: bytes) -> tuple[str, bool]:
    truncated = len(value) > _MAX_OUTPUT_BYTES
    selected = value[:_MAX_OUTPUT_BYTES]
    return selected.decode("utf-8", errors="replace"), truncated


def _limit_process() -> None:
    if resource is None:  # pragma: no cover - preexec_fn is POSIX-only.
        return
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_FSIZE, (100_000_000, 100_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))


def _bubblewrap_usable(executable: str) -> bool:
    try:
        result = subprocess.run(
            [
                executable,
                "--die-with-parent",
                "--unshare-all",
                "--ro-bind",
                "/usr",
                "/usr",
                "--symlink",
                "usr/bin",
                "/bin",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--",
                "/usr/bin/true",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
