from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_master.agent import ProjectMasterAgent, _looks_like_internal_deliberation
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.builtin import build_registry


def _write_call() -> Message:
    return Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "function": {
                    "name": "workspace_write",
                    "arguments": {
                        "path": "loop.txt",
                        "content": "x",
                        "mode": "append",
                    },
                }
            }
        ],
    )


class RepeatingWriteProvider:
    model = "repeating-test-model"

    def __init__(self) -> None:
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages
        self.chat_calls += 1
        if tools is None:
            return Message(role="assistant", content="The file was written once.")
        return _write_call()

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[Message]:
        del messages, tools
        self.stream_calls += 1
        yield _write_call()


class UniqueToolProvider:
    model = "unique-test-model"

    def __init__(self) -> None:
        self.round = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages
        if tools is None:
            return Message(role="assistant", content="Two calculations were verified.")
        self.round += 1
        return Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "function": {
                        "name": "calculator",
                        "arguments": {"expression": f"{self.round}+1"},
                    }
                }
            ],
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[Message]:
        yield self.chat(messages, tools)


class InterveningToolProvider:
    model = "intervening-test-model"

    def __init__(self) -> None:
        self.round = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages, tools
        expressions = ("1+1", "2+2", "1+1")
        if self.round >= len(expressions):
            return Message(role="assistant", content="All three calls completed.")
        expression = expressions[self.round]
        self.round += 1
        return Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "function": {
                        "name": "calculator",
                        "arguments": {"expression": expression},
                    }
                }
            ],
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[Message]:
        yield self.chat(messages, tools)


class DeliberativeFinalizerProvider:
    model = "deliberative-finalizer-model"

    def __init__(self) -> None:
        self.finalization_calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages
        if tools is not None:
            return _write_call()
        self.finalization_calls += 1
        if self.finalization_calls == 1:
            return Message(
                role="assistant",
                content=(
                    "The task requires a final status. "
                    "I should write a concise response."
                ),
            )
        return Message(role="assistant", content="The file was written once.")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[Message]:
        yield self.chat(messages, tools)


class ImageCaptureProvider:
    model = "vision-test-model"

    def __init__(self) -> None:
        self.seen_messages: list[Message] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del tools
        self.seen_messages = messages
        return Message(role="assistant", content="I analyzed the attached image.")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> Iterator[Message]:
        yield self.chat(messages, tools)


def _agent(
    tmp_path: Path,
    provider: (
        RepeatingWriteProvider
        | UniqueToolProvider
        | InterveningToolProvider
        | DeliberativeFinalizerProvider
        | ImageCaptureProvider
    ),
    *,
    max_tool_rounds: int = 6,
) -> tuple[ProjectMasterAgent, SQLiteStore]:
    store = SQLiteStore(tmp_path / "master.db")
    return (
        ProjectMasterAgent(
            provider=provider,  # type: ignore[arg-type]
            tools=build_registry(store, tmp_path / "workspace", allow_file_writes=True),
            store=store,
            profiler=StyleProfiler(store),
            prompt_builder=PromptBuilder(),
            max_tool_rounds=max_tool_rounds,
        ),
        store,
    )


def test_current_turn_images_reach_provider_but_not_history(tmp_path: Path) -> None:
    provider = ImageCaptureProvider()
    agent, store = _agent(tmp_path, provider)
    session_id = store.create_session()

    answer, executions = agent.respond(
        session_id,
        "Describe this image.",
        images=("dHJhbnNpZW50LWltYWdl",),
    )

    assert answer == "I analyzed the attached image."
    assert executions == []
    assert [
        (message.role, message.images)
        for message in provider.seen_messages
        if message.images
    ] == [("user", ("dHJhbnNpZW50LWltYWdl",))]
    assert store.recent_messages(session_id) == [
        {"role": "user", "content": "Describe this image."},
        {"role": "assistant", "content": "I analyzed the attached image."},
    ]


def test_duplicate_mutating_tool_call_is_suppressed_and_finalized(tmp_path: Path) -> None:
    provider = RepeatingWriteProvider()
    agent, store = _agent(tmp_path, provider)

    answer, executions = agent.respond(
        store.create_session(),
        "Write one x.",
        allow_mutations=True,
    )

    assert answer == "The file was written once."
    assert provider.chat_calls == 3
    assert [execution.ok for execution in executions] == [True, False]
    assert "duplicate_tool_call_suppressed" in executions[-1].result
    assert (tmp_path / "workspace" / "loop.txt").read_text(encoding="utf-8") == "x"


