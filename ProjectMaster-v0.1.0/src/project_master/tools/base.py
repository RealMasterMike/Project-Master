from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Handler = Callable[[dict[str, Any]], Any]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    mutating: bool = False

    def ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._workspace_root: ContextVar[Path | None] = ContextVar(
            f"project_master_tool_workspace_{id(self)}",
            default=None,
        )
        self._allow_mutations: ContextVar[bool] = ContextVar(
            f"project_master_tool_mutations_{id(self)}",
            default=False,
        )

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [
            tool.ollama_schema()
            for tool in self._tools.values()
            if self.mutations_allowed or not tool.mutating
        ]

    def inventory(self) -> list[dict[str, Any]]:
        """Describe every registered tool, including tools hidden by the current scope."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "mutating": tool.mutating,
                "risk": "mutating" if tool.mutating else "read_only",
                "requires_explicit_chat_authorization": tool.mutating,
                "available_in_default_chat": not tool.mutating,
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return sorted(self._tools)

    @property
    def workspace_root(self) -> Path | None:
        return self._workspace_root.get()

    @property
    def mutations_allowed(self) -> bool:
        return self._allow_mutations.get()

    @contextmanager
    def workspace_scope(self, root: Path) -> Any:
        """Scope workspace-aware tools to one explicitly selected project root."""
        resolved = root.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(resolved)
        token = self._workspace_root.set(resolved)
        try:
            yield
        finally:
            self._workspace_root.reset(token)

    @contextmanager
    def mutation_scope(self, allow_mutations: bool) -> Any:
        """Apply one explicit chat request's mutation permission to its tool loop."""
        token = self._allow_mutations.set(bool(allow_mutations))
        try:
            yield
        finally:
            self._allow_mutations.reset(token)

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        tool = self._tools.get(name)
        if tool is None:
            return False, json.dumps({"error": f"Unknown tool: {name}"})
        if tool.mutating and not self.mutations_allowed:
            return False, json.dumps(
                {
                    "error": "PermissionError",
                    "message": (
                        f"Mutating tool '{name}' requires explicit authorization "
                        "for this chat request."
                    ),
                },
                ensure_ascii=False,
            )
        try:
            result = tool.handler(arguments)
            if isinstance(result, str):
                return True, result
            return True, json.dumps(result, ensure_ascii=False, default=str)
        except (
            Exception
        ) as exc:  # Tool errors must be returned to the model, not crash the session.
            return False, json.dumps(
                {"error": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            )
