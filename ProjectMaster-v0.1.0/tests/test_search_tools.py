from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from project_master.memory.store import SQLiteStore
from project_master.tools.base import ToolRegistry
from project_master.tools.search import register_search_tools


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str, timeout: float = 0.0) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse(self.payload)


def _registry(
    tmp_path: Path,
    *,
    client: Any | None = None,
    resolver: Any | None = None,
    clock: Any | None = None,
) -> tuple[ToolRegistry, SQLiteStore, Path]:
    store = SQLiteStore(tmp_path / "master.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    registry = ToolRegistry()
    register_search_tools(
        registry,
        store,
        workspace,
        http_client=client,
        host_resolver=resolver,
        monotonic_clock=clock,
    )
    return registry, store, workspace


def test_conversation_search_finds_a_past_turn(tmp_path: Path) -> None:
    registry, store, _ = _registry(tmp_path)
    session = store.create_session("Planning")
    store.add_message(session, "user", "We decided to use SQLite FTS5 for the Binder.")
    store.add_message(session, "assistant", "Understood, noted for later.")

    ok, result = registry.execute("conversation_search", {"query": "FTS5"})

    assert ok
    assert "SQLite FTS5" in result
    assert session in result


def test_conversation_search_treats_wildcards_literally(tmp_path: Path) -> None:
    """A bare % must not match every message."""
    registry, store, _ = _registry(tmp_path)
    session = store.create_session()
    store.add_message(session, "user", "no percent sign here")
    store.add_message(session, "user", "discount is 50% today")

    matches = store.search_messages("%")

    assert len(matches) == 1
    assert "50%" in matches[0]["snippet"]


def test_file_search_finds_content_and_reports_the_line(tmp_path: Path) -> None:
    registry, _store, workspace = _registry(tmp_path)
    (workspace / "notes.md").write_text(
        "intro\nthe answer is fortytwo\noutro", encoding="utf-8"
    )

    ok, result = registry.execute("file_search", {"query": "fortytwo"})

    assert ok
    assert "notes.md" in result
    assert '"line": 2' in result


def test_file_search_skips_secrets_and_dependency_trees(tmp_path: Path) -> None:
    registry, _store, workspace = _registry(tmp_path)
    (workspace / ".env").write_text("API_KEY=supersecret", encoding="utf-8")
    vendored = workspace / "node_modules"
    vendored.mkdir()
    (vendored / "dep.js").write_text("supersecret", encoding="utf-8")

    ok, result = registry.execute("file_search", {"query": "supersecret"})

    assert ok
    assert ".env" not in result
    assert "node_modules" not in result
    assert '"count": 0' in result


def test_file_search_cannot_escape_the_workspace(tmp_path: Path) -> None:
    registry, _store, workspace = _registry(tmp_path)
    (tmp_path / "outside.txt").write_text("private", encoding="utf-8")

    ok, result = registry.execute("file_search", {"query": "private", "path": ".."})

    assert not ok
    assert "escapes" in result.lower()


def test_file_search_uses_the_request_selected_project_root(tmp_path: Path) -> None:
    registry, _store, workspace = _registry(tmp_path)
    (workspace / "global.txt").write_text("scope-marker", encoding="utf-8")
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "selected.txt").write_text("scope-marker", encoding="utf-8")

    with (
        registry.project_scope("selected-project", workspace_available=True),
        registry.workspace_scope(selected),
    ):
        ok, result = registry.execute("file_search", {"query": "scope-marker"})

    assert ok
    assert "selected.txt" in result
    assert "global.txt" not in result
    assert f'"root": "{selected}"' in result


def test_rootless_project_keeps_non_file_search_but_blocks_file_search(
    tmp_path: Path,
) -> None:
    registry, _store, _workspace = _registry(tmp_path)

    with registry.project_scope("creator-project", workspace_available=False):
        schemas = {
            schema["function"]["name"]
            for schema in registry.schemas()
        }
        ok, result = registry.execute("file_search", {"query": "anything"})

    assert "conversation_search" in schemas
    assert "file_search" not in schemas
    assert not ok
    assert "has no local workspace" in result


def test_web_search_is_unavailable_and_honest_without_an_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MASTER_SEARXNG_URL", raising=False)
    registry, _store, _ = _registry(tmp_path)

    with registry.external_network_scope(True):
        ok, result = registry.execute("web_search", {"query": "anything"})

    assert ok
    assert '"available": false' in result.lower()
    assert "not configured" in result
    # Nothing may imply a search happened.
    assert '"results": []' in result


