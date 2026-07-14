from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Handler = Callable[[dict[str, Any]], Any]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler

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

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.ollama_schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def execute(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        tool = self._tools.get(name)
        if tool is None:
            return False, json.dumps({"error": f"Unknown tool: {name}"})
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
