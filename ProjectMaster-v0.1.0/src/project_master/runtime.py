from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_master.agent import ProjectMasterAgent
from project_master.config import MasterConfig
from project_master.core.prompting import PromptBuilder
from project_master.dreams import (
    DreamBackgroundConfig,
    DreamBackgroundExecutor,
    DreamCouncilRunner,
    DreamService,
    DreamSource,
    DreamStore,
    ResourceSnapshot,
    SourceKind,
    SourceSensitivity,
    StoredDreamSchedule,
)
from project_master.dreams.sources import source_matches_scopes
from project_master.integrations.comfyui import (
    ComfyUIProfile,
    ComfyUIService,
    FilesystemComfyArtifactStore,
    HttpxComfyTransport,
    SQLiteComfyStore,
)
from project_master.integrations.voice import (
    EngineAdapter,
    EspeakNgAdapter,
    GovernorVoiceLeaseProvider,
    SQLiteVoiceStore,
    VoiceResourceRequest,
    VoiceStudioService,
    discover_chatterbox_pack,
    discover_espeak_pack,
)
from project_master.knowledge import KnowledgeService, KnowledgeStore
from project_master.llm.ollama import OllamaClient
from project_master.memory.store import SQLiteStore
from project_master.orchestration.resource import (
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.team import OllamaModelCatalog, ProjectMasterTeam, SequentialCouncil
from project_master.tools.builtin import build_registry
from project_master.tools.comfyui import register_comfyui_tools
from project_master.tools.dreams import register_dream_tools
from project_master.tools.knowledge import register_knowledge_tools
from project_master.tools.terminal import (
    TerminalPolicy,
    WorkspaceTerminal,
    register_terminal_tool,
)
from project_master.tools.voice import register_voice_tools


@dataclass(slots=True)
class MasterRuntime:
    config: MasterConfig
    store: SQLiteStore
    profiler: StyleProfiler
    provider: OllamaClient
    agent: ProjectMasterAgent
    orchestration: OrchestrationStore | None = None
    team: ProjectMasterTeam | None = None
    comfy: ComfyUIService | None = None
    comfy_store: SQLiteComfyStore | None = None
    dream: DreamService | None = None
    dream_background: DreamBackgroundExecutor | None = None
    dream_store: DreamStore | None = None
    knowledge: KnowledgeService | None = None
    knowledge_store: KnowledgeStore | None = None
    terminal: WorkspaceTerminal | None = None
    voice: VoiceStudioService | None = None
    voice_store: SQLiteVoiceStore | None = None
    resource_governor: ResourceGovernor | None = None
    model_catalog: OllamaModelCatalog | None = None
    _activity_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _interactive_count: int = field(default=0, init=False, repr=False)
    _foreground_waiters: int = field(default=0, init=False, repr=False)
    _model_execution_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _interactive_owner: str | None = field(default=None, init=False, repr=False)
    _last_interactive_at: float = field(
        default_factory=time.monotonic,
        init=False,
        repr=False,
    )

    def agent_for_model(self, model: str | None = None) -> ProjectMasterAgent:
        if not model or model == self.config.model:
            return self.agent
        provider = self.provider.for_model(
            model,
            max_output_tokens=self.config.max_response_tokens,
        )
        return ProjectMasterAgent(
            provider=provider,
            tools=self.agent.tools,
            store=self.store,
            profiler=self.profiler,
            prompt_builder=PromptBuilder(),
            max_tool_rounds=self.config.max_tool_rounds,
            max_history_messages=self.config.max_history_messages,
            max_prompt_chars=self.config.max_prompt_chars,
        )

    def start_background_services(self) -> bool:
        if self.dream_background is None:
            return False
        return self.dream_background.start()

    def shutdown_background_services(self, timeout_seconds: float = 10.0) -> bool:
        if self.dream_background is None:
            return True
        return self.dream_background.shutdown(
            cancel_running=True,
            timeout_seconds=timeout_seconds,
        )

    def shutdown_local_inference(self) -> str | None:
        """Release any Ollama runner owned by this runtime before its process exits."""
        unload = getattr(self.provider, "unload_active_model", None)
        if not callable(unload):
            return None
        return unload()

    def begin_interactive_model_use(self, timeout_seconds: float = 10.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        deadline = time.monotonic() + timeout_seconds
        with self._activity_lock:
            self._foreground_waiters += 1
            self._last_interactive_at = time.monotonic()
        background = self.dream_background
        if background is not None:
            background.set_interactive_busy(True)
        remaining = max(0.0, deadline - time.monotonic())
        if not self._model_execution_lock.acquire(timeout=remaining):
            self._finish_foreground_wait()
            return False
        if background is not None:
            remaining = max(0.0, deadline - time.monotonic())
            if not background.wait_for_idle(remaining):
                self._model_execution_lock.release()
                self._finish_foreground_wait()
                return False

        owner = f"interactive-chat:{uuid4().hex}"
        governor = self.resource_governor
        while governor is not None and not governor.acquire(
            LOCAL_GPU_INFERENCE_RESOURCE,
            owner,
            ttl_seconds=3_600,
            metadata={
                "subsystem": "chat",
                "priority": 100,
                "preemptible": False,
            },
        ):
            if time.monotonic() >= deadline:
                self._model_execution_lock.release()
                self._finish_foreground_wait()
                return False
            time.sleep(0.05)
        with self._activity_lock:
            self._foreground_waiters -= 1
            self._interactive_count = 1
            self._interactive_owner = owner if governor is not None else None
        return True

    def end_interactive_model_use(self) -> None:
        with self._activity_lock:
            if self._interactive_count <= 0:
                return
            self._interactive_count = 0
            owner = self._interactive_owner
            self._interactive_owner = None
            self._last_interactive_at = time.monotonic()
            idle = self._foreground_waiters == 0
        if owner is not None and self.resource_governor is not None:
            self.resource_governor.release(LOCAL_GPU_INFERENCE_RESOURCE, owner)
        self._model_execution_lock.release()
        if idle and self.dream_background is not None:
            self.dream_background.set_interactive_busy(False)

    def _finish_foreground_wait(self) -> None:
        with self._activity_lock:
            self._foreground_waiters = max(0, self._foreground_waiters - 1)
            idle = self._foreground_waiters == 0 and self._interactive_count == 0
            self._last_interactive_at = time.monotonic()
        if idle and self.dream_background is not None:
            self.dream_background.set_interactive_busy(False)

    @property
    def interactive_model_busy(self) -> bool:
        with self._activity_lock:
            return self._interactive_count > 0 or self._foreground_waiters > 0

    @property
    def interactive_idle_seconds(self) -> float:
        with self._activity_lock:
            if self._interactive_count > 0 or self._foreground_waiters > 0:
                return 0.0
            return max(0.0, time.monotonic() - self._last_interactive_at)


def build_runtime(config_path: str | Path | None = None) -> MasterRuntime:
    config = MasterConfig.load(config_path)
    store = SQLiteStore(config.db_path)
    profiler = StyleProfiler(store)
    provider = OllamaClient(
        base_url=config.ollama_url,
        model=config.model,
        temperature=config.temperature,
        num_ctx=config.num_ctx,
        max_output_tokens=config.max_response_tokens,
        timeout_seconds=config.request_timeout_seconds,
    )
    orchestration = OrchestrationStore(store)
    knowledge_store = KnowledgeStore(store)
    knowledge = KnowledgeService(knowledge_store, orchestration)
    comfy_store = SQLiteComfyStore(store)
    profiles = comfy_store.list_profiles()
    if not profiles:
        comfy_store.upsert_profile(
            ComfyUIProfile(
                id="local-default",
                name="Local ComfyUI",
                base_url="http://127.0.0.1:8188",
            )
        )
        profiles = comfy_store.list_profiles()
    transport_cache: dict[str, HttpxComfyTransport] = {}

    def comfy_transport(profile: ComfyUIProfile) -> HttpxComfyTransport:
        key = profile.model_dump_json()
        transport = transport_cache.get(key)
        if transport is None:
            transport = HttpxComfyTransport(profile)
            transport_cache[key] = transport
        return transport

    comfy = ComfyUIService(
        profiles,
        comfy_transport,
        jobs=comfy_store,
        artifact_store=FilesystemComfyArtifactStore(
            config.db_path.parent / "comfy-artifacts"
        ),
    )
    for stored_workflow in comfy_store.list_workflows():
        comfy.add_workflow(stored_workflow.revision)
    council = SequentialCouncil(
        lambda model: provider.for_model(
            model,
            max_output_tokens=config.team_role_max_tokens,
        )
    )
    dream_store = DreamStore(store)
    dream = DreamService(
        dream_store,
        DreamCouncilRunner(council),
    )
    catalog = OllamaModelCatalog(provider)
    resource_governor = ResourceGovernor(store)
    voice_store = SQLiteVoiceStore(
        store,
        config.db_path.parent / "voice-artifacts",
    )
    voice_adapters: dict[str, EngineAdapter] = {}
    voice_resource_requests: dict[str, VoiceResourceRequest] = {}
    espeak_adapter = EspeakNgAdapter()
    espeak_pack = discover_espeak_pack(espeak_adapter)
    if espeak_pack is not None:
        voice_store.upsert_pack(espeak_pack)
        voice_adapters[espeak_pack.engine_id] = espeak_adapter
        voice_resource_requests[espeak_pack.id] = VoiceResourceRequest(
            kind="cpu",
            minimum_memory_mb=64,
            exclusive=False,
            priority=40,
        )
    chatterbox = discover_chatterbox_pack(
        config.db_path.parent / "voice-engines" / "chatterbox",
        voice_store.reference_path,
    )
    if chatterbox is not None:
        chatterbox_pack, chatterbox_adapter = chatterbox
        voice_store.upsert_pack(chatterbox_pack)
        voice_adapters[chatterbox_pack.engine_id] = chatterbox_adapter
        voice_resource_requests[chatterbox_pack.id] = VoiceResourceRequest(
            kind="gpu",
            minimum_vram_mb=5_000,
            minimum_memory_mb=6_000,
            exclusive=True,
            priority=30,
            preemptible=False,
        )
    supported_voice_packs = tuple(
        pack
        for pack in voice_store.list_packs()
        if pack.engine_id in voice_adapters
    )
    voice = VoiceStudioService(
        profiles=voice_store.list_profiles(),
        projects=voice_store.list_projects(),
        packs=supported_voice_packs,
        adapters=voice_adapters,
        resource_leases=GovernorVoiceLeaseProvider(
            resource_governor,
            before_gpu_acquire=provider.unload_active_model,
        ),
        resource_requests=voice_resource_requests,
        jobs=voice_store.jobs,
        cache=voice_store.cache,
        artifacts=voice_store.artifacts,
    )
    tools = build_registry(store, config.workspace_root, config.allow_file_writes)
    terminal = WorkspaceTerminal(
        TerminalPolicy(
            workspace_root=config.workspace_root,
            enabled=config.terminal_enabled,
            network_enabled=config.terminal_network_enabled,
        )
    )
    register_comfyui_tools(tools, comfy, comfy_store)
    register_dream_tools(
        tools,
        dream,
        catalog,
        configured_model=config.model,
    )
    register_knowledge_tools(tools, knowledge)
    register_terminal_tool(tools, terminal)
    register_voice_tools(tools, voice, voice_store)
    agent = ProjectMasterAgent(
        provider=provider,
        tools=tools,
        store=store,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
        max_tool_rounds=config.max_tool_rounds,
        max_history_messages=config.max_history_messages,
        max_prompt_chars=config.max_prompt_chars,
    )
    runtime = MasterRuntime(
        config,
        store,
        profiler,
        provider,
        agent,
        orchestration=orchestration,
        comfy=comfy,
        comfy_store=comfy_store,
        dream=dream,
        dream_store=dream_store,
        knowledge=knowledge,
        knowledge_store=knowledge_store,
        terminal=terminal,
        voice=voice,
        voice_store=voice_store,
        resource_governor=resource_governor,
        model_catalog=catalog,
    )
    runtime.team = ProjectMasterTeam(
        catalog=catalog,
        council=council,
        agent_factory=runtime.agent_for_model,
        orchestration=orchestration,
        workspace_root=config.workspace_root,
        configured_model=config.model,
    )
    runtime.dream_background = DreamBackgroundExecutor(
        dream,
        resource_governor,
        source_provider=lambda schedule: _scheduled_dream_sources(
            schedule,
            dream_store=dream_store,
            store=store,
            orchestration=orchestration,
        ),
        model_provider=catalog.load,
        resource_provider=lambda: _resource_snapshot(runtime, resource_governor),
        interactive_probe=lambda: runtime.interactive_model_busy,
        config=DreamBackgroundConfig(preferred_lead=config.model),
    )
    return runtime


def _scheduled_dream_sources(
    schedule: StoredDreamSchedule,
    *,
    dream_store: DreamStore,
    store: SQLiteStore,
    orchestration: OrchestrationStore,
) -> tuple[DreamSource, ...]:
    return resolve_scheduled_dream_sources(
        schedule.schedule.recipe_id,
        dream_store=dream_store,
        store=store,
        orchestration=orchestration,
    )


def resolve_scheduled_dream_sources(
    recipe_id: str,
    *,
    dream_store: DreamStore,
    store: SQLiteStore,
    orchestration: OrchestrationStore,
) -> tuple[DreamSource, ...]:
    """Resolve only durable, explicitly opted-in sources declared by the recipe."""
    recipe = dream_store.get_recipe(recipe_id).recipe
    scopes = tuple(scope.strip() for scope in recipe.source_scopes if scope.strip())
    if not scopes:
        return ()

    captured_at = datetime.now(UTC)
    sources: list[DreamSource] = []
    project_ids = _project_scope_ids(scopes, orchestration)
    for project_id in project_ids:
        project = orchestration.get_project(project_id)
        metadata = project.get("metadata", {}) if project else {}
        if (
            not project
            or project.get("status") != "active"
            or not isinstance(metadata, dict)
            or metadata.get("allow_dreaming") is not True
        ):
            continue
        remaining = 64 - len(sources)
        if remaining <= 0:
            break
        with store.connection() as conn:
            tables = {
                str(row["name"])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN ('knowledge_documents', 'knowledge_chunks')
                    """
                ).fetchall()
            }
            if tables != {"knowledge_documents", "knowledge_chunks"}:
                continue
            rows = conn.execute(
                """
                SELECT c.id, c.relative_path, c.line_start, c.line_end, c.content,
                       d.indexed_at
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.project_id = ? AND d.active = 1
                ORDER BY c.relative_path, c.chunk_index
                LIMIT ?
                """,
                (project_id, remaining),
            ).fetchall()
        for row in rows:
            locator = (
                f"project://{project_id}/{row['relative_path']}"
                f"#L{row['line_start']}-L{row['line_end']}"
            )
            source = DreamSource(
                source_id=str(row["id"]),
                kind=SourceKind.PROJECT,
                locator=locator,
                content=str(row["content"]),
                captured_at_utc=_parsed_datetime(str(row["indexed_at"]), captured_at),
                sensitivity=SourceSensitivity.INTERNAL,
                allow_dreaming=True,
            )
            if source_matches_scopes(source, scopes):
                sources.append(source)

    for row in _consented_memory_rows(scopes, store, limit=64 - len(sources)):
        value = row.get("value")
        if not isinstance(value, dict):
            continue
        content = _memory_content(value)
        if not content:
            continue
        try:
            sensitivity = SourceSensitivity(
                str(value.get("sensitivity", SourceSensitivity.INTERNAL.value))
            )
        except ValueError:
            sensitivity = SourceSensitivity.INTERNAL
        source = DreamSource(
            source_id=f"memory-{row['id']}",
            kind=SourceKind.MEMORY,
            locator=f"memory://{row['namespace']}/{row['key']}",
            content=content,
            captured_at_utc=_parsed_datetime(
                str(row.get("updated_at", "")),
                captured_at,
            ),
            sensitivity=sensitivity,
            allow_dreaming=True,
        )
        if source_matches_scopes(source, scopes):
            sources.append(source)
        if len(sources) >= 64:
            break
    return tuple(sources)


def _project_scope_ids(
    scopes: tuple[str, ...],
    orchestration: OrchestrationStore,
) -> tuple[str, ...]:
    include_all = any(
        scope in {"*", "all", "project", "kind:project", "project:*"}
        for scope in scopes
    )
    selected = {
        scope.removeprefix("project:")
        for scope in scopes
        if scope.startswith("project:") and scope != "project:*"
    }
    if include_all:
        selected.update(
            str(project["id"])
            for project in orchestration.list_projects()
            if project.get("id")
        )
    return tuple(sorted(selected))


def _consented_memory_rows(
    scopes: tuple[str, ...],
    store: SQLiteStore,
    *,
    limit: int,
) -> tuple[dict[str, Any], ...]:
    if limit <= 0:
        return ()
    include_all = any(
        scope in {"*", "all", "memory", "kind:memory", "memory:*"}
        for scope in scopes
    )
    namespaces = {
        scope.removeprefix("memory:")
        for scope in scopes
        if scope.startswith("memory:") and scope != "memory:*"
    }
    rows: list[dict[str, Any]] = []
    if include_all:
        rows.extend(store.recall(limit=limit))
    else:
        for namespace in sorted(namespaces):
            rows.extend(store.recall(namespace=namespace, limit=limit - len(rows)))
            if len(rows) >= limit:
                break
    return tuple(
        row
        for row in rows
        if isinstance(row.get("value"), dict)
        and row["value"].get("allow_dreaming") is True
    )


def _memory_content(value: dict[str, Any]) -> str:
    for key in ("content", "text", "value"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    public_value = {
        key: item
        for key, item in value.items()
        if key not in {"allow_dreaming", "sensitivity"}
    }
    return (
        json.dumps(public_value, ensure_ascii=False, sort_keys=True, default=str)
        if public_value
        else ""
    )


def _parsed_datetime(value: str, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return fallback
    return parsed.astimezone(UTC)


def _resource_snapshot(
    runtime: MasterRuntime,
    governor: ResourceGovernor,
) -> ResourceSnapshot:
    lease = governor.status(LOCAL_GPU_INFERENCE_RESOURCE)
    return ResourceSnapshot(
        idle_seconds=runtime.interactive_idle_seconds,
        cpu_percent=_cpu_load_percent(),
        available_memory_bytes=_available_memory_bytes(),
        gpu_free_bytes=_gpu_free_bytes(),
        active_model_jobs=int(runtime.interactive_model_busy or lease is not None),
        on_ac_power=_on_ac_power(),
    )


def _cpu_load_percent() -> float:
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return 0.0
    cores = max(os.cpu_count() or 1, 1)
    return min(max(load / cores * 100.0, 0.0), 100.0)


def _available_memory_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _gpu_free_bytes() -> int | None:
    try:
        result = subprocess.run(
            (
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        free_mib = sum(
            int(line.strip())
            for line in result.stdout.splitlines()
            if line.strip()
        )
    except ValueError:
        return None
    return free_mib * 1024**2


def _on_ac_power() -> bool:
    online_paths = tuple(Path("/sys/class/power_supply").glob("*/online"))
    if not online_paths:
        return True
    values: list[bool] = []
    for path in online_paths:
        try:
            values.append(path.read_text(encoding="utf-8").strip() == "1")
        except OSError:
            continue
    return any(values) if values else True
