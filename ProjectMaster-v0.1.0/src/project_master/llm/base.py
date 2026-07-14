from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from project_master.core.models import Message


class ChatProvider(Protocol):
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Return one assistant message."""

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[Message]:
        """Yield assistant message fragments."""