def test_streaming_duplicate_mutating_tool_call_is_suppressed(tmp_path: Path) -> None:
    provider = RepeatingWriteProvider()
    agent, store = _agent(tmp_path, provider)

    events = list(
        agent.respond_stream(
            store.create_session(),
            "Write one x.",
            allow_mutations=True,
        )
    )

    assert provider.stream_calls == 2
    assert provider.chat_calls == 1
    assert [event["type"] for event in events] == ["tool", "tool", "token", "done"]
    assert events[-1]["content"] == "The file was written once."
    assert "duplicate_tool_call_suppressed" in events[1]["tool"]["result"]
    assert (tmp_path / "workspace" / "loop.txt").read_text(encoding="utf-8") == "x"


def test_tool_budget_exhaustion_gets_one_tool_free_finalization(tmp_path: Path) -> None:
    provider = UniqueToolProvider()
    agent, store = _agent(tmp_path, provider, max_tool_rounds=2)

    answer, executions = agent.respond(store.create_session(), "Calculate twice.")

    assert answer == "Two calculations were verified."
    assert len(executions) == 2
    assert all(execution.ok for execution in executions)


def test_tool_finalizer_internal_deliberation_is_repaired(tmp_path: Path) -> None:
    provider = DeliberativeFinalizerProvider()
    agent, store = _agent(tmp_path, provider, max_tool_rounds=1)

    answer, executions = agent.respond(
        store.create_session(),
        "Write one x.",
        allow_mutations=True,
    )

    assert answer == "The file was written once."
    assert len(executions) == 1
    assert executions[0].ok
    assert provider.finalization_calls == 2
    assert (tmp_path / "workspace" / "loop.txt").read_text(encoding="utf-8") == "x"


EXPECTED_ZERO_TOOL_BUDGET_WARNING = (
    "No tool action was executed because the configured tool-call budget was exhausted. "
    "The requested result is not verified complete."
)


def test_sync_zero_tool_budget_blocks_calls_without_blocking_model_response(
    tmp_path: Path,
) -> None:
    provider = RepeatingWriteProvider()
    agent, store = _agent(tmp_path, provider, max_tool_rounds=0)

    answer, executions = agent.respond(
        store.create_session(),
        "Write one x.",
        allow_mutations=True,
    )

    assert answer == EXPECTED_ZERO_TOOL_BUDGET_WARNING
    assert executions == []
    assert provider.chat_calls == 1
    assert not (tmp_path / "workspace" / "loop.txt").exists()


def test_streaming_zero_tool_budget_blocks_calls_without_blocking_model_response(
    tmp_path: Path,
) -> None:
    provider = RepeatingWriteProvider()
    agent, store = _agent(tmp_path, provider, max_tool_rounds=0)

    events = list(
        agent.respond_stream(
            store.create_session(),
            "Write one x.",
            allow_mutations=True,
        )
    )

    assert events == [
        {"type": "token", "content": EXPECTED_ZERO_TOOL_BUDGET_WARNING},
        {"type": "done", "content": EXPECTED_ZERO_TOOL_BUDGET_WARNING},
    ]
    assert provider.stream_calls == 1
    assert provider.chat_calls == 0
    assert not (tmp_path / "workspace" / "loop.txt").exists()


def test_same_call_after_an_intervening_tool_is_not_suppressed(tmp_path: Path) -> None:
    provider = InterveningToolProvider()
    agent, store = _agent(tmp_path, provider)

    answer, executions = agent.respond(store.create_session(), "Run three calculations.")

    assert answer == "All three calls completed."
    assert [execution.arguments["expression"] for execution in executions] == [
        "1+1",
        "2+2",
        "1+1",
    ]
    assert all(execution.ok for execution in executions)


