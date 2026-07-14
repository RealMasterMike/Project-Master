from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_master.agent import ProjectMasterAgent
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.builtin import build_registry


class MemoryWritingProvider:
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "function": {
                            "name": "memory_remember",
                            "arguments": {
                                "namespace": "user_preference",
                                "key": "response_length",
                                "value": "concise",
                                "source": "assistant_inference",
                            },
                        }
                    }
                ],
            )
        return Message(role="assistant", content="Acknowledged.")

    def chat_stream(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> Iterator[Message]:
        self.calls += 1
        if self.calls == 1:
            yield Message(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "function": {
                            "name": "memory_remember",
                            "arguments": {
                                "namespace": "user_preference",
                                "key": "response_length",
                                "value": "concise",
                            },
                        }
                    }
                ],
            )
            return
        yield Message(role="assistant", content="Acknowledged.")


def _agent(tmp_path: Path) -> tuple[ProjectMasterAgent, SQLiteStore]:
    store = SQLiteStore(tmp_path / "test.db")
    agent = ProjectMasterAgent(
        provider=MemoryWritingProvider(),  # type: ignore[arg-type]
        tools=build_registry(store, tmp_path / "workspace"),
        store=store,
        profiler=StyleProfiler(store),
        prompt_builder=PromptBuilder(),
    )
    return agent, store


def test_model_cannot_promote_ordinary_chat_to_durable_memory(tmp_path: Path) -> None:
    agent, store = _agent(tmp_path)
    session_id = store.create_session()

    _answer, executions = agent.respond(
        session_id,
        "I am thinking out loud about whether concise answers would help.",
    )

    assert executions[0].name == "memory_remember"
    assert not executions[0].ok
    assert "explicit user request" in executions[0].result
    assert store.recall(namespace="user_preference") == []


def test_explicit_memory_request_is_recorded_as_user_authorized(tmp_path: Path) -> None:
    agent, store = _agent(tmp_path)
    session_id = store.create_session()

    _answer, executions = agent.respond(
        session_id,
        "Please remember that I prefer concise answers.",
    )

    assert executions[0].ok
    memory = store.recall(namespace="user_preference")[0]
    assert memory["value"] == "concise"
    assert memory["source"] == "explicit_user_request"


def test_streaming_model_cannot_promote_ordinary_chat_to_durable_memory(tmp_path: Path) -> None:
    agent, store = _agent(tmp_path)
    session_id = store.create_session()

    events = list(
        agent.respond_stream(
            session_id,
            "I am thinking out loud about whether concise answers would help.",
        )
    )

    tool_event = next(event for event in events if event["type"] == "tool")
    assert not tool_event["tool"]["ok"]
    assert events[-1] == {"type": "done", "content": "Acknowledged."}
    assert store.recall(namespace="user_preference") == []
