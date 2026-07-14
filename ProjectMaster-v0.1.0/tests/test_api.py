import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from project_master.agent import ProjectMasterAgent
from project_master.api import create_app
from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime
from project_master.tools.builtin import build_registry


class FakeProvider:
    model = "test-model"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "models": [self.model], "configured_model": self.model}

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        return Message(role="assistant", content="Test response")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        yield Message(role="assistant", content="Test ")
        yield Message(role="assistant", content="response")


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        assert cancellation is not None
        self.started.set()
        cancellation.wait(timeout=5)
        if cancellation.cancelled:
            self.stopped.set()
            return
        yield Message(role="assistant", content="Cancellation failed")


def make_runtime(tmp_path: Path, provider: FakeProvider | None = None) -> MasterRuntime:
    config = MasterConfig(
        model="test-model",
        db_path=tmp_path / "test.db",
        workspace_root=tmp_path / "workspace",
        num_ctx=32768,
    )
    store = SQLiteStore(config.db_path)
    profiler = StyleProfiler(store)
    provider = provider or FakeProvider()
    agent = ProjectMasterAgent(
        provider=provider,
        tools=build_registry(store, config.workspace_root),
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
    )
    return MasterRuntime(config, store, profiler, provider, agent)  # type: ignore[arg-type]


def test_health_and_model_status(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    assert client.get("/api/v1/health").json()["ok"] is True
    status = client.get("/api/v1/models/status").json()
    assert status["configured_model"] == "test-model"
    assert status["num_ctx"] == 32768
    assert status["models"] == ["test-model"]


def test_chat_stream_persists_conversation(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    response = client.post("/api/v1/chat/stream", json={"message": "Hello"})
    events = [line for line in response.iter_lines() if line]
    assert response.status_code == 200
    assert '"type": "start"' in events[0]
    assert any('"type": "token"' in line for line in events)
    assert '"type": "done"' in events[-1]

    start = json.loads(events[0])
    conversation_id = start["conversation_id"]
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert [item["role"] for item in conversation["messages"]] == ["user", "assistant"]


def test_conversation_and_non_streaming_chat_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))
    created = client.post("/api/v1/conversations", json={"title": "API test"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": conversation_id, "message": "Hello"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Test response"
    assert response.json()["conversation_id"] == conversation_id

    listed = client.get("/api/v1/conversations").json()["conversations"]
    assert listed[0]["id"] == conversation_id
    assert listed[0]["message_count"] == 2


def test_chat_stream_can_be_cancelled_and_releases_provider(tmp_path: Path) -> None:
    provider = BlockingProvider()
    client = TestClient(create_app(make_runtime(tmp_path, provider)))
    result: dict[str, Any] = {}

    def run_stream() -> None:
        result["response"] = client.post(
            "/api/v1/chat/stream",
            json={"message": "Keep generating", "request_id": "cancel-test"},
        )

    worker = threading.Thread(target=run_stream, daemon=True)
    worker.start()
    assert provider.started.wait(timeout=2)

    cancelled = client.post("/api/v1/chat/cancel", json={"request_id": "cancel-test"})
    assert cancelled.status_code == 200
    assert cancelled.json() == {"accepted": True, "active": True}

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert provider.stopped.is_set()
    events = [json.loads(line) for line in result["response"].iter_lines() if line]
    assert events[-1]["type"] == "cancelled"


def test_cancel_before_stream_registration_is_not_lost(tmp_path: Path) -> None:
    provider = BlockingProvider()
    client = TestClient(create_app(make_runtime(tmp_path, provider)))

    cancelled = client.post("/api/v1/chat/cancel", json={"request_id": "early-cancel"})
    assert cancelled.json() == {"accepted": True, "active": False}
    response = client.post(
        "/api/v1/chat/stream",
        json={"message": "Do not start", "request_id": "early-cancel"},
    )

    assert provider.stopped.is_set()
    events = [json.loads(line) for line in response.iter_lines() if line]
    assert events[-1]["type"] == "cancelled"
