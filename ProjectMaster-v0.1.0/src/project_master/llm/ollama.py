from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message


class OllamaError(RuntimeError):
    pass


class _ModelResidency:
    """Process-local ownership of the last Ollama model used by Project Master."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_model: str | None = None


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        num_ctx: int = 8192,
        max_output_tokens: int = 2048,
        timeout_seconds: float = 180.0,
        *,
        _metadata_cache: dict[str, tuple[float, dict[str, Any]]] | None = None,
        _metadata_lock: threading.Lock | None = None,
        _residency: _ModelResidency | None = None,
        metadata_ttl_seconds: float = 300.0,
        keep_alive: str | int | float = "5m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.metadata_ttl_seconds = metadata_ttl_seconds
        self._metadata_cache = _metadata_cache if _metadata_cache is not None else {}
        self._metadata_lock = _metadata_lock or threading.Lock()
        self._residency = _residency or _ModelResidency()
        self.keep_alive = keep_alive

    def for_model(
        self,
        model: str,
        *,
        max_output_tokens: int | None = None,
    ) -> OllamaClient:
        """Return an equivalently configured client bound to another local model."""
        return OllamaClient(
            base_url=self.base_url,
            model=model,
            temperature=self.temperature,
            num_ctx=self.num_ctx,
            max_output_tokens=max_output_tokens or self.max_output_tokens,
            timeout_seconds=self.timeout_seconds,
            _metadata_cache=self._metadata_cache,
            _metadata_lock=self._metadata_lock,
            _residency=self._residency,
            metadata_ttl_seconds=self.metadata_ttl_seconds,
            keep_alive=self.keep_alive,
        )

    def _unload_model(self, model: str) -> None:
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "stream": False,
                    "keep_alive": 0,
                },
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code} while unloading "
                f"{model}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Ollama could not unload {model} before switching models: {exc}"
            ) from exc

    def unload_active_model(self) -> str | None:
        """Unload the Project Master model tracked by this shared client family."""
        with self._residency.lock:
            active = self._residency.active_model
            if active is None:
                return None
            self._unload_model(active)
            self._residency.active_model = None
            return active

    @contextmanager
    def _model_residency_scope(self) -> Iterator[None]:
        """Unload Project Master's prior model before allowing a different one to load."""
        with self._residency.lock:
            previous = self._residency.active_model
            if previous is not None and previous.casefold() != self.model.casefold():
                self._unload_model(previous)
                self._residency.active_model = None
            # Track a failed or partial load so the next model switch still cleans it up.
            self._residency.active_model = self.model
            yield

    def list_models(self) -> list[dict[str, Any]]:
        """Return Ollama's installed-model metadata without discarding alias information."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code} while listing models: {body}"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

        if not isinstance(data, dict):
            raise OllamaError("Ollama model list did not contain a valid object")
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise OllamaError("Ollama model list did not contain a valid models array")
        return [dict(item) for item in raw_models if isinstance(item, dict)]

    def show_model(self, model: str, *, refresh: bool = False) -> dict[str, Any]:
        """Return capability and template metadata for one installed model tag."""
        requested_model = model.strip()
        if not requested_model:
            raise ValueError("model must not be empty")
        cache_key = requested_model.casefold()
        now = time.monotonic()
        if not refresh:
            with self._metadata_lock:
                cached = self._metadata_cache.get(cache_key)
                if cached is not None and now - cached[0] < self.metadata_ttl_seconds:
                    return dict(cached[1])
        try:
            response = httpx.post(
                f"{self.base_url}/api/show",
                json={"model": requested_model},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code} while inspecting "
                f"{requested_model}: {body}"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaError(
                f"Ollama model inspection failed for {requested_model}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise OllamaError(f"Ollama returned invalid model metadata for {requested_model}")
        with self._metadata_lock:
            self._metadata_cache[cache_key] = (now, dict(data))
        return dict(data)

    def supports_tools(self, model: str | None = None) -> bool:
        """Return whether Ollama explicitly reports native tool-call support.

        Models without the capability still work as ordinary chat models; Project Master simply
        omits schemas instead of sending a request Ollama will reject.
        """
        try:
            metadata = self.show_model(model or self.model)
        except OllamaError:
            return False
        capabilities = metadata.get("capabilities")
        if not isinstance(capabilities, list):
            return False
        return any(
            isinstance(item, str) and item.strip().casefold() == "tools"
            for item in capabilities
        )

    def _thinking_request_value(self) -> bool | str | None:
        """Choose the least-expensive supported thinking policy for this model.

        Ollama enables thinking by default for capable models, but Project Master does not expose
        or persist private reasoning traces. Disable boolean thinking explicitly. GPT-OSS cannot
        disable thinking, so request its lowest documented level instead.
        """
        try:
            metadata = self.show_model(self.model)
        except OllamaError:
            return "low" if _looks_like_gpt_oss(self.model) else False
        capabilities = metadata.get("capabilities")
        supports_thinking = isinstance(capabilities, list) and any(
            isinstance(item, str) and item.strip().casefold() == "thinking"
            for item in capabilities
        )
        # Some converted models emit a separate thinking field even though /api/show omits the
        # capability. Sending false is harmless for ordinary chat models and prevents an
        # unadvertised private trace from consuming the bounded visible-response budget.
        if not supports_thinking:
            return False
        return "low" if _is_gpt_oss(self.model, metadata) else False

    def health(self) -> dict[str, Any]:
        models = [item.get("name", item.get("model", "")) for item in self.list_models()]
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
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_output_tokens,
            },
        }
        thinking = self._thinking_request_value()
        if thinking is not None:
            payload["think"] = thinking
        if tools and self.supports_tools():
            payload["tools"] = tools

        try:
            with self._model_residency_scope():
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
        content = str(raw_message.get("content", ""))
        if (
            str(raw_message.get("thinking", "")).strip()
            and not content.strip()
            and not tool_calls
        ):
            raise OllamaError(
                "Ollama returned private thinking but no visible response or tool call; "
                "the bounded output budget may have been exhausted."
            )
        return Message(
            role="assistant",
            content=content,
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
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.max_output_tokens,
            },
        }
        thinking = self._thinking_request_value()
        if thinking is not None:
            payload["think"] = thinking
        if tools and self.supports_tools():
            payload["tools"] = tools

        saw_private_thinking = False
        saw_visible_output = False
        try:
            with self._model_residency_scope():
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
                        if not isinstance(tool_calls, list):
                            tool_calls = []
                        content = str(raw_message.get("content", ""))
                        saw_private_thinking = (
                            saw_private_thinking
                            or bool(str(raw_message.get("thinking", "")).strip())
                        )
                        saw_visible_output = (
                            saw_visible_output
                            or bool(content.strip())
                            or bool(tool_calls)
                        )
                        yield Message(
                            role="assistant",
                            content=content,
                            tool_calls=tool_calls,
                        )
                        if data.get("done") is True:
                            if saw_private_thinking and not saw_visible_output:
                                raise OllamaError(
                                    "Ollama returned private thinking but no visible response or "
                                    "tool call; the bounded output budget may have been exhausted."
                                )
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


def _looks_like_gpt_oss(value: str) -> bool:
    return bool(re.search(r"(?:^|[/_.:-])gpt-?oss(?:$|[/_.:-])", value.casefold()))


def _is_gpt_oss(model: str, metadata: dict[str, Any]) -> bool:
    if _looks_like_gpt_oss(model):
        return True
    details = metadata.get("details")
    identifiers: list[str] = []
    if isinstance(details, dict):
        family = details.get("family")
        if isinstance(family, str):
            identifiers.append(family)
        families = details.get("families")
        if isinstance(families, list):
            identifiers.extend(item for item in families if isinstance(item, str))
    model_info = metadata.get("model_info")
    if isinstance(model_info, dict):
        architecture = model_info.get("general.architecture")
        if isinstance(architecture, str):
            identifiers.append(architecture)
    return any(
        re.sub(r"[^a-z0-9]", "", item.casefold()) == "gptoss"
        for item in identifiers
    )