def test_web_search_queries_the_configured_searxng_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MASTER_SEARXNG_URL", "http://127.0.0.1:8888/")
    client = _FakeClient(
        {
            "results": [
                {
                    "title": "Local first software",
                    "url": "https://example.invalid/a",
                    "content": "A summary.",
                }
            ]
        }
    )
    registry, _store, _ = _registry(tmp_path, client=client)

    with registry.external_network_scope(True):
        ok, result = registry.execute("web_search", {"query": "local first"})

    assert ok
    assert "Local first software" in result
    assert client.urls[0].startswith("http://127.0.0.1:8888/search?")
    assert "q=local+first" in client.urls[0]
    assert "127.0.0.1:8888" not in result
    # The user must be able to see that this call left the machine.
    assert "left the machine" in result


def test_web_search_reports_a_failing_instance_instead_of_inventing_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MASTER_SEARXNG_URL", "http://127.0.0.1:8888")

    class _BrokenClient:
        def get(self, url: str, timeout: float = 0.0) -> _FakeResponse:
            raise ConnectionError("instance unreachable")

    registry, _store, _ = _registry(tmp_path, client=_BrokenClient())

    with registry.external_network_scope(True):
        ok, result = registry.execute("web_search", {"query": "anything"})

    assert ok
    assert "ConnectionError" in result
    assert "instance unreachable" not in result
    assert "127.0.0.1:8888" not in result
    assert '"results": []' in result


def test_web_search_rejects_invalid_payloads_and_credential_bearing_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MASTER_SEARXNG_URL", "http://127.0.0.1:8888")
    registry, _store, _ = _registry(
        tmp_path,
        client=_FakeClient({"results": "not-a-list"}),
    )
    with registry.external_network_scope(True):
        valid_request, invalid_payload = registry.execute(
            "web_search",
            {"query": "anything"},
        )

    monkeypatch.setenv(
        "MASTER_SEARXNG_URL",
        "https://private-user:private-password@example.test",
    )
    with registry.external_network_scope(True):
        credential_request, credential_error = registry.execute(
            "web_search",
            {"query": "anything"},
        )

    assert valid_request
    assert "invalid response" in invalid_payload
    assert credential_request
    assert "ValueError" in credential_error
    assert "private-user" not in credential_error
    assert "private-password" not in credential_error


def test_web_search_is_hidden_and_blocked_without_explicit_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MASTER_SEARXNG_URL", "http://127.0.0.1:8888")
    client = _FakeClient({"results": []})
    registry, _store, _ = _registry(tmp_path, client=client)

    web_tool = next(
        item for item in registry.inventory() if item["name"] == "web_search"
    )
    schemas = {
        schema["function"]["name"]
        for schema in registry.schemas()
    }
    ok, result = registry.execute("web_search", {"query": "private prompt"})

    assert "web_search" not in schemas
    assert not ok
    assert "PermissionError" in result
    assert "web-access authorization" in result
    assert client.urls == []
    assert web_tool["risk"] == "external_network"
    assert web_tool["requires_explicit_chat_authorization"] is True
    assert web_tool["available_in_default_chat"] is False

    with registry.external_network_scope(True):
        authorized_schemas = {
            schema["function"]["name"]
            for schema in registry.schemas()
        }
    assert "web_search" in authorized_schemas
    assert "web_fetch" in authorized_schemas


