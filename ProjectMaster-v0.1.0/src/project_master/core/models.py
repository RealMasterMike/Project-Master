from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_name: str | None = None

    def to_ollama(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        return payload


@dataclass(slots=True)
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool


@dataclass(slots=True)
class AuditFinding:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
