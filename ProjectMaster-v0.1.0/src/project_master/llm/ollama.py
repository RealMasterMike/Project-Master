from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        num_ctx: int = 32768,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        models = [item.get("name", "") for item in data.get("models", [])]
        return {"ok": True, "models": models, "configured_model": self.model}

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_ollama() for message in messages],
            "stream": False,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if tools:
            payload["tools"] = tools

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code}: {body}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

        raw_message = data.get("message")
        if not isinstance(raw_message, dict):
            raise OllamaError("Ollama response did not contain a valid message")
        tool_calls = raw_message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []
        return Message(
            role="assistant",
            content=str(raw_message.get("content", "")),
            tool_calls=tool_calls,
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        if cancellation is not None and cancellation.cancelled:
            return
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_ollama() for message in messages],
            "stream": True,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if tools:
            payload["tools"] = tools

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_seconds,
            ) as response:
                close_response = response.close
                if cancellation is not None:
                    cancellation.bind_closer(close_response)
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancellation is not None and cancellation.cancelled:
                        return
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if error := data.get("error"):
                        raise OllamaError(f"Ollama streaming error: {error}")
                    raw_message = data.get("message") or {}
                    tool_calls = raw_message.get("tool_calls") or []
                    yield Message(
                        role="assistant",
                        content=str(raw_message.get("content", "")),
                        tool_calls=tool_calls if isinstance(tool_calls, list) else [],
                    )
                    if data.get("done") is True:
                        return
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code}: {body}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            if cancellation is not None and cancellation.cancelled:
                return
            raise OllamaError(f"Ollama streaming request failed: {exc}") from exc
        finally:
            if cancellation is not None and "close_response" in locals():
                cancellation.unbind_closer(close_response)