class _FetchResponse:
    def __init__(
        self,
        content: bytes = b"",
        *,
        chunks: list[bytes] | None = None,
        content_type: str = "text/html; charset=utf-8",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else [content]
        self.chunks_read = 0
        self.closed = False
        self.encoding = "utf-8"
        self.status_code = status_code
        self.headers = {
            "content-type": content_type,
            "content-length": str(sum(len(chunk) for chunk in self.chunks)),
            **(headers or {}),
        }

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_raw(self, chunk_size: int | None = None) -> Any:
        del chunk_size
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk

    def __enter__(self) -> _FetchResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.closed = True


class _FetchClient:
    def __init__(self, responses: list[_FetchResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []
        self.requests: list[dict[str, Any]] = []

    def stream(self, method: str, url: Any, **kwargs: Any) -> _FetchResponse:
        self.urls.append(str(url))
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_web_fetch_extracts_readable_html_with_explicit_authorization(
    tmp_path: Path,
) -> None:
    client = _FetchClient(
        [
            _FetchResponse(
                b"<html><head><title>Example page</title><style>hidden</style></head>"
                b"<body><h1>Public heading</h1><script>secret()</script><p>Useful text.</p>"
                b"</body></html>"
            )
        ]
    )
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda _host: ("93.184.216.34",),
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/article", "max_chars": 5_000},
        )

    assert ok
    assert "Example page" in result
    assert "Public heading" in result
    assert "Useful text." in result
    assert "secret()" not in result
    assert "hidden" not in result
    assert "untrusted_external_content" in result
    assert client.urls == ["https://93.184.216.34/article"]
    request = client.requests[0]
    assert request["headers"]["Host"] == "example.com"
    assert request["headers"]["Accept-Encoding"] == "identity"
    assert request["extensions"] == {"sni_hostname": "example.com"}
    assert request["follow_redirects"] is False


@pytest.mark.parametrize(
    ("url", "addresses", "message"),
    [
        ("file:///etc/passwd", ("93.184.216.34",), "only HTTP"),
        ("http://user:pass@example.com/", ("93.184.216.34",), "credentials"),
        ("http://example.com:8080/", ("93.184.216.34",), "standard"),
        ("http://127.0.0.1/", ("127.0.0.1",), "private or local"),
        ("http://example.com/", ("10.0.0.2",), "private or local"),
    ],
)
def test_web_fetch_blocks_unsafe_targets_before_network_access(
    tmp_path: Path,
    url: str,
    addresses: tuple[str, ...],
    message: str,
) -> None:
    client = _FetchClient([])
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda _host: addresses,
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute("web_fetch", {"url": url})

    assert not ok
    assert message in result
    assert client.urls == []


def test_web_fetch_revalidates_redirect_targets(tmp_path: Path) -> None:
    client = _FetchClient(
        [
            _FetchResponse(
                b"",
                status_code=302,
                headers={"location": "http://127.0.0.1/private"},
            )
        ]
    )
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda host: (
            ("127.0.0.1",) if host == "127.0.0.1" else ("93.184.216.34",)
        ),
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/redirect"},
        )

    assert not ok
    assert "private or local" in result
    assert client.urls == ["https://93.184.216.34/redirect"]


def test_web_fetch_stops_streaming_as_soon_as_decoded_limit_is_exceeded(
    tmp_path: Path,
) -> None:
    response = _FetchResponse(
        chunks=[
            b"a" * 1_999_000,
            b"b" * 2_000,
            b"must-not-be-read",
        ],
        headers={"content-length": ""},
    )
    client = _FetchClient([response])
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda _host: ("93.184.216.34",),
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/large"},
        )

    assert not ok
    assert "2 MB" in result
    assert response.chunks_read == 2
    assert response.closed is True


def test_web_fetch_rejects_compression_before_consuming_the_body(
    tmp_path: Path,
) -> None:
    response = _FetchResponse(
        b"compressed payload",
        headers={"content-encoding": "gzip"},
    )
    client = _FetchClient([response])
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda _host: ("93.184.216.34",),
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/compressed"},
        )

    assert not ok
    assert "Compressed" in result
    assert response.chunks_read == 0
    assert response.closed is True


def test_web_fetch_enforces_one_deadline_across_the_stream(
    tmp_path: Path,
) -> None:
    now = [100.0]

    class _SlowResponse(_FetchResponse):
        def iter_raw(self, chunk_size: int | None = None) -> Any:
            del chunk_size
            self.chunks_read += 1
            yield b"first"
            now[0] += 31.0
            self.chunks_read += 1
            yield b"late"

    response = _SlowResponse(headers={"content-length": ""})
    client = _FetchClient([response])
    registry, _store, _workspace = _registry(
        tmp_path,
        client=client,
        resolver=lambda _host: ("93.184.216.34",),
        clock=lambda: now[0],
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/slow"},
        )

    assert not ok
    assert "30 second total deadline" in result
    assert response.closed is True


def test_web_fetch_default_client_ignores_environment_proxies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[dict[str, Any], _FetchClient]] = []

    class _ManagedFetchClient(_FetchClient):
        def __enter__(self) -> _ManagedFetchClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def client_factory(**kwargs: Any) -> _ManagedFetchClient:
        client = _ManagedFetchClient([_FetchResponse(b"public text")])
        created.append((kwargs, client))
        return client

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(
        "project_master.tools.search.httpx.Client",
        client_factory,
    )
    registry, _store, _workspace = _registry(
        tmp_path,
        resolver=lambda _host: ("93.184.216.34",),
    )

    with registry.external_network_scope(True):
        ok, result = registry.execute(
            "web_fetch",
            {"url": "https://example.com/no-proxy"},
        )

    assert ok, result
    assert created[0][0]["trust_env"] is False
    assert created[0][1].urls == ["https://93.184.216.34/no-proxy"]
