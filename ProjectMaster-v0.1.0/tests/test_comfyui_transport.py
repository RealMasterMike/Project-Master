import asyncio
import math
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import pytest

from project_master.integrations.comfyui.profiles import (
    ComfyAuth,
    ComfyUIProfile,
    SecretRef,
)
from project_master.integrations.comfyui.transport import (
    ComfyProtocolError,
    ComfyTransportError,
    HttpxComfyTransport,
    OutputMetadata,
    OutputRef,
    normalize_comfy_event,
    parse_history_result,
    parse_queue_snapshot,
)


def test_official_queue_shape_is_reduced_to_stable_metadata() -> None:
    snapshot = parse_queue_snapshot(
        {
            "queue_running": [
                [3, "running-id", {"large": "workflow omitted"}, {"client_id": "client-1"}]
            ],
            "queue_pending": [
                [4, "queued-id", {"large": "workflow omitted"}, {"client_id": "client-2"}]
            ],
        }
    )

    assert snapshot.running[0].prompt_id == "running-id"
    assert snapshot.running[0].client_id == "client-1"
    assert snapshot.queued[0].number == 4


def test_official_history_shape_preserves_safe_output_provenance() -> None:
    result = parse_history_result(
        "prompt-1",
        {
            "prompt-1": {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": "image.png",
                                "subfolder": "project",
                                "type": "output",
                                "width": 512,
                                "height": 768,
                            }
                        ]
                    }
                },
            }
        },
    )

    assert result.completed
    assert result.outputs[0].node_id == "9"
    assert result.outputs[0].ref.subfolder == "project"
    assert result.outputs[0].media_type == "image/png"
    assert result.outputs[0].height == 768
    assert result.history_sha256 is not None


def test_history_parses_generic_media_and_file_metadata_with_stable_indices() -> None:
    result = parse_history_result(
        "prompt-1",
        {
            "prompt-1": {
                "status": {"completed": True, "status_str": "SUCCESS"},
                "outputs": {
                    "10": {
                        "audio": {
                            "filename": "voice.wav",
                            "type": "output",
                            "duration": 2.5,
                        },
                        "videos": [
                            {"filename": "clip.mp4", "type": "output"},
                            {"filename": "clip-2.webm", "type": "output"},
                        ],
                        "files": [
                            {
                                "filename": "report.pdf",
                                "type": "output",
                                "mime_type": "application/pdf",
                                "size_bytes": 1234,
                            }
                        ],
                    }
                },
            }
        },
    )

    assert result.completed
    assert [item.media_type for item in result.outputs] == [
        "audio/wav",
        "video/mp4",
        "video/webm",
        "application/pdf",
    ]
    assert [item.output_index for item in result.outputs] == [0, 0, 1, 0]
    assert result.outputs[-1].reported_size_bytes == 1234


def test_history_rejects_coerced_completion_and_non_finite_metadata() -> None:
    with pytest.raises(ComfyProtocolError, match="must be boolean"):
        parse_history_result(
            "prompt-1",
            {
                "prompt-1": {
                    "status": {"completed": "false", "status_str": "success"},
                    "outputs": {},
                }
            },
        )
    with pytest.raises(ComfyProtocolError, match="invalid output metadata"):
        parse_history_result(
            "prompt-1",
            {
                "prompt-1": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {
                        "9": {
                            "audio": [
                                {
                                    "filename": "voice.wav",
                                    "duration": math.inf,
                                }
                            ]
                        }
                    },
                }
            },
        )


