from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_master.agent import ProjectMasterAgent
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


def _agent(
    tmp_path: Path,
    provider: RepeatingWriteProvider | UniqueToolProvider | InterveningToolProvider,
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