class ScriptedTextProvider:
    """Yields a scripted reply per call so continuation can be observed."""

    model = "scripted-test-model"

    def __init__(
        self,
        replies: list[str],
        finish_reasons: list[str | None] | None = None,
    ) -> None:
        self.replies = replies
        self.finish_reasons = finish_reasons or []
        self.calls: list[list[Message]] = []

    def _next(self, messages: list[Message]) -> Message:
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        finish_reason = (
            self.finish_reasons[min(index, len(self.finish_reasons) - 1)]
            if self.finish_reasons
            else None
        )
        return Message(
            role="assistant",
            content=self.replies[index],
            finish_reason=finish_reason,
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        return self._next(messages)

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: Any = None,
    ) -> Iterator[Message]:
        yield self._next(messages)


class ContinuationThenToolsProvider:
    """Promises text once, then keeps requesting distinct calculator calls."""

    model = "continuation-then-tools-model"

    def __init__(self) -> None:
        self.main_calls = 0
        self.stream_calls = 0
        self.finalization_calls = 0

    def _next_main(self) -> Message:
        self.main_calls += 1
        if self.main_calls == 1:
            return Message(
                role="assistant",
                content="I'll now perform the requested calculations.",
            )
        return Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "function": {
                        "name": "calculator",
                        "arguments": {"expression": f"{self.main_calls}+1"},
                    }
                }
            ],
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        if tools is None:
            self.finalization_calls += 1
            assert "tool-call budget was exhausted" in messages[-1].content
            return Message(
                role="assistant",
                content="One calculation was verified before the tool limit.",
            )
        return self._next_main()

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: Any = None,
    ) -> Iterator[Message]:
        del messages, cancellation
        assert tools is not None
        self.stream_calls += 1
        yield self._next_main()


class UnsupportedMarkerProvider:
    """Emits raw tool syntax alongside a call that must never execute."""

    model = "unsupported-marker-model"
    raw_attempt = (
        '<|tool_call|>{"name":"workspace_write","arguments":'
        '{"path":"unsupported-marker.txt","content":"ran","mode":"overwrite"}}'
    )

    def __init__(self, *, marker_phase: str = "main") -> None:
        self.marker_phase = marker_phase
        self.main_calls = 0
        self.finalization_calls = 0

    @staticmethod
    def _structured_call() -> list[dict[str, Any]]:
        return [
            {
                "function": {
                    "name": "workspace_write",
                    "arguments": {
                        "path": "unsupported-marker.txt",
                        "content": "ran",
                        "mode": "overwrite",
                    },
                }
            }
        ]

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del messages
        if tools is None:
            self.finalization_calls += 1
            return Message(role="assistant", content=self.raw_attempt)
        self.main_calls += 1
        return Message(
            role="assistant",
            content=self.raw_attempt if self.marker_phase == "main" else "",
            tool_calls=self._structured_call(),
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: Any = None,
    ) -> Iterator[Message]:
        del messages, cancellation
        assert tools is not None
        self.main_calls += 1
        if self.marker_phase == "main":
            split = self.raw_attempt.index("call")
            yield Message(role="assistant", content=self.raw_attempt[:split])
            yield Message(
                role="assistant",
                content=self.raw_attempt[split:],
                tool_calls=self._structured_call(),
            )
        else:
            yield Message(
                role="assistant",
                content="",
                tool_calls=self._structured_call(),
            )


def _text_agent(
    tmp_path: Path,
    provider: (
        ScriptedTextProvider
        | ContinuationThenToolsProvider
        | UnsupportedMarkerProvider
    ),
    *,
    max_auto_continuations: int = 2,
    max_tool_rounds: int = 6,
) -> tuple[ProjectMasterAgent, SQLiteStore]:
    store = SQLiteStore(tmp_path / "master.db")
    return (
        ProjectMasterAgent(
            provider=provider,  # type: ignore[arg-type]
            tools=build_registry(
                store,
                tmp_path / "workspace",
                allow_file_writes=True,
            ),
            store=store,
            profiler=StyleProfiler(store),
            prompt_builder=PromptBuilder(),
            max_auto_continuations=max_auto_continuations,
            max_tool_rounds=max_tool_rounds,
        ),
        store,
    )


