from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message, ToolExecution
from project_master.core.prompting import PromptBuilder
from project_master.llm.base import ChatProvider
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.base import ToolRegistry


class ProjectMasterAgent:
    def __init__(
        self,
        provider: ChatProvider,
        tools: ToolRegistry,
        store: SQLiteStore,
        profiler: StyleProfiler,
        prompt_builder: PromptBuilder,
        max_tool_rounds: int = 6,
        max_history_messages: int = 30,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.store = store
        self.profiler = profiler
        self.prompt_builder = prompt_builder
        self.max_tool_rounds = max_tool_rounds
        self.max_history_messages = max_history_messages

    def respond(self, session_id: str, user_text: str) -> tuple[str, list[ToolExecution]]:
        self.profiler.observe(user_text)
        self.store.add_message(session_id, "user", user_text)
        memory_context = self._memory_context(user_text)
        system_prompt = self.prompt_builder.build(self.profiler.profile, memory_context)

        history = self.store.recent_messages(session_id, self.max_history_messages)
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(Message(role=item["role"], content=item["content"]) for item in history)

        executions: list[ToolExecution] = []
        for _round in range(self.max_tool_rounds):
            assistant = self.provider.chat(messages, self.tools.schemas())
            messages.append(assistant)

            if not assistant.tool_calls:
                final = assistant.content.strip() or "I could not produce a response."
                self.store.add_message(session_id, "assistant", final)
                return final, executions

            for call in assistant.tool_calls:
                name, arguments = _parse_tool_call(call)
                ok, result = self.tools.execute(name, arguments)
                executions.append(
                    ToolExecution(name=name, arguments=arguments, result=result, ok=ok)
                )
                messages.append(Message(role="tool", content=result, tool_name=name))

        final = (
            "I reached the configured tool-call limit before producing a final answer. "
            "The partial tool results were preserved in this run, but completion is not verified."
        )
        self.store.add_message(session_id, "assistant", final)
        return final, executions

    def respond_stream(
        self,
        session_id: str,
        user_text: str,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Run the normal agent loop while yielding observable progress events."""
        self.profiler.observe(user_text)
        self.store.add_message(session_id, "user", user_text)
        memory_context = self._memory_context(user_text)
        system_prompt = self.prompt_builder.build(self.profiler.profile, memory_context)
        history = self.store.recent_messages(session_id, self.max_history_messages)
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(Message(role=item["role"], content=item["content"]) for item in history)

        for _round in range(self.max_tool_rounds):
            content_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for fragment in self.provider.chat_stream(
                messages, self.tools.schemas(), cancellation=cancellation
            ):
                if cancellation is not None and cancellation.cancelled:
                    yield {"type": "cancelled"}
                    return
                if fragment.content:
                    content_parts.append(fragment.content)
                    yield {"type": "token", "content": fragment.content}
                if fragment.tool_calls:
                    tool_calls.extend(fragment.tool_calls)

            if cancellation is not None and cancellation.cancelled:
                yield {"type": "cancelled"}
                return

            assistant = Message(
                role="assistant",
                content="".join(content_parts),
                tool_calls=tool_calls,
            )
            messages.append(assistant)
            if not assistant.tool_calls:
                final = assistant.content.strip() or "I could not produce a response."
                self.store.add_message(session_id, "assistant", final)
                yield {"type": "done", "content": final}
                return

            for call in assistant.tool_calls:
                if cancellation is not None and cancellation.cancelled:
                    yield {"type": "cancelled"}
                    return
                name, arguments = _parse_tool_call(call)
                ok, result = self.tools.execute(name, arguments)
                execution = ToolExecution(name=name, arguments=arguments, result=result, ok=ok)
                yield {
                    "type": "tool",
                    "tool": {
                        "name": execution.name,
                        "arguments": execution.arguments,
                        "result": execution.result,
                        "ok": execution.ok,
                    },
                }
                messages.append(Message(role="tool", content=result, tool_name=name))

        final = (
            "I reached the configured tool-call limit before producing a final answer. "
            "The partial tool results were preserved in this run, but completion is not verified."
        )
        self.store.add_message(session_id, "assistant", final)
        yield {"type": "done", "content": final}

    def _memory_context(self, user_text: str) -> str:
        terms = [word for word in user_text.split() if len(word) >= 5][:4]
        query = " ".join(terms)
        memories = self.store.recall(query=query, limit=8) if query else []
        if not memories:
            memories = self.store.recall(namespace="user_preference", limit=5)
        lines = []
        for item in memories:
            lines.append(
                f"- [{item['namespace']}] {item['key']} = {item['value']!r} "
                f"(source={item['source']}, confidence={item['confidence']:.2f})"
            )
        return "\n".join(lines)


def _parse_tool_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function", {})
    if not isinstance(function, dict):
        raise ValueError("Tool call is missing a function object")
    name = str(function.get("name", ""))
    if not name:
        raise ValueError("Tool call is missing a function name")
    raw_arguments = function.get("arguments", {})
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Tool arguments are not valid JSON: {raw_arguments}") from exc
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        raise ValueError("Tool arguments must be an object or JSON string")
    return name, arguments
