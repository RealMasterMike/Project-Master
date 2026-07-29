from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import project_master.config as config_module
from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.llm.ollama import OllamaClient, OllamaError

_UNCENSORED_CHAT_DEFAULT = (
    "hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
)


def test_shipped_uncensored_chat_default_stays_aligned(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    engine_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(config_module, "load_dotenv", lambda: False)
    monkeypatch.delenv("MASTER_MODEL", raising=False)
    monkeypatch.setenv("MASTER_DB_PATH", str(tmp_path / "master.db"))
    monkeypatch.setenv("MASTER_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    shipped = MasterConfig.load(engine_root / "config" / "default.yaml")
    example_value = next(
        line.partition("=")[2]
        for line in (engine_root / ".env.example").read_text(
            encoding="utf-8",
        ).splitlines()
        if line.startswith("MASTER_MODEL=")
    )

    assert MasterConfig().model == _UNCENSORED_CHAT_DEFAULT
    assert shipped.model == _UNCENSORED_CHAT_DEFAULT
    assert example_value == _UNCENSORED_CHAT_DEFAULT


def test_shipped_context_defaults_stay_aligned(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    engine_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(config_module, "load_dotenv", lambda: False)
    monkeypatch.delenv("MASTER_NUM_CTX", raising=False)
    monkeypatch.setenv("MASTER_DB_PATH", str(tmp_path / "master.db"))
    monkeypatch.setenv("MASTER_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    shipped = MasterConfig.load(engine_root / "config" / "default.yaml")
    example_value = next(
        line.partition("=")[2]
        for line in (engine_root / ".env.example").read_text(encoding="utf-8").splitlines()
        if line.startswith("MASTER_NUM_CTX=")
    )

    assert MasterConfig().num_ctx == 65536
    assert shipped.num_ctx == 65536
    assert example_value == "65536"
    assert OllamaClient("http://127.0.0.1:11434", "test").num_ctx == 65536


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
            json={
                "message": {"role": "assistant", "content": "ok"},
                "done_reason": "length",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = OllamaClient("http://127.0.0.1:11434", "test", num_ctx=65536)
    response = client.chat(
        [Message(role="user", content="hello", images=("cHJvamVjdC1pbWFnZQ==",))]
    )
    assert captured["options"]["num_ctx"] == 65536
    assert captured["options"]["num_predict"] == 2048
    assert captured["keep_alive"] == "5m"
    assert captured["messages"] == [
        {
            "role": "user",
            "content": "hello",
            "images": ["cHJvamVjdC1pbWFnZQ=="],
        }
    ]
    assert response.finish_reason == "length"


def test_message_omits_empty_image_payload() -> None:
    assert Message(role="user", content="text only").to_ollama() == {
        "role": "user",
        "content": "text only",
    }


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


def test_streaming_http_error_reports_body_instead_of_response_not_read(
    monkeypatch: Any,
) -> None:
    url = "http://127.0.0.1:11434/api/chat"
    response = httpx.Response(
        500,
        stream=httpx.ByteStream(b'{"error":"model runner failed"}'),
        request=httpx.Request("POST", url),
    )

    class ErrorStream:
        def __enter__(self) -> httpx.Response:
            return response

        def __exit__(self, *_args: Any) -> None:
            response.close()

    monkeypatch.setattr(
        httpx,
        "post",
        lambda show_url, **_kwargs: httpx.Response(
            200,
            json={"capabilities": ["completion"]},
            request=httpx.Request("POST", show_url),
        ),
    )
    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: ErrorStream())

    client = OllamaClient("http://127.0.0.1:11434", "broken-model")
    with pytest.raises(OllamaError, match="HTTP 500.*model runner failed"):
        list(client.chat_stream([Message(role="user", content="hello")]))


def test_streaming_finish_reason_is_preserved_on_terminal_fragment(
    monkeypatch: Any,
) -> None:
    class LengthLimitedResponse:
        def __enter__(self) -> LengthLimitedResponse:
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
                    "message": {"content": "A complete-looking sentence."},
                    "done": True,
                    "done_reason": "length",
                }
            )

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **_kwargs: httpx.Response(
            200,
            json={"capabilities": ["completion"]},
            request=httpx.Request("POST", url),
        ),
    )
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *_args, **_kwargs: LengthLimitedResponse(),
    )

    fragments = list(
        OllamaClient("http://127.0.0.1:11434", "test").chat_stream(
            [Message(role="user", content="hello")]
        )
    )

    assert [fragment.content for fragment in fragments] == [
        "A complete-looking sentence."
    ]
    assert fragments[-1].finish_reason == "length"


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


