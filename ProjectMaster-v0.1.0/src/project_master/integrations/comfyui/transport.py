from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.comfyui.profiles import (
    ComfyUIProfile,
    EnvironmentSecretResolver,
    SecretResolver,
)
from project_master.integrations.comfyui.security import (
    ComfySecurityError,
    join_api_url,
    validate_identifier,
    validate_output_locator,
)

_MAX_JSON_BYTES = 16 * 1024 * 1024
_DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_OUTPUT_COUNT = 4_096
_MAX_EVENT_BYTES = 256 * 1024
_DOWNLOADABLE_CATEGORIES = frozenset(
    {
        "audio",
        "audios",
        "file",
        "files",
        "gif",
        "gifs",
        "image",
        "images",
        "video",
        "videos",
    }
)
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
_SAFE_APPLICATION_MEDIA_TYPES = frozenset(
    {
        "application/gzip",
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/vnd.rar",
        "application/x-7z-compressed",
        "application/x-tar",
        "application/zip",
        "model/gltf-binary",
        "model/gltf+json",
        "text/csv",
        "text/plain",
    }
)
_FORBIDDEN_MEDIA_TYPES = frozenset(
    {
        "application/javascript",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/html",
        "text/javascript",
    }
)


class ComfyTransportError(RuntimeError):
    """A network, HTTP, or remote protocol failure without response-body leakage."""


class ComfyProtocolError(ComfyTransportError):
    """ComfyUI returned a response that does not match its public API contract."""


@dataclass(frozen=True, slots=True)
class DownloadedOutput:
    """A bounded backend-only `/view` response ready for app-owned persistence."""

    content: bytes
    media_type: str
    source_url: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.content, (bytes, bytearray, memoryview)):
            raise TypeError("ComfyUI downloaded output content must be bytes.")
        content = bytes(self.content)
        if not content:
            raise ValueError("ComfyUI downloaded output cannot be empty.")
        try:
            media_type = _normalize_media_type(self.media_type)
        except ValueError as exc:
            raise ValueError("ComfyUI downloaded output media type is invalid.") from exc
        if media_type in _FORBIDDEN_MEDIA_TYPES or not _media_type_allowed(media_type):
            raise ValueError("ComfyUI downloaded output media type is not permitted.")
        if self.fetched_at.tzinfo is None:
            raise ValueError("ComfyUI downloaded output timestamp must include a timezone.")
        _url_origin(self.source_url)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "media_type", media_type)


class OutputRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str = Field(min_length=1, max_length=240)
    subfolder: str = Field(default="", max_length=1_024)
    type: Literal["output", "temp"] = "output"

    @model_validator(mode="after")
    def validate_locator(self) -> OutputRef:
        validate_output_locator(self.filename, self.subfolder, self.type)
        return self


class OutputMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=80)
    output_index: int = Field(default=0, ge=0, le=_MAX_OUTPUT_COUNT)
    ref: OutputRef
    media_type: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, ge=0)
    reported_size_bytes: int | None = Field(default=None, ge=0)


class QueueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    number: int | None = None
    state: Literal["running", "queued"]
    client_id: str | None = None

    @field_validator("prompt_id")
    @classmethod
    def validate_prompt_id(cls, value: str) -> str:
        return validate_identifier(value, "prompt ID")


class QueueSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    running: tuple[QueueEntry, ...] = ()
    queued: tuple[QueueEntry, ...] = ()

    def find(self, prompt_id: str) -> QueueEntry | None:
        return next(
            (entry for entry in (*self.running, *self.queued) if entry.prompt_id == prompt_id),
            None,
        )

    def for_client(self, client_id: str) -> tuple[QueueEntry, ...]:
        validate_identifier(client_id, "client ID")
        return tuple(
            entry for entry in (*self.running, *self.queued) if entry.client_id == client_id
        )


class PromptSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    number: int | None = None
    node_errors: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt_id")
    @classmethod
    def validate_prompt_id(cls, value: str) -> str:
        return validate_identifier(value, "prompt ID")


class HistoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    found: bool
    completed: bool = False
    failed: bool = False
    status_text: str | None = None
    outputs: tuple[OutputMetadata, ...] = ()
    history_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ComfyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(min_length=1, max_length=100)
    data: dict[str, Any] = Field(default_factory=dict)
    kind: Literal[
        "queue",
        "started",
        "cached",
        "executing",
        "progress",
        "output",
        "completed",
        "failed",
        "cancelled",
        "unknown",
    ] = "unknown"
    prompt_id: str | None = None
    node_id: str | None = None
    current: float | None = Field(default=None, ge=0)
    total: float | None = Field(default=None, gt=0)
    fraction: float | None = Field(default=None, ge=0, le=1)


class WebSocketEventSource(Protocol):
    def events(self, url: str, headers: Mapping[str, str]) -> AsyncIterator[Mapping[str, Any]]: ...


class ComfyTransport(Protocol):
    """Official ComfyUI REST and WebSocket operations used by Project Master."""

    async def system_stats(self) -> Mapping[str, Any]: ...

    async def object_info(self) -> Mapping[str, Any]: ...

    async def queue(self) -> QueueSnapshot: ...

    async def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        extra_data: Mapping[str, Any] | None = None,
    ) -> PromptSubmission: ...

    async def history(self, prompt_id: str) -> HistoryResult: ...

    async def download_output(self, output: OutputMetadata) -> DownloadedOutput: ...

    async def delete_queue_items(self, prompt_ids: Sequence[str]) -> None: ...

    async def interrupt(self) -> None: ...

    def events(self, client_id: str) -> AsyncIterator[ComfyEvent]: ...