def test_history_rejects_server_supplied_output_path_escape() -> None:
    with pytest.raises(ComfyProtocolError, match="unsafe"):
        parse_history_result(
            "prompt-1",
            {
                "prompt-1": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {
                        "9": {
                            "images": [
                                {
                                    "filename": "../../secret.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
            },
        )


def test_history_rejects_known_download_category_without_file_metadata() -> None:
    with pytest.raises(ComfyProtocolError, match="missing file metadata"):
        parse_history_result(
            "prompt-1",
            {
                "prompt-1": {
                    "status": {"completed": True, "status_str": "success"},
                    "outputs": {"9": {"images": [{"width": 512}]}},
                }
            },
        )


class FakeEventSource:
    def __init__(self) -> None:
        self.url: str | None = None
        self.headers: Mapping[str, str] | None = None

    async def events(
        self, url: str, headers: Mapping[str, str]
    ) -> AsyncIterator[Mapping[str, Any]]:
        self.url = url
        self.headers = headers
        yield {"type": "executing", "data": {"node": "9"}}


def test_websocket_event_source_uses_official_ws_route_and_typed_events() -> None:
    source = FakeEventSource()
    transport = HttpxComfyTransport(
        ComfyUIProfile(id="local", name="Local"),
        event_source=source,
    )

    async def exercise() -> None:
        events = [event async for event in transport.events("project-master-client")]
        assert events[0].type == "executing"
        assert events[0].data == {"node": "9"}
        await transport.aclose()

    asyncio.run(exercise())
    assert source.url == "ws://127.0.0.1:8188/ws?clientId=project-master-client"
    assert source.headers == {}


def test_websocket_progress_is_normalized_but_remains_advisory() -> None:
    event = normalize_comfy_event(
        {
            "type": "progress",
            "data": {
                "prompt_id": "prompt-1",
                "node": "9",
                "value": 3,
                "max": 12,
            },
        }
    )

    assert event.kind == "progress"
    assert event.prompt_id == "prompt-1"
    assert event.node_id == "9"
    assert event.fraction == 0.25
    with pytest.raises(ComfyProtocolError, match="maximum"):
        normalize_comfy_event({"type": "progress", "data": {"value": 1, "max": 0}})


class ChunkedStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"123"
        yield b"456"


def test_output_download_uses_exact_view_origin_auth_and_verified_media_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setenv("COMFY_OUTPUT_TEST_TOKEN", "download-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\ncontent",
            headers={"content-type": "image/png"},
            request=request,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    transport = HttpxComfyTransport(
        ComfyUIProfile(
            id="local",
            name="Local",
            auth=ComfyAuth(secret_ref=SecretRef(key="COMFY_OUTPUT_TEST_TOKEN")),
        ),
        client=client,
    )
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="frame 01.png", subfolder="daily"),
        media_type="image/png",
    )

    async def exercise() -> None:
        download = await transport.download_output(output)
        assert download.content.endswith(b"content")
        assert download.media_type == "image/png"
        assert download.source_url.startswith("http://127.0.0.1:8188/view?")
        await client.aclose()

    asyncio.run(exercise())
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].url.path == "/view"
    assert requests[0].url.params["filename"] == "frame 01.png"
    assert requests[0].headers["authorization"] == "Bearer download-secret"


def test_output_download_rejects_chunked_overflow_and_mime_mismatch() -> None:
    responses = [
        httpx.Response(
            200,
            stream=ChunkedStream(),
            headers={"content-type": "application/octet-stream"},
        ),
        httpx.Response(
            200,
            content=b"not-an-image",
            headers={"content-type": "text/plain"},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    binary_transport = HttpxComfyTransport(
        ComfyUIProfile(id="local", name="Local"),
        client=client,
        max_output_bytes=5,
    )
    binary = OutputMetadata(
        node_id="9",
        category="files",
        ref=OutputRef(filename="result.bin"),
        media_type="application/octet-stream",
    )
    image_transport = HttpxComfyTransport(
        ComfyUIProfile(id="local-two", name="Local"),
        client=client,
    )
    image = OutputMetadata(
        node_id="10",
        category="images",
        ref=OutputRef(filename="result.png"),
        media_type="image/png",
    )

    async def exercise() -> None:
        with pytest.raises(ComfyProtocolError, match="size limit"):
            await binary_transport.download_output(binary)
        with pytest.raises(ComfyProtocolError, match="does not match"):
            await image_transport.download_output(image)
        await client.aclose()

    asyncio.run(exercise())


def test_output_download_refuses_cross_origin_redirect_without_following_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            request=request,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    transport = HttpxComfyTransport(
        ComfyUIProfile(id="local", name="Local"),
        client=client,
    )
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="result.png"),
        media_type="image/png",
    )

    async def exercise() -> None:
        with pytest.raises(ComfyTransportError, match="redirects"):
            await transport.download_output(output)
        await client.aclose()

    asyncio.run(exercise())
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"
