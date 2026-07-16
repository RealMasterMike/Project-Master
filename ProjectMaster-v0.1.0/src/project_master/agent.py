from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from project_master.communication.interpretation import (
    ConversationInterpretation,
    interpret_conversation,
)
from project_master.core.audit import audit_response
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message, ToolExecution
from project_master.core.prompting import PromptBuilder
from project_master.llm.base import ChatProvider
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.base import ToolRegistry

_EXPLICIT_MEMORY_REQUEST = re.compile(
    r"\b(?:please\s+)?(?:remember|save|store)\b|\bkeep\s+(?:this|that|it)\s+in\s+mind\b",
    re.IGNORECASE,
)


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
        messages, interpretation = self._prepare_turn(session_id, user_text)

        executions: list[ToolExecution] = []
        for _round in range(self.max_tool_rounds):
            assistant = self.provider.chat(messages, self.tools.schemas())
            messages.append(assistant)

            if not assistant.tool_calls:
                final = self._guard_final_response(assistant.content, messages, interpretation)
                self.store.add_message(session_id, "assistant", final)
                return final, executions

            for call in assistant.tool_calls:
                execution = self._execute_tool_call(call, user_text)
                executions.append(execution)
                messages.append(
                    Message(role="tool", content=execution.result, tool_name=execution.name)
                )

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
        messages, _interpretation = self._prepare_turn(session_id, user_text)

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
                execution = self._execute_tool_call(call, user_text)
                yield {
                    "type": "tool",
                    "tool": {
                        "name": execution.name,
                        "arguments": execution.arguments,
                        "result": execution.result,
                        "ok": execution.ok,
                    },
                }
                messages.append(
                    Message(role="tool", content=execution.result, tool_name=execution.name)
                )

        final = (
            "I reached the configured tool-call limit before producing a final answer. "
            "The partial tool results were preserved in this run, but completion is not verified."
        )
        self.store.add_message(session_id, "assistant", final)
        yield {"type": "done", "content": final}

    def _prepare_turn(
        self, session_id: str, user_text: str
    ) -> tuple[list[Message], ConversationInterpretation]:
        # Interpret against prior messages before appending the current one, so the distinction
        # between established context and the present statement remains explicit.
        history = self.store.recent_messages(session_id, self.max_history_messages)
        interpretation = interpret_conversation(history, user_text)
        self.profiler.observe(user_text)
        self.store.add_message(session_id, "user", user_text)
        memory_context = self._memory_context(user_text)
        system_prompt = self.prompt_builder.build(
            self.profiler.profile,
            memory_context,
            interpretation.prompt_summary(),
        )
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(Message(role=item["role"], content=item["content"]) for item in history)
        messages.append(Message(role="user", content=user_text))
        return messages, interpretation

    def _guard_final_response(
        self,
        draft: str,
        messages: list[Message],
        interpretation: ConversationInterpretation,
    ) -> str:
        final = draft.strip() or "I could not produce a response."
        findings = audit_response(final, interpretation)
        repairable = [
            item
            for item in findings
            if item.code
            in {
                "unsupported-user-attribution",
                "contradicts-established-project-context",
                "reintroduces-rejected-interpretation",
                "unsolicited-advice",
            }
        ]
        if not repairable:
            return final

        repair_prompt = Message(
            role="system",
            content=(
                "Rewrite the draft response below before it is shown to the user. Return only the "
                "replacement response. Preserve useful content, but remove unsupported "
                "attributions, contradictions with established context, repeated rejected "
                "interpretations, and advice that was not explicitly requested. Do not mention "
                "this audit or claim the user said anything they did not explicitly say.\n\n"
                "Draft response:\n"
                f"{final}\n\n"
                "Detected concerns:\n" + "\n".join(f"- {item.message}" for item in repairable)
            ),
        )
        repaired = self.provider.chat([*messages, repair_prompt], tools=None)
        if repaired.tool_calls or not repaired.content.strip():
            return final
        return repaired.content.strip()

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

    def _execute_tool_call(self, call: dict[str, Any], user_text: str) -> ToolExecution:
        name, arguments = _parse_tool_call(call)
        if name == "memory_remember":
            if not _EXPLICIT_MEMORY_REQUEST.search(user_text):
                result = json.dumps(
                    {
                        "stored": False,
                        "reason": (
                            "Durable memory requires an explicit user request to remember, "
                            "save, or store the information in the current message."
                        ),
                    }
                )
                return ToolExecution(name=name, arguments=arguments, result=result, ok=False)
            # The model may propose the key and value, but the durable record must make its
            # authorization visible during recall and later auditing.
            arguments = {**arguments, "source": "explicit_user_request"}

        ok, result = self.tools.execute(name, arguments)
        return ToolExecution(name=name, arguments=arguments, result=result, ok=ok)


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