class HttpxComfyTransport:
    """Strict REST transport with an injectable WebSocket event source.

    `httpx` is configured never to follow redirects. WebSocket I/O is injected so Project
    Master does not require a ComfyUI or WebSocket package merely to import this integration.
    """

    def __init__(
        self,
        profile: ComfyUIProfile,
        *,
        secret_resolver: SecretResolver | None = None,
        event_source: WebSocketEventSource | None = None,
        client: httpx.AsyncClient | None = None,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        if (
            not isinstance(max_output_bytes, int)
            or isinstance(max_output_bytes, bool)
            or max_output_bytes < 1
        ):
            raise ValueError("ComfyUI output size limit must be a positive integer.")
        self.profile = profile
        self._resolver = secret_resolver or EnvironmentSecretResolver()
        self._event_source = event_source
        self._max_output_bytes = max_output_bytes
        self._owns_client = client is None
        self._headers = profile.authentication_headers(self._resolver)
        if client is not None and client.follow_redirects:
            raise ComfySecurityError("Injected ComfyUI HTTP clients cannot follow redirects.")
        self._client = client or httpx.AsyncClient(
            timeout=profile.timeout_seconds,
            verify=profile.verify_tls,
            follow_redirects=False,
            headers=self._headers,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def system_stats(self) -> Mapping[str, Any]:
        return await self._request_json("GET", "system_stats")

    async def object_info(self) -> Mapping[str, Any]:
        return await self._request_json("GET", "object_info")

    async def queue(self) -> QueueSnapshot:
        payload = await self._request_json("GET", "queue")
        return parse_queue_snapshot(payload)

    async def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        extra_data: Mapping[str, Any] | None = None,
    ) -> PromptSubmission:
        validate_identifier(client_id, "client ID")
        body: dict[str, Any] = {"prompt": workflow, "client_id": client_id}
        if extra_data:
            body["extra_data"] = dict(extra_data)
        payload = await self._request_json("POST", "prompt", json=body)
        try:
            return PromptSubmission.model_validate(payload)
        except ValueError as exc:
            raise ComfyProtocolError("ComfyUI returned an invalid prompt submission.") from exc

    async def history(self, prompt_id: str) -> HistoryResult:
        validate_identifier(prompt_id, "prompt ID")
        payload = await self._request_json("GET", f"history/{prompt_id}")
        return parse_history_result(prompt_id, payload)

    async def download_output(self, output: OutputMetadata) -> DownloadedOutput:
        validated = OutputMetadata.model_validate(output.model_dump())
        if (
            validated.reported_size_bytes is not None
            and validated.reported_size_bytes > self._max_output_bytes
        ):
            raise ComfyProtocolError("ComfyUI history output exceeds the configured size limit.")
        url = self.output_url(validated.ref)
        if _url_origin(url) != _url_origin(self.profile.base_url):
            raise ComfySecurityError("ComfyUI output URL left the configured profile origin.")
        request_options: dict[str, Any] = {}
        if self._headers:
            request_options["headers"] = self._headers
        try:
            async with self._client.stream("GET", url, **request_options) as response:
                if _url_origin(str(response.request.url)) != _url_origin(self.profile.base_url):
                    raise ComfySecurityError(
                        "ComfyUI output response left the configured profile origin."
                    )
                if 300 <= response.status_code < 400:
                    raise ComfyTransportError("ComfyUI redirects are not permitted.")
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ComfyTransportError(
                        f"ComfyUI returned HTTP {response.status_code}."
                    ) from exc
                declared_length = _content_length(response.headers.get("content-length"))
                if declared_length is not None and declared_length > self._max_output_bytes:
                    raise ComfyProtocolError("ComfyUI output exceeds the configured size limit.")
                media_type = _download_media_type(
                    response.headers.get("content-type"),
                    validated.media_type,
                )
                content_encoding = response.headers.get("content-encoding")
                if content_encoding is not None and content_encoding.lower() != "identity":
                    raise ComfyProtocolError(
                        "Compressed ComfyUI output responses are not permitted."
                    )
                content = bytearray()
                if response.is_stream_consumed:
                    chunks = (response.content,)
                    for chunk in chunks:
                        if len(content) + len(chunk) > self._max_output_bytes:
                            raise ComfyProtocolError(
                                "ComfyUI output exceeds the configured size limit."
                            )
                        content.extend(chunk)
                else:
                    async for chunk in response.aiter_raw():
                        if len(content) + len(chunk) > self._max_output_bytes:
                            raise ComfyProtocolError(
                                "ComfyUI output exceeds the configured size limit."
                            )
                        content.extend(chunk)
        except ComfyTransportError:
            raise
        except httpx.HTTPError as exc:
            raise ComfyTransportError(
                f"ComfyUI output download failed: {type(exc).__name__}."
            ) from exc
        if not content:
            raise ComfyProtocolError("ComfyUI returned an empty output.")
        if declared_length is not None and len(content) != declared_length:
            raise ComfyProtocolError("ComfyUI output length does not match its response headers.")
        if (
            validated.reported_size_bytes is not None
            and len(content) != validated.reported_size_bytes
        ):
            raise ComfyProtocolError("ComfyUI output size does not match its history metadata.")
        return DownloadedOutput(
            content=bytes(content),
            media_type=media_type,
            source_url=url,
            fetched_at=datetime.now(UTC),
        )

    async def delete_queue_items(self, prompt_ids: Sequence[str]) -> None:
        validated = [validate_identifier(item, "prompt ID") for item in prompt_ids]
        if not validated:
            return
        await self._request_json("POST", "queue", json={"delete": validated}, allow_empty=True)

    async def interrupt(self) -> None:
        await self._request_json("POST", "interrupt", json={}, allow_empty=True)

    async def events(self, client_id: str) -> AsyncIterator[ComfyEvent]:
        validate_identifier(client_id, "client ID")
        if self._event_source is None:
            raise ComfyTransportError("No ComfyUI WebSocket event source is configured.")
        url = _websocket_url(self.profile.base_url, client_id)
        async for payload in self._event_source.events(url, self._headers):
            yield normalize_comfy_event(payload)

    def output_url(self, output: OutputRef) -> str:
        query = urlencode(
            {
                "filename": output.filename,
                "subfolder": output.subfolder,
                "type": output.type,
            }
        )
        return f"{join_api_url(self.profile.base_url, 'view')}?{query}"

    async def _request_json(
        self,
        method: str,
        route: str,
        *,
        json: Mapping[str, Any] | None = None,
        allow_empty: bool = False,
    ) -> Mapping[str, Any]:
        url = join_api_url(self.profile.base_url, route)
        request_options: dict[str, Any] = {}
        if json is not None:
            request_options["json"] = json
        if self._headers:
            request_options["headers"] = self._headers
        try:
            response = await self._client.request(method, url, **request_options)
        except httpx.HTTPError as exc:
            raise ComfyTransportError(f"ComfyUI request failed: {type(exc).__name__}.") from exc
        if 300 <= response.status_code < 400:
            raise ComfyTransportError("ComfyUI redirects are not permitted.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ComfyTransportError(f"ComfyUI returned HTTP {response.status_code}.") from exc
        if not response.content and allow_empty:
            return {}
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_JSON_BYTES:
            raise ComfyProtocolError("ComfyUI JSON response exceeds the size limit.")
        if len(response.content) > _MAX_JSON_BYTES:
            raise ComfyProtocolError("ComfyUI JSON response exceeds the size limit.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ComfyProtocolError("ComfyUI returned invalid JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ComfyProtocolError("ComfyUI JSON response must be an object.")
        return payload


def parse_queue_snapshot(payload: Mapping[str, Any]) -> QueueSnapshot:
    try:
        running = tuple(_parse_queue_entry(item, "running") for item in payload["queue_running"])
        queued = tuple(_parse_queue_entry(item, "queued") for item in payload["queue_pending"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ComfyProtocolError("ComfyUI returned an invalid queue response.") from exc
    return QueueSnapshot(running=running, queued=queued)


def parse_history_result(prompt_id: str, payload: Mapping[str, Any]) -> HistoryResult:
    raw = payload.get(prompt_id)
    if raw is None:
        return HistoryResult(found=False)
    if not isinstance(raw, Mapping):
        raise ComfyProtocolError("ComfyUI history entry must be an object.")

    status = raw.get("status", {})
    if not isinstance(status, Mapping):
        raise ComfyProtocolError("ComfyUI history status must be an object.")
    status_text_raw = status.get("status_str")
    status_text = status_text_raw if isinstance(status_text_raw, str) else None
    completed_raw = status.get("completed", False)
    if not isinstance(completed_raw, bool):
        raise ComfyProtocolError("ComfyUI history completion state must be boolean.")
    normalized_status = status_text.strip().lower() if status_text is not None else None
    failed = normalized_status in {"error", "failed"} or (
        completed_raw and normalized_status not in {None, "success"}
    )
    outputs = _parse_outputs(raw.get("outputs", {}))
    try:
        history_document = json.dumps(
            raw,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComfyProtocolError("ComfyUI history entry is not finite JSON.") from exc
    return HistoryResult(
        found=True,
        completed=completed_raw and not failed,
        failed=failed,
        status_text=status_text,
        outputs=outputs,
        history_sha256=hashlib.sha256(history_document).hexdigest(),
    )


def _parse_queue_entry(raw: Any, state: Literal["running", "queued"]) -> QueueEntry:
    if isinstance(raw, Mapping):
        prompt_id = raw.get("prompt_id")
        number = raw.get("number")
        client_id = raw.get("client_id")
    elif isinstance(raw, list) and len(raw) >= 2:
        number, prompt_id = raw[0], raw[1]
        extra = raw[3] if len(raw) > 3 and isinstance(raw[3], Mapping) else {}
        client_id = extra.get("client_id")
    else:
        raise ValueError("Unsupported queue entry.")
    validate_identifier(prompt_id, "prompt ID")
    if number is not None and (not isinstance(number, int) or isinstance(number, bool)):
        raise ValueError("Queue number is invalid.")
    if client_id is not None and not isinstance(client_id, str):
        raise ValueError("Queue client ID is invalid.")
    return QueueEntry(
        prompt_id=prompt_id,
        number=number,
        state=state,
        client_id=client_id,
    )


def _parse_outputs(raw_outputs: Any) -> tuple[OutputMetadata, ...]:
    if not isinstance(raw_outputs, Mapping):
        raise ComfyProtocolError("ComfyUI history outputs must be an object.")
    outputs: list[OutputMetadata] = []
    for node_id, raw_node_outputs in raw_outputs.items():
        if (
            not isinstance(node_id, str)
            or not node_id
            or len(node_id) > 128
            or any(ord(character) < 32 for character in node_id)
            or not isinstance(raw_node_outputs, Mapping)
        ):
            raise ComfyProtocolError("ComfyUI output node metadata is invalid.")
        for category, raw_items in raw_node_outputs.items():
            if (
                not isinstance(category, str)
                or not category
                or len(category) > 80
                or any(ord(character) < 32 for character in category)
            ):
                raise ComfyProtocolError("ComfyUI output category is invalid.")
            if isinstance(raw_items, Mapping):
                items: list[Any] = [raw_items]
            elif isinstance(raw_items, list):
                items = raw_items
            else:
                continue
            for output_index, raw_item in enumerate(items):
                if not isinstance(raw_item, Mapping) or "filename" not in raw_item:
                    if category.lower() in _DOWNLOADABLE_CATEGORIES:
                        raise ComfyProtocolError(
                            "ComfyUI downloadable output is missing file metadata."
                        )
                    continue
                if len(outputs) >= _MAX_OUTPUT_COUNT:
                    raise ComfyProtocolError("ComfyUI history contains too many outputs.")
                try:
                    ref = OutputRef(
                        filename=raw_item["filename"],
                        subfolder=raw_item.get("subfolder", ""),
                        type=raw_item.get("type", "output"),
                    )
                    outputs.append(
                        OutputMetadata(
                            node_id=node_id,
                            category=category,
                            output_index=output_index,
                            ref=ref,
                            media_type=_history_media_type(
                                raw_item,
                                ref.filename,
                                category,
                            ),
                            width=_optional_positive_int(raw_item.get("width")),
                            height=_optional_positive_int(raw_item.get("height")),
                            duration_seconds=_optional_nonnegative_float(raw_item.get("duration")),
                            reported_size_bytes=_optional_nonnegative_int(
                                raw_item.get("size_bytes", raw_item.get("size"))
                            ),
                        )
                    )
                except (TypeError, ValueError, ComfySecurityError) as exc:
                    raise ComfyProtocolError(
                        "ComfyUI returned unsafe or invalid output metadata."
                    ) from exc
    return tuple(outputs)


def normalize_comfy_event(payload: Mapping[str, Any]) -> ComfyEvent:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("type"), str):
        raise ComfyProtocolError("ComfyUI WebSocket returned an invalid event.")
    event_type = payload["type"]
    if (
        not event_type
        or len(event_type) > 100
        or any(ord(character) < 32 for character in event_type)
    ):
        raise ComfyProtocolError("ComfyUI WebSocket event type is invalid.")
    data = payload.get("data", {})
    if not isinstance(data, Mapping):
        raise ComfyProtocolError("ComfyUI WebSocket event data must be an object.")
    try:
        encoded = json.dumps(
            data,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComfyProtocolError("ComfyUI WebSocket event must contain finite JSON.") from exc
    if len(encoded) > _MAX_EVENT_BYTES:
        raise ComfyProtocolError("ComfyUI WebSocket event exceeds the size limit.")

    prompt_id = data.get("prompt_id")
    if prompt_id is not None:
        if not isinstance(prompt_id, str):
            raise ComfyProtocolError("ComfyUI WebSocket prompt ID is invalid.")
        try:
            validate_identifier(prompt_id, "prompt ID")
        except ComfySecurityError as exc:
            raise ComfyProtocolError("ComfyUI WebSocket prompt ID is invalid.") from exc
    node_id = data.get("node")
    if node_id is not None and (
        not isinstance(node_id, str)
        or not node_id
        or len(node_id) > 128
        or any(ord(character) < 32 for character in node_id)
    ):
        raise ComfyProtocolError("ComfyUI WebSocket node ID is invalid.")

    kinds = {
        "status": "queue",
        "execution_start": "started",
        "execution_cached": "cached",
        "executing": "executing",
        "progress": "progress",
        "executed": "output",
        "execution_success": "completed",
        "execution_error": "failed",
        "execution_interrupted": "cancelled",
    }
    current: float | None = None
    total: float | None = None
    fraction: float | None = None
    if event_type == "progress":
        current = _progress_number(data.get("value"), "value", allow_zero=True)
        total = _progress_number(data.get("max"), "maximum", allow_zero=False)
        if current > total:
            raise ComfyProtocolError("ComfyUI progress exceeds its maximum.")
        fraction = current / total
    return ComfyEvent(
        type=event_type,
        data=dict(data),
        kind=kinds.get(event_type, "unknown"),
        prompt_id=prompt_id,
        node_id=node_id,
        current=current,
        total=total,
        fraction=fraction,
    )


def _websocket_url(base_url: str, client_id: str) -> str:
    parsed = urlsplit(join_api_url(base_url, "ws"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode({"clientId": client_id}), ""))


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Expected a positive integer.")
    return value


def _optional_nonnegative_float(value: Any) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Expected a non-negative number.")
    return float(value)


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Expected a non-negative integer.")
    return value


def _history_media_type(
    raw_item: Mapping[str, Any],
    filename: str,
    category: str,
) -> str | None:
    declared = next(
        (raw_item[key] for key in ("media_type", "mime_type", "content_type") if key in raw_item),
        None,
    )
    if declared is not None:
        if not isinstance(declared, str):
            raise ValueError("Output media type must be a string.")
        return _normalize_media_type(declared)
    return _media_type(filename, category)


def _media_type(filename: str, category: str) -> str | None:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    known = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "gif": "image/gif",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "json": "application/json",
        "pdf": "application/pdf",
        "txt": "text/plain",
        "csv": "text/csv",
        "zip": "application/zip",
        "gz": "application/gzip",
        "glb": "model/gltf-binary",
        "gltf": "model/gltf+json",
        "safetensors": "application/octet-stream",
    }
    if extension in known:
        return known[extension]
    normalized_category = category.lower()
    if normalized_category in {"gif", "gifs", "image", "images"}:
        return "image/*"
    if normalized_category in {"audio", "audios"}:
        return "audio/*"
    if normalized_category in {"video", "videos"}:
        return "video/*"
    if normalized_category in {"file", "files"}:
        return "application/octet-stream"
    return None


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdigit():
        raise ComfyProtocolError("ComfyUI returned an invalid Content-Length header.")
    return int(value)


def _download_media_type(header: str | None, expected: str | None) -> str:
    try:
        actual = (
            _normalize_media_type(header.split(";", 1)[0])
            if header is not None
            else (
                _normalize_media_type(expected)
                if expected is not None and not expected.endswith("/*")
                else "application/octet-stream"
            )
        )
    except ValueError as exc:
        raise ComfyProtocolError("ComfyUI output media type is invalid.") from exc
    if actual in _FORBIDDEN_MEDIA_TYPES or not _media_type_allowed(actual):
        raise ComfyProtocolError(f"ComfyUI output media type {actual!r} is not permitted.")
    if expected is not None:
        normalized_expected = expected.strip().lower()
        if normalized_expected.endswith("/*"):
            if actual.split("/", 1)[0] != normalized_expected[:-2]:
                raise ComfyProtocolError(
                    "ComfyUI output media type does not match its history metadata."
                )
        elif actual != _normalize_media_type(normalized_expected):
            raise ComfyProtocolError(
                "ComfyUI output media type does not match its history metadata."
            )
    return actual


def _normalize_media_type(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "audio/x-wav": "audio/wav",
        "image/jpg": "image/jpeg",
    }
    normalized = aliases.get(normalized, normalized)
    if not _MEDIA_TYPE.fullmatch(normalized):
        raise ValueError("ComfyUI output media type is invalid.")
    return normalized


def _media_type_allowed(value: str) -> bool:
    if value in _SAFE_APPLICATION_MEDIA_TYPES:
        return True
    major = value.split("/", 1)[0]
    return major in {"audio", "image", "video"} and value not in _FORBIDDEN_MEDIA_TYPES


def _url_origin(value: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ComfySecurityError("ComfyUI URL has an invalid origin.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ComfySecurityError("ComfyUI URL has an invalid port.") from exc
    return (
        parsed.scheme,
        parsed.hostname.lower().rstrip("."),
        port or (443 if parsed.scheme == "https" else 80),
    )


def _progress_number(value: Any, label: str, *, allow_zero: bool) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
        or (not allow_zero and value == 0)
    ):
        raise ComfyProtocolError(f"ComfyUI progress {label} is invalid.")
    return float(value)
