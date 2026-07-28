from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from project_master.agent import ProjectMasterAgent
from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.integrations.voice.governor import GovernorVoiceLeaseProvider
from project_master.integrations.voice.resources import VoiceResourceRequest
from project_master.memory.store import SQLiteStore
from project_master.orchestration.resource import (
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime
from project_master.tools.builtin import build_registry


class _Provider:
    model = "test"

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        return Message(role="assistant", content="ok")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        yield Message(role="assistant", content="ok")


def _runtime(tmp_path: Path) -> MasterRuntime:
    config = MasterConfig(
        model="test",
        db_path=tmp_path / "master.db",
        workspace_root=tmp_path / "workspace",
    )
    store = SQLiteStore(config.db_path)
    OrchestrationStore(store)
    profiler = StyleProfiler(store)
    provider = _Provider()
    agent = ProjectMasterAgent(
        provider=provider,
        tools=build_registry(store, config.workspace_root),
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
    )
    return MasterRuntime(
        config,
        store,
        profiler,
        provider,  # type: ignore[arg-type]
        agent,
        resource_governor=ResourceGovernor(store),
    )


def test_interactive_chat_uses_the_shared_gpu_lease_and_waits_for_voice(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    governor = runtime.resource_governor
    assert governor is not None
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        "voice-render",
        ttl_seconds=30,
        metadata={"subsystem": "voice", "preemptible": False},
    )

    assert runtime.begin_interactive_model_use(timeout_seconds=0.05) is False
    assert runtime.interactive_model_busy is False
    assert governor.release(LOCAL_GPU_INFERENCE_RESOURCE, "voice-render")

    assert runtime.begin_interactive_model_use(timeout_seconds=0.2) is True
    lease = governor.status(LOCAL_GPU_INFERENCE_RESOURCE)
    assert lease is not None
    assert lease["metadata"]["subsystem"] == "chat"
    runtime.end_interactive_model_use()
    assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None


def test_gpu_voice_lease_unloads_warm_project_master_model(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)
    events: list[str] = []
    provider = GovernorVoiceLeaseProvider(
        governor,
        before_gpu_acquire=lambda: events.append("unload"),
    )

    async def exercise() -> None:
        lease = await provider.acquire(
            VoiceResourceRequest(kind="gpu"),
            owner_id="voice-render",
        )

        assert events == ["unload"]
        assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is not None
        await provider.release(lease)
        assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None

    asyncio.run(exercise())


def test_failed_voice_model_unload_releases_gpu_lease(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)

    def fail_unload() -> None:
        raise RuntimeError("Ollama unload failed")

    provider = GovernorVoiceLeaseProvider(
        governor,
        before_gpu_acquire=fail_unload,
    )

    with pytest.raises(RuntimeError, match="Ollama unload failed"):
        asyncio.run(
            provider.acquire(
                VoiceResourceRequest(kind="gpu"),
                owner_id="voice-render",
            )
        )

    assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None
