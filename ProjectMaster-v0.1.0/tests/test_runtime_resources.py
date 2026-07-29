from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import project_master.runtime as runtime_module
from project_master.agent import ProjectMasterAgent
from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.integrations.comfyui.profiles import ComfyUIProfile
from project_master.integrations.comfyui.service import ComfyUIService
from project_master.integrations.comfyui.transport import (
    QueueEntry,
    QueueSnapshot,
)
from project_master.integrations.voice.cache import VoiceChunkPlan
from project_master.integrations.voice.governor import GovernorVoiceLeaseProvider
from project_master.integrations.voice.jobs import (
    VOICE_RENDER_OWNER_PREFIX,
    RenderJob,
    RenderJobStatus,
)
from project_master.integrations.voice.persistence import SQLiteVoiceStore
from project_master.integrations.voice.profiles import RenderPurpose
from project_master.integrations.voice.projects import RenderSettings
from project_master.integrations.voice.resources import (
    ResourceLease,
    VoiceResourceRequest,
)
from project_master.memory.store import SQLiteStore
from project_master.orchestration.resource import (
    INTERACTIVE_CHAT_OWNER_PREFIX,
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime, build_runtime
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


def test_interactive_chat_runs_comfy_handoff_while_holding_gpu_lease(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    governor = runtime.resource_governor
    assert governor is not None
    observations: list[str] = []

    def prepare(_timeout_seconds: float) -> bool:
        lease = governor.status(LOCAL_GPU_INFERENCE_RESOURCE)
        assert lease is not None
        observations.append(str(lease["metadata"]["subsystem"]))
        return True

    runtime.prepare_interactive_model_use = prepare

    assert runtime.begin_interactive_model_use(timeout_seconds=0.2)
    assert observations == ["chat"]
    runtime.end_interactive_model_use()


def test_busy_comfy_handoff_uses_existing_interactive_busy_cleanup(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    governor = runtime.resource_governor
    assert governor is not None
    runtime.prepare_interactive_model_use = lambda _timeout: False

    assert runtime.begin_interactive_model_use(timeout_seconds=0.05) is False
    assert runtime.interactive_model_busy is False
    assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None

    runtime.prepare_interactive_model_use = lambda _timeout: True
    assert runtime.begin_interactive_model_use(timeout_seconds=0.2)
    runtime.end_interactive_model_use()


def test_runtime_comfy_handoff_skips_offline_profile_and_releases_idle_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = (
        ComfyUIProfile(id="idle", name="Idle"),
        ComfyUIProfile(id="offline", name="Offline"),
    )
    events: list[tuple[str, str]] = []

    class HandoffTransport:
        def __init__(self, profile: ComfyUIProfile) -> None:
            self.profile = profile
            events.append(("open", profile.id))

        async def queue(self) -> QueueSnapshot:
            events.append(("queue", self.profile.id))
            if self.profile.id == "offline":
                raise OSError("optional ComfyUI is offline")
            return QueueSnapshot()

        async def free_models_and_memory(self) -> None:
            events.append(("free", self.profile.id))

        async def aclose(self) -> None:
            events.append(("close", self.profile.id))

    comfy = ComfyUIService(
        profiles,
        lambda _profile: pytest.fail("persistent transport must not be reused"),
    )
    monkeypatch.setattr(runtime_module, "HttpxComfyTransport", HandoffTransport)

    assert runtime_module._prepare_comfy_for_interactive_model(
        comfy,
        timeout_seconds=0.2,
    )
    assert ("free", "idle") in events
    assert ("free", "offline") not in events
    assert ("close", "idle") in events
    assert ("close", "offline") in events


def test_runtime_comfy_handoff_never_frees_an_active_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ComfyUIProfile(id="active", name="Active")
    events: list[str] = []

    class HandoffTransport:
        def __init__(self, _profile: ComfyUIProfile) -> None:
            pass

        async def queue(self) -> QueueSnapshot:
            events.append("queue")
            return QueueSnapshot(
                queued=(
                    QueueEntry(
                        prompt_id="prompt-active",
                        state="queued",
                    ),
                )
            )

        async def free_models_and_memory(self) -> None:
            events.append("free")

        async def aclose(self) -> None:
            events.append("close")

    comfy = ComfyUIService(
        [profile],
        lambda _profile: pytest.fail("persistent transport must not be reused"),
    )
    monkeypatch.setattr(runtime_module, "HttpxComfyTransport", HandoffTransport)

    assert (
        runtime_module._prepare_comfy_for_interactive_model(
            comfy,
            timeout_seconds=0.01,
        )
        is False
    )
    assert "queue" in events
    assert "free" not in events
    assert events[-1] == "close"


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


def test_orphaned_interactive_chat_lease_is_cleared_and_unblocks_voice(
    tmp_path: Path,
) -> None:
    """A chat lease that survived a hard kill must not block GPU work.

    The interactive-chat lease is released in a `finally`, so it only outlives
    its request when the backend was SIGKILLed. Its hour-long TTL then blocked
    every voice render with an opaque "busy" error until it expired.
    """
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)

    # Simulate the orphan: a chat lease left behind by a killed process.
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        "interactive-chat:deadbeef",
        ttl_seconds=3_600,
        metadata={"subsystem": "chat", "preemptible": False},
    )
    # A voice render cannot take the GPU while the orphan holds it.
    assert (
        governor.acquire(
            LOCAL_GPU_INFERENCE_RESOURCE, "voice-render", ttl_seconds=30
        )
        is False
    )

    cleared = governor.release_process_scoped(INTERACTIVE_CHAT_OWNER_PREFIX)

    assert cleared == 1
    assert governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE, "voice-render", ttl_seconds=30
    )


