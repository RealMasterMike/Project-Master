from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


class MasterConfig(BaseModel):
    model: str = (
        "hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
    )
    ollama_url: str = "http://127.0.0.1:11434"
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    num_ctx: int = Field(default=65536, ge=2048, le=1048576)
    max_response_tokens: int = Field(default=2048, ge=128, le=32768)
    team_role_max_tokens: int = Field(default=768, ge=128, le=8192)
    max_prompt_chars: int = Field(default=24000, ge=8000, le=500000)
    max_tool_rounds: int = Field(default=6, ge=1, le=30)
    max_auto_continuations: int = Field(default=2, ge=0, le=6)
    max_history_messages: int = Field(default=30, ge=2, le=500)
    db_path: Path = Path("master.db")
    workspace_root: Path = Path("workspace")
    allow_file_writes: bool = False
    terminal_enabled: bool = False
    terminal_network_enabled: bool = False
    self_review: bool = False
    request_timeout_seconds: float = Field(default=180.0, gt=0)

    @field_validator("ollama_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @classmethod
    def load(cls, path: str | Path | None = None) -> MasterConfig:
        load_dotenv()
        config_path = Path(path or os.getenv("MASTER_CONFIG", "config/default.yaml"))
        data: dict[str, Any] = {}
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)

        env_map: dict[str, tuple[str, Any]] = {
            "MASTER_MODEL": ("model", str),
            "MASTER_OLLAMA_URL": ("ollama_url", str),
            "MASTER_NUM_CTX": ("num_ctx", int),
            "MASTER_MAX_RESPONSE_TOKENS": ("max_response_tokens", int),
            "MASTER_TEAM_ROLE_MAX_TOKENS": ("team_role_max_tokens", int),
            "MASTER_MAX_PROMPT_CHARS": ("max_prompt_chars", int),
            "MASTER_MAX_AUTO_CONTINUATIONS": ("max_auto_continuations", int),
            "MASTER_DB_PATH": ("db_path", Path),
            "MASTER_WORKSPACE_ROOT": ("workspace_root", Path),
            "MASTER_ALLOW_FILE_WRITES": ("allow_file_writes", _parse_bool),
            "MASTER_TERMINAL_ENABLED": ("terminal_enabled", _parse_bool),
            "MASTER_TERMINAL_NETWORK_ENABLED": (
                "terminal_network_enabled",
                _parse_bool,
            ),
            "MASTER_SELF_REVIEW": ("self_review", _parse_bool),
        }
        for env_name, (field_name, parser) in env_map.items():
            raw = os.getenv(env_name)
            if raw is not None:
                data[field_name] = parser(raw)

        config = cls.model_validate(data)
        config.workspace_root.mkdir(parents=True, exist_ok=True)
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        return config


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")