def test_nonstreaming_thinking_only_response_gets_one_visible_answer_retry(
    monkeypatch: Any,
) -> None:
    chat_payloads: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/api/show"):
            return httpx.Response(
                200,
                json={"capabilities": ["completion", "thinking"]},
                request=httpx.Request("POST", url),
            )
        chat_payloads.append(kwargs["json"])
        if len(chat_payloads) == 1:
            body = {
                "message": {
                    "content": "",
                    "thinking": "private trace must not leak",
                },
                "done_reason": "length",
            }
        else:
            body = {
                "message": {"content": "Visible answer."},
                "done_reason": "stop",
            }
        return httpx.Response(
            200,
            json=body,
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    response = OllamaClient(
        "http://127.0.0.1:11434",
        "thinking-model",
    ).chat([Message(role="user", content="hello")])

    assert response.content == "Visible answer."
    assert response.finish_reason == "stop"
    assert len(chat_payloads) == 2
    assert chat_payloads[0]["think"] is False
    assert chat_payloads[1]["options"]["num_predict"] == 4096
    assert chat_payloads[1]["messages"][-1]["role"] == "user"
    assert "user-visible output" in chat_payloads[1]["messages"][-1]["content"]
    assert all(
        "private trace" not in str(payload["messages"])
        for payload in chat_payloads
    )


def test_streaming_thinking_only_response_gets_one_visible_answer_retry(
    monkeypatch: Any,
) -> None:
    payloads: list[dict[str, Any]] = []

    class AttemptResponse:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt

        def __enter__(self) -> AttemptResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

        def iter_lines(self) -> Any:
            if self.attempt == 1:
                yield json.dumps(
                    {
                        "message": {
                            "content": "",
                            "thinking": "private trace must not leak",
                        },
                        "done": True,
                        "done_reason": "length",
                    }
                )
                return
            yield json.dumps(
                {
                    "message": {"content": "Visible answer."},
                    "done": True,
                    "done_reason": "stop",
                }
            )

    def fake_post(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={"capabilities": ["completion", "thinking"]},
            request=httpx.Request("POST", url),
        )

    def fake_stream(*_args: Any, **kwargs: Any) -> AttemptResponse:
        payloads.append(kwargs["json"])
        return AttemptResponse(len(payloads))

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    fragments = list(
        OllamaClient(
            "http://127.0.0.1:11434",
            "thinking-model",
        ).chat_stream([Message(role="user", content="hello")])
    )

    assert [fragment.content for fragment in fragments] == ["Visible answer."]
    assert fragments[-1].finish_reason == "stop"
    assert len(payloads) == 2
    assert payloads[1]["options"]["num_predict"] == 4096
    assert payloads[1]["messages"][-1]["role"] == "user"
    assert "user-visible output" in payloads[1]["messages"][-1]["content"]
    assert all("private trace" not in str(payload["messages"]) for payload in payloads)


def test_streaming_private_thinking_without_terminal_frame_is_retried(
    monkeypatch: Any,
) -> None:
    attempts = 0

    class MissingTerminalResponse:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt

        def __enter__(self) -> MissingTerminalResponse:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

        def iter_lines(self) -> Any:
            if self.attempt == 1:
                yield json.dumps(
                    {
                        "message": {
                            "content": "",
                            "thinking": "private trace must not leak",
                        },
                        "done": False,
                    }
                )
                return
            yield json.dumps(
                {
                    "message": {"content": "Recovered answer."},
                    "done": True,
                    "done_reason": "stop",
                }
            )

    def fake_post(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={"capabilities": ["completion", "thinking"]},
            request=httpx.Request("POST", url),
        )

    def fake_stream(*_args: Any, **_kwargs: Any) -> MissingTerminalResponse:
        nonlocal attempts
        attempts += 1
        return MissingTerminalResponse(attempts)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)

    fragments = list(
        OllamaClient(
            "http://127.0.0.1:11434",
            "thinking-model",
        ).chat_stream([Message(role="user", content="hello")])
    )

    assert attempts == 2
    assert [fragment.content for fragment in fragments] == ["Recovered answer."]
    assert fragments[-1].finish_reason == "stop"


def test_streaming_visible_output_without_terminal_frame_is_marked_truncated(
    monkeypatch: Any,
) -> None:
    class MissingTerminalResponse:
        def __enter__(self) -> MissingTerminalResponse:
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
                    "message": {"content": "Partial but punctuated output."},
                    "done": False,
                }
            )

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **_kwargs: httpx.Response(
            200,
            json={"capabilities": ["completion"]},
            request=httpx.Request("POST", url),
        ),
    )
    monkeypatch.setattr(
        httpx,
        "stream",
        lambda *_args, **_kwargs: MissingTerminalResponse(),
    )

    fragments = list(
        OllamaClient("http://127.0.0.1:11434", "test").chat_stream(
            [Message(role="user", content="hello")]
        )
    )

    assert "".join(fragment.content for fragment in fragments) == (
        "Partial but punctuated output."
    )
    assert fragments[-1].finish_reason == "length"


def test_streaming_thinking_only_response_reports_plainly_without_leaking_trace(
    monkeypatch: Any,
) -> None:
    payloads: list[dict[str, Any]] = []

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
        payloads.append(kwargs["json"])
        return ThinkingOnlyResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "stream", fake_stream)
    client = OllamaClient("http://127.0.0.1:11434", "thinking-model")

    messages = list(client.chat_stream([Message(role="user", content="hello")]))

    assert len(payloads) == 2
    assert payloads[0]["think"] is False
    assert payloads[0]["keep_alive"] == "5m"
    assert payloads[1]["options"]["num_predict"] == 4096

    # An empty turn is reported as a plain assistant message rather than raising, so the
    # conversation survives a model that cannot answer. The private trace must still never
    # reach the user, and the reply must not imply any tool ran.
    assert len(messages) == 1
    reply = messages[0]
    assert reply.role == "assistant"
    assert "private trace" not in reply.content
    assert "only private reasoning" in reply.content
    assert "No tool ran and nothing was changed" in reply.content
    assert reply.tool_calls == []
