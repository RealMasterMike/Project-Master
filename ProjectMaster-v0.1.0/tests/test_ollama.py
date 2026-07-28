from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.llm.ollama import OllamaClient, OllamaError


def test_context_length_loads_from_environment(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTER_NUM_CTX", "65536")
    monkeypatch.setenv("MASTER_DB_PATH", str(tmp_path / "master.db"))
    monkeypatch.setenv("MASTER_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    config = MasterConfig.load(tmp_path / "missing.yaml")
    assert config.num_ctx == 65536


def test_ollama_chat_sends_num_ctx(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OllamaClient("http://127.0.0.1:11434", "test", num_ctx=65536)
    client.chat([Message(role="user", content="hello")])
    assert captured["options"]["num_ctx"] == 65536
    assert captured["options"]["num_predict"] == 2048
    assert captured["keep_alive"] == "5m"


def test_toolless_model_gracefully_receives_plain_chat_request(monkeypatch: Any) -> None:
    chat_payload: dict[str, Any] = {}
    calls = {"show": 0}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            calls["show"] += 1
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        chat_payload.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "plain response"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OllamaClient("http://127.0.0.1:11434", "plain")
    response = client.chat(
        [Message(role="user", content="hello")],
        tools=[{"type": "function", "function": {"name": "calculator"}}],
    )

    assert response.content == "plain response"
    assert "tools" not in chat_payload
    assert calls["show"] == 1


def test_tool_capability_metadata_is_cached_across_model_clients(
    monkeypatch: Any,
) -> None:
    calls = {"show": 0, "chat": 0}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            calls["show"] += 1
            return httpx.Response(
                200,
                json={"capabilities": ["completion", "tools"]},
                request=httpx.Request("POST", url),
            )
        calls["chat"] += 1
        assert "tools" in kwargs["json"]
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    first = OllamaClient("http://127.0.0.1:11434", "same")
    second = first.for_model("same", max_output_tokens=512)

    first.chat([Message(role="user", content="one")], tools=[{"type": "function"}])
    second.chat([Message(role="user", content="two")], tools=[{"type": "function"}])

    assert calls == {"show": 1, "chat": 2}
    assert second.max_output_tokens == 512


def test_ollama_model_catalog_endpoints_preserve_raw_metadata(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured["get_url"] = url
        captured["get_timeout"] = kwargs["timeout"]
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "test:latest",
                        "digest": "abc",
                        "size": 42,
                    }
                ]
            },
            request=httpx.Request("GET", url),
        )

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["post_url"] = url
        captured["post_json"] = kwargs["json"]
        return httpx.Response(
            200,
            json={"capabilities": ["completion", "tools"]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    client = OllamaClient("http://127.0.0.1:11434/", "test:latest")

    models = client.list_models()
    shown = client.show_model("test:latest")

    assert models == [{"name": "test:latest", "digest": "abc", "size": 42}]
    assert shown == {"capabilities": ["completion", "tools"]}
    assert captured["get_url"] == "http://127.0.0.1:11434/api/tags"
    assert captured["post_url"] == "http://127.0.0.1:11434/api/show"
    assert captured["post_json"] == {"model": "test:latest"}


def test_ollama_for_model_preserves_runtime_options() -> None:
    client = OllamaClient(
        "http://127.0.0.1:11434",
        "first",
        temperature=0.7,
        num_ctx=65_536,
        timeout_seconds=45.0,
        keep_alive="90s",
    )

    second = client.for_model("second")

    assert second is not client
    assert second.base_url == client.base_url
    assert second.model == "second"
    assert second.temperature == 0.7
    assert second.num_ctx == 65_536
    assert second.max_output_tokens == client.max_output_tokens
    assert second.timeout_seconds == 45.0
    assert second.keep_alive == "90s"
    assert second._residency is client._residency


def test_model_switch_unloads_prior_shared_client_before_loading_next(
    monkeypatch: Any,
) -> None:
    events: list[tuple[str, str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        payload = kwargs["json"]
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        if url.endswith("/api/generate"):
            events.append(("unload", payload["model"], payload["keep_alive"]))
            return httpx.Response(
                200,
                json={"done": True},
                request=httpx.Request("POST", url),
            )
        events.append(("chat", payload["model"], payload["keep_alive"]))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    first = OllamaClient(
        "http://127.0.0.1:11434",
        "first",
        keep_alive="45s",
    )
    second = first.for_model("second")

    first.chat([Message(role="user", content="one")])
    second.chat([Message(role="user", content="two")])
    second.chat([Message(role="user", content="three")])

    assert events == [
        ("chat", "first", "45s"),
        ("unload", "first", 0),
        ("chat", "second", "45s"),
        ("chat", "second", "45s"),
    ]


def test_model_switch_fails_closed_when_prior_model_cannot_unload(
    monkeypatch: Any,
) -> None:
    chatted_models: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        payload = kwargs["json"]
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        if url.endswith("/api/generate"):
            return httpx.Response(
                503,
                text="runner remains resident",
                request=httpx.Request("POST", url),
            )
        chatted_models.append(payload["model"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    first = OllamaClient("http://127.0.0.1:11434", "first")
    second = first.for_model("second")
    first.chat([Message(role="user", content="one")])

    with pytest.raises(OllamaError, match="while unloading first"):
        second.chat([Message(role="user", content="two")])

    assert chatted_models == ["first"]


def test_explicit_active_model_unload_is_shared_and_idempotent(
    monkeypatch: Any,
) -> None:
    events: list[tuple[str, str]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        payload = kwargs["json"]
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        if url.endswith("/api/generate"):
            events.append(("unload", payload["model"]))
            return httpx.Response(
                200,
                json={"done": True},
                request=httpx.Request("POST", url),
            )
        events.append(("chat", payload["model"]))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    first = OllamaClient("http://127.0.0.1:11434", "first")
    second = first.for_model("second")

    first.chat([Message(role="user", content="one")])
    assert second.unload_active_model() == "first"
    assert first.unload_active_model() is None
    second.chat([Message(role="user", content="two")])

    assert events == [
        ("chat", "first"),
        ("unload", "first"),
        ("chat", "second"),
    ]


def test_streaming_request_releases_switch_lock_and_preserves_keep_alive(
    monkeypatch: Any,
) -> None:
    events: list[tuple[str, str, Any]] = []

    class DoneStreamResponse:
        def __enter__(self) -> DoneStreamResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

        def iter_lines(self) -> Any:
            yield json.dumps(
                {
                    "message": {"content": "streamed"},
                    "done": True,
                }
            )

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        payload = kwargs["json"]
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        if url.endswith("/api/generate"):
            events.append(("unload", payload["model"], payload["keep_alive"]))
            return httpx.Response(
                200,
                json={"done": True},
                request=httpx.Request("POST", url),
            )
        events.append(("chat", payload["model"], payload["keep_alive"]))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
            request=httpx.Request("POST", url),
        )

    def fake_stream(*_args: Any, **kwargs: Any) -> DoneStreamResponse:
        payload = kwargs["json"]
        events.append(("stream", payload["model"], payload["keep_alive"]))
        return DoneStreamResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    first = OllamaClient(
        "http://127.0.0.1:11434",
        "first",
        keep_alive="30s",
    )
    second = first.for_model("second")

    assert "".join(
        item.content
        for item in first.chat_stream([Message(role="user", content="one")])
    ) == "streamed"
    second.chat([Message(role="user", content="two")])

    assert events == [
        ("stream", "first", "30s"),
        ("unload", "first", 0),
        ("chat", "second", "30s"),
    ]


def test_ollama_rejects_malformed_model_catalog_response(monkeypatch: Any) -> None:
    def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json=["not", "an", "object"],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    client = OllamaClient("http://127.0.0.1:11434", "test")

    with pytest.raises(OllamaError, match="valid object"):
        client.list_models()


def test_ollama_stream_closes_active_response_when_cancelled(monkeypatch: Any) -> None:
    class FakeStreamResponse:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self) -> FakeStreamResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            self.close()

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def iter_lines(self) -> Any:
            yield json.dumps({"message": {"content": "first"}, "done": False})
            if self.closed:
                raise httpx.ReadError("stream closed for cancellation")
            yield json.dumps({"message": {"content": "second"}, "done": True})

    response = FakeStreamResponse()
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **_kwargs: httpx.Response(
            200,
            json={"capabilities": ["completion"]},
            request=httpx.Request("POST", url),
        ),
    )
    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: response)
    token = CancellationToken()
    client = OllamaClient("http://127.0.0.1:11434", "test")
    stream = client.chat_stream(
        [Message(role="user", content="hello")],
        cancellation=token,
    )

    assert next(stream).content == "first"
    token.cancel()
    assert response.closed is True
    assert list(stream) == []


def test_thinking_capability_is_explicitly_disabled(monkeypatch: Any) -> None:
    chat_payload: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "thinking"],
                    "details": {"family": "gemma4"},
                },
                request=httpx.Request("POST", url),
            )
        chat_payload.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "visible"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    response = OllamaClient("http://127.0.0.1:11434", "gemma4").chat(
        [Message(role="user", content="hello")]
    )

    assert response.content == "visible"
    assert chat_payload["think"] is False


