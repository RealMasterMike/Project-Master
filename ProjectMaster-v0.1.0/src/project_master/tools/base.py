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
    external_network: bool = False
    requires_workspace: bool = False

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
        self._project_id: ContextVar[str | None] = ContextVar(
            f"project_master_tool_project_{id(self)}",
            default=None,
        )
        self._workspace_available: ContextVar[bool] = ContextVar(
            f"project_master_tool_workspace_available_{id(self)}",
            default=True,
        )
        self._allow_mutations: ContextVar[bool] = ContextVar(
            f"project_master_tool_mutations_{id(self)}",
            default=False,
        )
        self._allow_external_network: ContextVar[bool] = ContextVar(
            f"project_master_tool_external_network_{id(self)}",
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
            if self.external_network_allowed or not tool.external_network
            if self.workspace_available or not tool.requires_workspace
        ]

    def inventory(self) -> list[dict[str, Any]]:
        """Describe every registered tool, including tools hidden by the current scope."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "mutating": tool.mutating,
                "external_network": tool.external_network,
                "requires_workspace": tool.requires_workspace,
                "risk": (
                    "mutating"
                    if tool.mutating
                    else "external_network"
                    if tool.external_network
                    else "read_only"
                ),
                "requires_explicit_chat_authorization": (
                    tool.mutating or tool.external_network
                ),
                "available_in_default_chat": (
                    not tool.mutating and not tool.external_network
                ),
                "available_in_current_scope": (
                    (self.mutations_allowed or not tool.mutating)
                    and (
                        self.external_network_allowed
                        or not tool.external_network
                    )
                    and (
                        self.workspace_available
                        or not tool.requires_workspace
                    )
                ),
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return sorted(self._tools)

    @property
    def workspace_root(self) -> Path | None:
        return self._workspace_root.get()

    @property
    def project_id(self) -> str | None:
        return self._project_id.get()

    @property
    def workspace_available(self) -> bool:
        return self._workspace_available.get()

    @property
    def mutations_allowed(self) -> bool:
        return self._allow_mutations.get()

    @property
    def external_network_allowed(self) -> bool:
        return self._allow_external_network.get()

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
    def project_scope(
        self,
        project_id: str,
        *,
        workspace_available: bool,
    ) -> Any:
        """Scope project-aware tools and declare whether that project has a workspace."""
        if not project_id.strip():
            raise ValueError("Project ID cannot be empty.")
        project_token = self._project_id.set(project_id)
        workspace_token = self._workspace_available.set(bool(workspace_available))
        try:
            yield
        finally:
            self._workspace_available.reset(workspace_token)
            self._project_id.reset(project_token)

    @contextmanager
    def mutation_scope(self, allow_mutations: bool) -> Any:
        """Apply one explicit chat request's mutation permission to its tool loop."""
        token = self._allow_mutations.set(bool(allow_mutations))
        try:
            yield
        finally:
            self._allow_mutations.reset(token)

    @contextmanager
    def external_network_scope(self, allow_external_network: bool) -> Any:
        """Apply one explicit chat request's network permission to its tool loop."""
        token = self._allow_external_network.set(bool(allow_external_network))
        try:
            yield
        finally:
            self._allow_external_network.reset(token)

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        tool = self._tools.get(name)
        if tool is None:
            return False, json.dumps({"error": f"Unknown tool: {name}"})
        if tool.requires_workspace and not self.workspace_available:
            return False, json.dumps(
                {
                    "error": "PermissionError",
                    "message": (
                        f"Workspace tool '{name}' is unavailable because the "
                        "selected project has no local workspace."
                    ),
                },
                ensure_ascii=False,
            )
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
        if tool.external_network and not self.external_network_allowed:
            return False, json.dumps(
                {
                    "error": "PermissionError",
                    "message": (
                        f"Network tool '{name}' requires explicit web-access "
                        "authorization for this chat request."
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