def test_announced_but_undelivered_work_is_continued(tmp_path: Path) -> None:
    """A promise to write in parts must trigger delivery of the missing part."""
    provider = ScriptedTextProvider(
        [
            "I'll write it in two parts, keeping the text concise.",
            "Part two: the actual delivered content, complete.",
        ]
    )
    agent, store = _text_agent(tmp_path, provider)

    events = list(agent.respond_stream(store.create_session(), "Write a story."))

    done = [event for event in events if event["type"] == "done"]
    assert len(done) == 1
    assert done[0]["content"] == (
        "I'll write it in two parts, keeping the text concise.\n\n"
        "Part two: the actual delivered content, complete."
    )
    assert len(provider.calls) == 2
    # The nudge is appended to the conversation the model sees.
    assert "Continue from exactly where you stopped" in provider.calls[1][-1].content


def test_sync_announced_but_undelivered_work_is_continued(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(
        [
            "I'll write it in two parts, keeping the text concise.",
            "Part two: the actual delivered content, complete.",
        ]
    )
    agent, store = _text_agent(tmp_path, provider)
    session_id = store.create_session()

    answer, executions = agent.respond(session_id, "Write a story.")

    assert executions == []
    assert answer == (
        "I'll write it in two parts, keeping the text concise.\n\n"
        "Part two: the actual delivered content, complete."
    )
    assert len(provider.calls) == 2
    assert "Continue from exactly where you stopped" in provider.calls[1][-1].content
    assert store.recent_messages(session_id)[-1] == {
        "role": "assistant",
        "content": answer,
    }


def test_a_complete_answer_is_not_continued(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(
        ["The capital of France is Paris."],
        finish_reasons=["stop"],
    )
    agent, store = _text_agent(tmp_path, provider)

    events = list(agent.respond_stream(store.create_session(), "Capital of France?"))

    assert len(provider.calls) == 1
    done = [event for event in events if event["type"] == "done"]
    assert done[0]["content"] == "The capital of France is Paris."


def test_sync_length_stop_continues_despite_terminal_punctuation(
    tmp_path: Path,
) -> None:
    provider = ScriptedTextProvider(
        [
            "The first section happened to end at a sentence boundary.",
            "The remaining section is now complete.",
        ],
        finish_reasons=["length", "stop"],
    )
    agent, store = _text_agent(tmp_path, provider)

    answer, executions = agent.respond(store.create_session(), "Write both sections.")

    assert executions == []
    assert answer == (
        "The first section happened to end at a sentence boundary.\n\n"
        "The remaining section is now complete."
    )
    assert len(provider.calls) == 2
    assert "Continue from exactly where you stopped" in provider.calls[1][-1].content


def test_streaming_length_stop_continues_despite_terminal_punctuation(
    tmp_path: Path,
) -> None:
    provider = ScriptedTextProvider(
        [
            "The first section happened to end at a sentence boundary.",
            "The remaining section is now complete.",
        ],
        finish_reasons=["length", "stop"],
    )
    agent, store = _text_agent(tmp_path, provider)

    events = list(agent.respond_stream(store.create_session(), "Write both sections."))

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[-1]["content"] == (
        "The first section happened to end at a sentence boundary.\n\n"
        "The remaining section is now complete."
    )
    assert len(provider.calls) == 2


def test_repeated_length_stops_use_the_bounded_continuation_budget(
    tmp_path: Path,
) -> None:
    provider = ScriptedTextProvider(
        ["Every truncated round ends with punctuation."],
        finish_reasons=["length"],
    )
    agent, store = _text_agent(tmp_path, provider, max_auto_continuations=2)

    answer, _ = agent.respond(store.create_session(), "Write a long answer.")

    assert len(provider.calls) == 3
    assert answer.endswith("The response above may be incomplete.")


def test_internal_deliberation_detector_requires_multiple_strong_signals() -> None:
    flagged = [
        (
            "The prompt is a single line requesting a story. "
            "I'll write it in two parts and begin with setup."
        ),
        "Give them exactly what was promised.\n\n**Plan:**\n- Draft the answer.",
        "The user has asked for an explanation. I should write a concise response.",
    ]
    for sample in flagged:
        assert _looks_like_internal_deliberation(sample)

    assert not _looks_like_internal_deliberation(
        "Plan:\n1. Confirm the scope.\n2. Implement the change."
    )
    assert not _looks_like_internal_deliberation(
        "The user interface now keeps the send button visible."
    )


def test_sync_internal_deliberation_is_repaired_before_storage(tmp_path: Path) -> None:
    raw_draft = (
        "The prompt is a single line requesting a story. "
        "I'll write the answer in two parts."
    )
    provider = ScriptedTextProvider(
        [raw_draft, "Here is the complete user-facing story."]
    )
    agent, store = _text_agent(tmp_path, provider)
    session_id = store.create_session()

    answer, executions = agent.respond(session_id, "Write a story.")

    assert executions == []
    assert answer == "Here is the complete user-facing story."
    assert len(provider.calls) == 2
    assert "internal task deliberation" in provider.calls[1][-1].content
    stored = store.recent_messages(session_id)
    assert stored[-1] == {"role": "assistant", "content": answer}
    assert all(raw_draft not in message["content"] for message in stored)


def test_streaming_internal_deliberation_is_never_emitted(tmp_path: Path) -> None:
    raw_draft = (
        "The user has asked for an explanation. "
        "I should write a concise response."
    )
    provider = ScriptedTextProvider(
        [raw_draft, "This is the concise, user-facing explanation."]
    )
    agent, store = _text_agent(tmp_path, provider)

    events = list(agent.respond_stream(store.create_session(), "Explain this."))

    assert events == [
        {
            "type": "token",
            "content": "This is the concise, user-facing explanation.",
        },
        {
            "type": "done",
            "content": "This is the concise, user-facing explanation.",
        },
    ]
    assert all(raw_draft not in event.get("content", "") for event in events)
    assert len(provider.calls) == 2


def test_failed_deliberation_repair_returns_safe_user_facing_message(
    tmp_path: Path,
) -> None:
    raw_draft = (
        "The prompt is asking for a result. "
        "I need to write the answer."
    )
    provider = ScriptedTextProvider([raw_draft])
    agent, store = _text_agent(tmp_path, provider)

    answer, _ = agent.respond(store.create_session(), "Give me the result.")

    assert len(provider.calls) == 2
    assert answer == (
        "The selected model produced internal planning instead of a user-facing "
        "answer after one bounded repair attempt. Please retry or choose another model."
    )
    assert raw_draft not in answer


def test_tagged_private_reasoning_is_removed_without_an_extra_call(
    tmp_path: Path,
) -> None:
    provider = ScriptedTextProvider(
        ["<think>private trace</think>The public answer is ready."]
    )
    agent, store = _text_agent(tmp_path, provider)

    answer, _ = agent.respond(store.create_session(), "Answer.")

    assert answer == "The public answer is ready."
    assert "private trace" not in answer
    assert len(provider.calls) == 1


def test_length_limited_deliberation_repair_continues_without_raw_draft(
    tmp_path: Path,
) -> None:
    raw_draft = (
        "The task requires two sections. "
        "I should write both sections now."
    )
    provider = ScriptedTextProvider(
        [
            raw_draft,
            "The first user-facing section ends here.",
            "The second user-facing section completes the answer.",
        ],
        finish_reasons=["stop", "length", "stop"],
    )
    agent, store = _text_agent(tmp_path, provider)

    answer, _ = agent.respond(store.create_session(), "Write two sections.")

    assert answer == (
        "The first user-facing section ends here.\n\n"
        "The second user-facing section completes the answer."
    )
    assert len(provider.calls) == 3
    assert all(
        raw_draft not in message.content
        for message in provider.calls[2]
    )
    assert "Continue from exactly where you stopped" in provider.calls[2][-1].content


def test_audit_repair_cannot_publish_internal_deliberation(tmp_path: Path) -> None:
    raw_draft = (
        "The user has asked for a corrected response. "
        "I should write it without the unsupported attribution."
    )
    provider = ScriptedTextProvider(
        [
            "You said that the moon is made of cheese.",
            raw_draft,
            "The Moon is a rocky natural satellite.",
        ]
    )
    agent, store = _text_agent(tmp_path, provider)
    session_id = store.create_session()

    answer, _ = agent.respond(session_id, "Tell me about the Moon.")

    assert answer == "The Moon is a rocky natural satellite."
    assert len(provider.calls) == 3
    assert raw_draft not in answer
    assert all(
        raw_draft not in message["content"]
        for message in store.recent_messages(session_id)
    )


def test_streaming_continuation_is_bounded(tmp_path: Path) -> None:
    """A model that keeps promising must not loop forever."""
    provider = ScriptedTextProvider(["I'll begin writing the next section now."])
    agent, store = _text_agent(tmp_path, provider, max_auto_continuations=2)

    events = list(agent.respond_stream(store.create_session(), "Write a story."))

    # One initial turn plus at most max_auto_continuations retries.
    assert len(provider.calls) == 3
    assert events[-1]["content"].endswith(
        "The response above may be incomplete."
    )


def test_sync_continuation_is_bounded(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(["I'll begin writing the next section now."])
    agent, store = _text_agent(tmp_path, provider, max_auto_continuations=2)

    answer, _ = agent.respond(store.create_session(), "Write a story.")

    assert len(provider.calls) == 3
    assert answer.endswith(
        "The response above may be incomplete."
    )


def test_streaming_continuation_is_disabled_when_budget_is_zero(
    tmp_path: Path,
) -> None:
    provider = ScriptedTextProvider(["I'll write it in two parts."])
    agent, store = _text_agent(tmp_path, provider, max_auto_continuations=0)

    events = list(agent.respond_stream(store.create_session(), "Write a story."))

    assert len(provider.calls) == 1
    assert events[-1]["content"].endswith(
        "The response above may be incomplete."
    )


def test_sync_continuation_is_disabled_when_budget_is_zero(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(["I'll write it in two parts."])
    agent, store = _text_agent(tmp_path, provider, max_auto_continuations=0)

    answer, _ = agent.respond(store.create_session(), "Write a story.")

    assert len(provider.calls) == 1
    assert answer.endswith(
        "The response above may be incomplete."
    )


def test_sync_continuation_does_not_require_tool_budget(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(
        [
            "I'll write it in two parts.",
            "Part two: delivered without using a tool.",
        ]
    )
    agent, store = _text_agent(
        tmp_path,
        provider,
        max_auto_continuations=1,
        max_tool_rounds=0,
    )

    answer, executions = agent.respond(store.create_session(), "Write a story.")

    assert answer == (
        "I'll write it in two parts.\n\nPart two: delivered without using a tool."
    )
    assert executions == []
    assert len(provider.calls) == 2


def test_streaming_continuation_does_not_require_tool_budget(tmp_path: Path) -> None:
    provider = ScriptedTextProvider(
        [
            "I'll write it in two parts.",
            "Part two: delivered without using a tool.",
        ]
    )
    agent, store = _text_agent(
        tmp_path,
        provider,
        max_auto_continuations=1,
        max_tool_rounds=0,
    )

    events = list(agent.respond_stream(store.create_session(), "Write a story."))

    assert events == [
        {
            "type": "token",
            "content": (
                "I'll write it in two parts.\n\n"
                "Part two: delivered without using a tool."
            ),
        },
        {
            "type": "done",
            "content": (
                "I'll write it in two parts.\n\n"
                "Part two: delivered without using a tool."
            ),
        },
    ]
    assert len(provider.calls) == 2


def test_sync_continuation_does_not_expand_tool_round_budget(tmp_path: Path) -> None:
    provider = ContinuationThenToolsProvider()
    agent, store = _text_agent(
        tmp_path,
        provider,
        max_auto_continuations=2,
        max_tool_rounds=1,
    )

    answer, executions = agent.respond(store.create_session(), "Calculate twice.")

    assert answer == "One calculation was verified before the tool limit."
    assert len(executions) == 1
    assert executions[0].ok
    assert provider.main_calls == 2
    assert provider.finalization_calls == 1


def test_streaming_continuation_does_not_expand_tool_round_budget(
    tmp_path: Path,
) -> None:
    provider = ContinuationThenToolsProvider()
    agent, store = _text_agent(
        tmp_path,
        provider,
        max_auto_continuations=2,
        max_tool_rounds=1,
    )

    events = list(agent.respond_stream(store.create_session(), "Calculate twice."))

    assert [event["type"] for event in events] == ["tool", "token", "done"]
    assert events[-1]["content"] == "One calculation was verified before the tool limit."
    assert provider.main_calls == 2
    assert provider.stream_calls == 2
    assert provider.finalization_calls == 1


EXPECTED_UNSUPPORTED_TOOL_WARNING = (
    "The selected model attempted a tool call in an unsupported format. "
    "Project Master blocked that attempt, executed nothing from it, and cannot "
    "verify a result. Try a model whose tool calls are supported."
)


def test_sync_unsupported_tool_marker_is_blocked_and_stored(tmp_path: Path) -> None:
    provider = UnsupportedMarkerProvider()
    agent, store = _text_agent(tmp_path, provider, max_tool_rounds=1)
    session_id = store.create_session()

    answer, executions = agent.respond(
        session_id,
        "Write the file.",
        allow_mutations=True,
    )

    assert answer == EXPECTED_UNSUPPORTED_TOOL_WARNING
    assert executions == []
    assert provider.main_calls == 1
    assert provider.finalization_calls == 0
    assert "<|tool_call|>" not in answer
    assert not (tmp_path / "workspace" / "unsupported-marker.txt").exists()
    stored = store.recent_messages(session_id)
    assert stored[-1] == {"role": "assistant", "content": answer}
    assert all("<|tool_call|>" not in message["content"] for message in stored)


def test_streaming_unsupported_tool_marker_is_blocked_and_stored(
    tmp_path: Path,
) -> None:
    provider = UnsupportedMarkerProvider()
    agent, store = _text_agent(tmp_path, provider, max_tool_rounds=1)
    session_id = store.create_session()

    events = list(
        agent.respond_stream(
            session_id,
            "Write the file.",
            allow_mutations=True,
        )
    )

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[-1]["content"] == EXPECTED_UNSUPPORTED_TOOL_WARNING
    assert provider.main_calls == 1
    assert provider.finalization_calls == 0
    assert all("<|tool_call|>" not in event["content"] for event in events)
    assert not (tmp_path / "workspace" / "unsupported-marker.txt").exists()
    stored = store.recent_messages(session_id)
    assert stored[-1] == {
        "role": "assistant",
        "content": EXPECTED_UNSUPPORTED_TOOL_WARNING,
    }
    assert all("<|tool_call|>" not in message["content"] for message in stored)


def test_sync_unsupported_marker_from_finalizer_is_not_leaked(tmp_path: Path) -> None:
    provider = UnsupportedMarkerProvider(marker_phase="finalization")
    agent, store = _text_agent(tmp_path, provider, max_tool_rounds=1)
    session_id = store.create_session()

    answer, executions = agent.respond(
        session_id,
        "Write the file.",
        allow_mutations=True,
    )

    assert answer == EXPECTED_UNSUPPORTED_TOOL_WARNING
    assert len(executions) == 1
    assert executions[0].ok
    assert provider.main_calls == 1
    assert provider.finalization_calls == 1
    assert (
        tmp_path / "workspace" / "unsupported-marker.txt"
    ).read_text(encoding="utf-8") == "ran"
    stored = store.recent_messages(session_id)
    assert stored[-1] == {"role": "assistant", "content": answer}
    assert all("<|tool_call|>" not in message["content"] for message in stored)


def test_streaming_unsupported_marker_from_finalizer_is_not_leaked(
    tmp_path: Path,
) -> None:
    provider = UnsupportedMarkerProvider(marker_phase="finalization")
    agent, store = _text_agent(tmp_path, provider, max_tool_rounds=1)
    session_id = store.create_session()

    events = list(
        agent.respond_stream(
            session_id,
            "Write the file.",
            allow_mutations=True,
        )
    )

    assert [event["type"] for event in events] == ["tool", "token", "done"]
    assert events[-1]["content"] == EXPECTED_UNSUPPORTED_TOOL_WARNING
    assert provider.main_calls == 1
    assert provider.finalization_calls == 1
    assert (
        tmp_path / "workspace" / "unsupported-marker.txt"
    ).read_text(encoding="utf-8") == "ran"
    assert all("<|tool_call|>" not in event.get("content", "") for event in events)
    stored = store.recent_messages(session_id)
    assert stored[-1] == {
        "role": "assistant",
        "content": EXPECTED_UNSUPPORTED_TOOL_WARNING,
    }
    assert all("<|tool_call|>" not in message["content"] for message in stored)