def test_gpt_oss_uses_low_thinking_level_from_metadata(monkeypatch: Any) -> None:
    chat_payload: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "thinking"],
                    "details": {"family": "gptoss"},
                },
                request=httpx.Request("POST", url),
            )
        chat_payload.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "visible"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    OllamaClient("http://127.0.0.1:11434", "custom-alias").chat(
        [Message(role="user", content="hello")]
    )

    assert chat_payload["think"] == "low"


def test_metadata_without_thinking_still_disables_unadvertised_trace(
    monkeypatch: Any,
) -> None:
    chat_payload: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
                request=httpx.Request("POST", url),
            )
        chat_payload.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "visible"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    OllamaClient("http://127.0.0.1:11434", "plain").chat(
        [Message(role="user", content="hello")]
    )

    assert chat_payload["think"] is False


def test_metadata_failure_defaults_to_disabling_thinking(monkeypatch: Any) -> None:
    chat_payload: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            raise httpx.ConnectError(
                "metadata offline",
                request=httpx.Request("POST", url),
            )
        chat_payload.update(kwargs["json"])
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "visible"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    OllamaClient("http://127.0.0.1:11434", "unknown").chat(
        [Message(role="user", content="hello")]
    )

    assert chat_payload["think"] is False


def test_streaming_thinking_only_response_fails_without_leaking_trace(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class ThinkingOnlyResponse:
        def __enter__(self) -> ThinkingOnlyResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

        def iter_lines(self) -> Any:
            yield json.dumps(
                {
                    "message": {
                        "content": "",
                        "thinking": "private trace must not leak",
                    },
                    "done": True,
                }
            )

    def fake_post(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={"capabilities": ["completion", "thinking"]},
            request=httpx.Request("POST", url),
        )

    def fake_stream(*_args: Any, **kwargs: Any) -> ThinkingOnlyResponse:
        captured.update(kwargs["json"])
        return ThinkingOnlyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    client = OllamaClient("http://127.0.0.1:11434", "thinking-model")

    with pytest.raises(OllamaError, match="bounded output budget") as exc_info:
        list(client.chat_stream([Message(role="user", content="hello")]))

    assert captured["think"] is False
    assert captured["keep_alive"] == "5m"
    assert "private trace" not in str(exc_info.value)