def test_clearing_chat_leases_leaves_other_subsystems_alone(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE, "voice-render", ttl_seconds=30
    )

    assert governor.release_process_scoped(INTERACTIVE_CHAT_OWNER_PREFIX) == 0

    lease = governor.status(LOCAL_GPU_INFERENCE_RESOURCE)
    assert lease is not None
    assert lease["owner"] == "voice-render"


def test_runtime_startup_recovers_voice_job_and_orphaned_gpu_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = MasterConfig(
        model="test",
        db_path=tmp_path / "master.db",
        workspace_root=tmp_path / "workspace",
    )
    store = SQLiteStore(config.db_path)
    OrchestrationStore(store)
    voice_store = SQLiteVoiceStore(store, tmp_path / "voice-artifacts")
    raw_plan = {
        "schema_version": 1,
        "ordinal": 0,
        "block_id": "message",
        "block_chunk_index": 0,
        "text": "Recover after a hard kill.",
        "language": "en-US",
        "voice_profile_id": "narrator",
        "voice_profile_digest": "1" * 64,
        "project_digest": "2" * 64,
        "engine_pack_id": "pack-1",
        "engine_pack_digest": "3" * 64,
        "performance_direction": "",
        "speed": 1.0,
        "pause_after_ms": 0,
        "pronunciations": (),
        "output_format": "wav",
        "sample_rate_hz": 24_000,
        "channels": 1,
        "seed": 0,
        "normalize_loudness": True,
    }
    provisional = VoiceChunkPlan.model_construct(id="", cache_key="", **raw_plan)
    plan = VoiceChunkPlan(
        id=f"voice-chunk-{provisional._instance_digest()[:32]}",
        cache_key=f"voice-cache-{provisional._cache_digest()[:32]}",
        **raw_plan,
    )
    job = RenderJob.new(
        job_id=f"{VOICE_RENDER_OWNER_PREFIX}orphaned",
        project_id="project-1",
        project_digest="2" * 64,
        engine_pack_id="pack-1",
        engine_pack_digest="3" * 64,
        purpose=RenderPurpose.PRIVATE,
        settings=RenderSettings(),
        plans=(plan,),
    )
    job = voice_store.create_job(job)
    job = voice_store.save_job(
        job.transition(RenderJobStatus.WAITING_RESOURCE),
        expected_version=job.version,
    )
    job = voice_store.save_job(
        job.transition(RenderJobStatus.RUNNING, lease_id="gpu-lease"),
        expected_version=job.version,
    )
    job = voice_store.save_job(
        job.replace_chunk(job.chunks[0].running()),
        expected_version=job.version,
    )
    governor = ResourceGovernor(store)
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        job.id,
        ttl_seconds=3_600,
        metadata={"subsystem": "voice", "preemptible": False},
    )

    monkeypatch.setattr(
        MasterConfig,
        "load",
        classmethod(lambda _cls, _path=None: config),
    )
    restarted = build_runtime()

    assert restarted.voice_store is not None
    recovered = restarted.voice_store.get_job(job.id)
    assert recovered.status is RenderJobStatus.INTERRUPTED
    assert recovered.resource_lease_id is None
    assert recovered.chunks[0].status == "pending"
    assert restarted.resource_governor is not None
    assert (
        restarted.resource_governor.status(LOCAL_GPU_INFERENCE_RESOURCE) is None
    )


def test_voice_render_waits_for_a_busy_chat_lease_instead_of_failing(
    tmp_path: Path,
) -> None:
    """Rendering right after a chat turn must not hard-fail.

    Chat holds this GPU lease for the length of a turn. The voice provider
    used to give up on the first refusal, so a render started while a chat
    was finishing always failed with an unactionable error.
    """
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        "interactive-chat:busy",
        ttl_seconds=30,
        metadata={"subsystem": "chat"},
    )
    provider = GovernorVoiceLeaseProvider(governor, wait_seconds=5.0)

    async def exercise() -> ResourceLease:
        async def free_the_gpu() -> None:
            await asyncio.sleep(0.1)
            governor.release(LOCAL_GPU_INFERENCE_RESOURCE, "interactive-chat:busy")

        release_task = asyncio.ensure_future(free_the_gpu())
        try:
            return await provider.acquire(
                VoiceResourceRequest(kind="gpu"), owner_id="voice-job"
            )
        finally:
            await release_task

    lease = asyncio.run(exercise())

    assert lease.owner_id == "voice-job"
    assert lease.resource_id == LOCAL_GPU_INFERENCE_RESOURCE


def test_voice_render_reports_which_resource_stayed_busy(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "master.db")
    OrchestrationStore(store)
    governor = ResourceGovernor(store)
    assert governor.acquire(
        LOCAL_GPU_INFERENCE_RESOURCE,
        "interactive-chat:busy",
        ttl_seconds=30,
        metadata={"subsystem": "chat"},
    )
    provider = GovernorVoiceLeaseProvider(governor, wait_seconds=0.05)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(
            provider.acquire(VoiceResourceRequest(kind="gpu"), owner_id="voice-job")
        )

    assert LOCAL_GPU_INFERENCE_RESOURCE in str(excinfo.value)
    assert "retry" in str(excinfo.value).lower()
