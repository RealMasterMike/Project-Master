from pathlib import Path
from typing import Any

import httpx

from project_master.config import MasterConfig
from project_master.core.models import Message
from project_master.llm.ollama import OllamaClient


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
