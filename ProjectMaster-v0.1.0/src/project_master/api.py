from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from project_master import __version__
from project_master.core.audit import audit_response
from project_master.core.cancellation import StreamCancellationRegistry
from project_master.dreams import (
    CatchUpMode,
    DreamBackgroundExecutor,
    DreamDisposition,
    DreamInboxError,
    DreamRecipe,
    DreamRecipeKind,
    DreamSchedule,
    DreamService,
    DreamSource,
    PromotionTarget,
    QuietWindow,
    ResourceRules,
    RoleAngle,
    SnapshotPolicy,
    SourceKind,
    SourceSensitivity,
)
from project_master.dreams.sources import unsupported_scheduled_source_scopes
from project_master.integrations.comfyui import (
    ComfyAuth,
    ComfyUIProfile,
    ComfyUIService,
    SQLiteComfyStore,
    WorkflowBinding,
)
from project_master.integrations.comfyui.jobs import (
    JobConflictError as ComfyJobConflictError,
)
from project_master.integrations.comfyui.jobs import (
    JobNotFoundError as ComfyJobNotFoundError,
)
from project_master.integrations.comfyui.service import (
    ComfyServiceError,
    UnknownProfileError,
    UnknownWorkflowError,
)
from project_master.integrations.comfyui.workflow import WorkflowValidationError
from project_master.integrations.voice import (
    CHATTERBOX_PACK_TEMPLATE,
    QWEN3_TTS_PACK_TEMPLATE,
    ConsentRecord,
    ConsentScope,
    PronunciationEntry,
    RenderPurpose,
    RenderSettings,
    RightsBasis,
    ScriptBlock,
    SQLiteVoiceStore,
    VoiceProfile,
    VoiceProject,
    VoiceStudioService,
)
from project_master.integrations.voice.jobs import RenderJobNotFoundError
from project_master.integrations.voice.profiles import VoiceRightsError
from project_master.integrations.voice.service import VoiceStudioError
from project_master.knowledge import KnowledgeService
from project_master.llm.ollama import OllamaError
from project_master.orchestration.models import ProjectSpec, RunSpec
from project_master.orchestration.store import OrchestrationStore
from project_master.runtime import (
    MasterRuntime,
    build_runtime,
    resolve_scheduled_dream_sources,
)
from project_master.team.models import TeamRole

_LOGGER = logging.getLogger(__name__)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = None
    model: str | None = None
    mode: Literal["direct", "team"] = "direct"
    allow_mutations: bool = False
    project_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ChatCancelRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class CommunicationFeedbackRequest(BaseModel):
    category: Literal[
        "preserve_semantic_fidelity",
        "avoid_unjustified_assumptions",
        "avoid_unsolicited_advice",
        "avoid_unnecessary_repetition",
        "use_context_before_interpreting",
    ]
    note: str = Field(min_length=1, max_length=2_000)
    scope: Literal["global", "situational"] = "global"


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    root_path: str | None = Field(default=None, max_length=2_000)
    description: str = Field(default="", max_length=4_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectDreamingRequest(BaseModel):
    enabled: bool


class ApprovalResolutionRequest(BaseModel):
    status: Literal["approved", "rejected", "cancelled", "expired"]
    note: str = Field(default="", max_length=4_000)


class KnowledgeIndexRequest(BaseModel):
    relative_path: str = Field(default=".", min_length=1, max_length=2_000)
    prune: bool = True


class ComfyProfileRequest(BaseModel):
    id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(default="http://127.0.0.1:8188", max_length=2_000)
    trusted_hosts: tuple[str, ...] = ()
    verify_tls: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    auth: ComfyAuth | None = None


class ComfyWorkflowImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    workflow: dict[str, Any]
    bindings: tuple[WorkflowBinding, ...] = ()


class ComfyWorkflowDecisionRequest(BaseModel):
    trust_state: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=4_000)


class ComfyJobCreateRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=80)
    workflow_revision_id: str = Field(min_length=1, max_length=160)
    values: dict[str, Any] = Field(default_factory=dict)


class DreamRoleAngleRequest(BaseModel):
    role: TeamRole
    instruction: str = Field(min_length=1, max_length=2_000)


class DreamRecipeRequest(BaseModel):
    recipe_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=8_000)
    role_angles: tuple[DreamRoleAngleRequest, ...] = ()
    source_scopes: tuple[str, ...] = ()
    expected_version: int | None = Field(default=None, ge=0)


class DreamSourceRequest(BaseModel):
    source_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    kind: SourceKind = SourceKind.USER_NOTE
    locator: str = Field(min_length=1, max_length=2_000)
    content: str = Field(max_length=100_000)
    sensitivity: SourceSensitivity = SourceSensitivity.INTERNAL
    allow_dreaming: bool = True


class DreamManualRunRequest(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    sources: tuple[DreamSourceRequest, ...] = Field(min_length=1, max_length=64)
    preferred_lead: str | None = Field(default=None, max_length=500)


class DreamResourceRulesRequest(BaseModel):
    min_idle_seconds: float = Field(default=300.0, ge=0, le=604_800)
    max_cpu_percent: float = Field(default=60.0, ge=0, le=100)
    min_available_memory_bytes: int = Field(default=2 * 1024**3, ge=0)
    min_gpu_free_bytes: int | None = Field(default=None, ge=0)
    require_no_model_jobs: bool = True
    require_ac_power: bool = False


class DreamQuietWindowRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)
    start_local: time
    end_local: time
    weekdays: tuple[int, ...] = Field(
        default=tuple(range(7)),
        min_length=1,
        max_length=7,
    )


class DreamScheduleRequest(BaseModel):
    schedule_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    recipe_id: str = Field(min_length=1, max_length=128)
    timezone: str = Field(min_length=1, max_length=100)
    local_time: time
    enabled: bool = True
    catch_up: CatchUpMode = CatchUpMode.LATEST
    on_time_grace_seconds: int = Field(default=900, ge=0, le=86_400)
    max_lookback_days: int = Field(default=7, ge=1, le=365)
    max_catch_up_windows: int = Field(default=3, ge=1, le=100)
    resource_rules: DreamResourceRulesRequest = Field(
        default_factory=DreamResourceRulesRequest
    )
    quiet_window: DreamQuietWindowRequest | None = None
    expected_version: int | None = Field(default=None, ge=0)


class DreamScheduleEnabledRequest(BaseModel):
    enabled: bool


class DreamPromotionRequest(BaseModel):
    target: PromotionTarget
    rationale: str = Field(min_length=1, max_length=4_000)


class DreamRejectionRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=4_000)


class VoiceReferenceImportRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    audio_base64: str = Field(min_length=1, max_length=90_000_000)
    transcript: str | None = Field(default=None, max_length=10_000)


class VoiceDesignedProfileRequest(BaseModel):
    profile_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    description: str = Field(min_length=1, max_length=1_000)
    scopes: tuple[ConsentScope, ...] = (ConsentScope.VOICE_GENERATION,)
    attested_by_user: bool
    notes: str = Field(default="", max_length=500)


class VoiceReferenceProfileRequest(BaseModel):
    profile_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    description: str = Field(default="", max_length=1_000)
    reference_artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    rights_basis: Literal[
        "self_voice",
        "explicit_consent",
        "licensed_voice",
        "synthetic_reference",
    ]
    scopes: tuple[ConsentScope, ...] = (ConsentScope.VOICE_GENERATION,)
    subject_label: str = Field(min_length=1, max_length=120)
    attested_by_user: bool
    evidence_artifact_ids: tuple[str, ...] = ()
    notes: str = Field(default="", max_length=500)


class VoiceProjectRequest(BaseModel):
    project_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    language: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z0-9-]+$",
    )
    default_voice_profile_id: str = Field(min_length=1, max_length=100)
    blocks: tuple[ScriptBlock, ...] = Field(min_length=1, max_length=10_000)
    pronunciations: tuple[PronunciationEntry, ...] = Field(
        default=(),
        max_length=10_000,
    )


class VoiceRenderCreateRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    engine_pack_id: str = Field(min_length=1, max_length=120)
    purpose: RenderPurpose = RenderPurpose.PRIVATE
    settings: RenderSettings = Field(default_factory=RenderSettings)


def create_app(
    runtime: MasterRuntime | None = None,
    *,
    session_token: str | None = None,
) -> FastAPI:
    active = runtime or build_runtime()
    cancellations = StreamCancellationRegistry()
    required_token = session_token
    if required_token is None:
        required_token = os.getenv("MASTER_SESSION_TOKEN")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if active.comfy is not None:
            profiles = active.comfy.list_profiles()
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(
                        active.comfy.reconcile(profile.id),
                        timeout=5.0,
                    )
                    for profile in profiles
                ),
                return_exceptions=True,
            )
            for profile, result in zip(profiles, results, strict=True):
                if isinstance(result, BaseException):
                    _LOGGER.warning(
                        "ComfyUI startup reconciliation failed for profile %s (%s).",
                        profile.id,
                        type(result).__name__,
                    )
        active.start_background_services()
        try:
            yield
        finally:
            active.shutdown_background_services()
            try:
                active.shutdown_local_inference()
            except OllamaError as exc:
                _LOGGER.warning("Ollama shutdown cleanup failed: %s", exc)

    app = FastAPI(
        title="Project Master Local API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.runtime = active
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:1420", "tauri://localhost", "https://tauri.localhost"],
        allow_methods=["DELETE", "GET", "POST"],
        allow_headers=["Content-Type", "X-Project-Master-Token"],
    )

    @app.middleware("http")
    async def authenticate_loopback(request: Request, call_next: Any) -> Any:
        if request.method == "OPTIONS" or not required_token:
            return await call_next(request)
        supplied = request.headers.get("X-Project-Master-Token", "")
        if not hmac.compare_digest(supplied, required_token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid Project Master desktop session token."},
            )
        return await call_next(request)

    def conversation_id(request: ChatRequest) -> str:
        if request.conversation_id:
            if not active.store.session_exists(request.conversation_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
            return request.conversation_id
        return active.store.create_session(title=request.message[:80])

    def orchestration() -> OrchestrationStore:
        if active.orchestration is None:
            raise HTTPException(
                status_code=503,
                detail="Durable orchestration is not available in this runtime.",
            )
        return active.orchestration

    def comfy_services() -> tuple[ComfyUIService, SQLiteComfyStore]:
        if active.comfy is None or active.comfy_store is None:
            raise HTTPException(
                status_code=503,
                detail="ComfyUI support is not available in this runtime.",
            )
        return active.comfy, active.comfy_store

    def dream_service() -> DreamService:
        if active.dream is None:
            raise HTTPException(
                status_code=503,
                detail="Dream Lab is not available in this runtime.",
            )
        return active.dream

    def require_enabled_dream_schedule_ready(schedule: DreamSchedule) -> None:
        service = dream_service()
        recipe = service.store.get_recipe(schedule.recipe_id).recipe
        scopes = tuple(scope.strip() for scope in recipe.source_scopes if scope.strip())
        if not scopes:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Enabled Dream schedules require a recipe with explicit "
                    "source_scopes."
                ),
            )
        unsupported = unsupported_scheduled_source_scopes(scopes)
        if unsupported:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Enabled Dream schedules contain source_scopes that scheduled "
                    f"resolution does not support: {', '.join(unsupported)}."
                ),
            )
        sources = resolve_scheduled_dream_sources(
            schedule.recipe_id,
            dream_store=service.store,
            store=active.store,
            orchestration=orchestration(),
        )
        snapshot = service.snapshot_builder.build(
            sources,
            policy=SnapshotPolicy(),
            captured_at_utc=datetime.now(UTC),
        )
        if snapshot.entries:
            return
        raise HTTPException(
            status_code=409,
            detail=(
                "Enabled Dream schedule source_scopes currently resolve to no "
                "consented source eligible for a snapshot. Enable Dream consent "
                "and index or add the scoped source first."
            ),
        )

    def dream_executor(*, require_running: bool = False) -> DreamBackgroundExecutor:
        executor = active.dream_background
        if executor is None or (require_running and not executor.running):
            raise HTTPException(
                status_code=503,
                detail="Dream background execution is not running in this runtime.",
            )
        return executor

    def begin_foreground_model_use() -> None:
        if not active.begin_interactive_model_use(timeout_seconds=15.0):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Local models are still finishing the previous request "
                    "(a cancelled run can take a moment to wind down) — retry "
                    "in a moment."
                ),
            )

    def knowledge_service() -> KnowledgeService:
        if active.knowledge is None:
            raise HTTPException(
                status_code=503,
                detail="The local Project Binder index is not available in this runtime.",
            )
        return active.knowledge

    def knowledge_context(body: ChatRequest) -> str:
        if not body.project_id or active.knowledge is None:
            return ""
        try:
            hits = active.knowledge.search(
                body.message,
                project_id=body.project_id,
                limit=6,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not hits:
            return ""
        lines = [
            "Relevant excerpts from the selected local Project Binder:",
        ]
        remaining = 12_000
        for hit in hits:
            header = (
                f"\n[{hit.citation} | version={hit.document_version} | "
                f"sha256={hit.content_sha256}]\n"
            )
            excerpt = hit.content[: max(0, remaining - len(header))]
            if not excerpt:
                break
            lines.append(header + excerpt)
            remaining -= len(header) + len(excerpt)
            if remaining <= 0:
                break
        return "\n".join(lines)

    @contextmanager
    def project_tool_scope(project_id: str | None) -> Iterator[None]:
        if project_id is None:
            with nullcontext():
                yield
            return
        project = orchestration().get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raw_root = project.get("root_path")
        if not raw_root:
            raise HTTPException(
                status_code=422,
                detail="The selected project does not have a local root path.",
            )
        try:
            root = Path(str(raw_root)).expanduser().resolve(strict=True)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail="The selected project root is unavailable.",
            ) from exc
        if not root.is_dir():
            raise HTTPException(
                status_code=422,
                detail="The selected project root is not a directory.",
            )
        with active.agent.tools.workspace_scope(root):
            yield

    def start_direct_run(body: ChatRequest) -> str | None:
        if body.project_id is None:
            return None
        persistence = orchestration()
        if persistence.get_project(body.project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        run_id = persistence.create_run(
            RunSpec(
                project_id=body.project_id,
                kind="direct_chat",
                objective=body.message,
                mode="direct",
                metadata={
                    "chat_mode": "direct",
                    "allow_mutations": body.allow_mutations,
                    "tool_authorization": (
                        "explicit_mutations_allowed"
                        if body.allow_mutations
                        else "read_only"
                    ),
                },
            )
        )
        persistence.append_event(
            run_id,
            "tool_authorization",
            (
                "Explicit mutation authorization granted for this direct chat"
                if body.allow_mutations
                else "Direct chat restricted to read-only tools"
            ),
            {
                "chat_mode": "direct",
                "allow_mutations": body.allow_mutations,
                "tool_authorization": (
                    "explicit_mutations_allowed"
                    if body.allow_mutations
                    else "read_only"
                ),
            },
        )
        persistence.set_run_status(run_id, "running")
        return run_id

    def finish_direct_run(
        run_id: str | None,
        status: Literal["complete", "failed", "cancelled"],
        *,
        message: str = "",
    ) -> None:
        if run_id is None:
            return
        persistence = orchestration()
        if status == "complete":
            persistence.append_event(
                run_id,
                "delivery",
                "MASTER delivered the direct response",
                {},
            )
        persistence.set_run_status(run_id, status, message)

    def voice_services() -> tuple[VoiceStudioService, SQLiteVoiceStore]:
        if active.voice is None or active.voice_store is None:
            raise HTTPException(
                status_code=503,
                detail="Voice Studio is not available in this runtime.",
            )
        return active.voice, active.voice_store

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        try:
            ollama = active.provider.health()
        except OllamaError as exc:
            return {
                "ok": False,
                "service": "ready",
                "ollama": "unreachable",
                "error": str(exc),
                "version": __version__,
            }
        return {"ok": True, "service": "ready", "ollama": ollama, "version": __version__}

    @app.get("/api/v1/ready")
    def ready() -> dict[str, Any]:
        """Cheap process readiness/version probe; intentionally independent of Ollama."""
        return {
            "ok": True,
            "service": "ready",
            "version": __version__,
        }

    @app.get("/api/v1/models/status")
    def model_status() -> dict[str, Any]:
        try:
            ollama = active.provider.health()
            models = ollama["models"]
            reachable = True
        except OllamaError:
            models = []
            reachable = False
        payload: dict[str, Any] = {
            "configured_model": active.config.model,
            "num_ctx": active.config.num_ctx,
            "ollama_url": active.config.ollama_url,
            "ollama_reachable": reachable,
            "models": models,
        }
        if reachable and active.team is not None:
            try:
                payload["catalog"] = active.team.catalog_status()
            except OllamaError as exc:
                payload["catalog"] = []
                payload["catalog_error"] = str(exc)
        return payload

    @app.get("/api/v1/profile/communication")
    def communication_profile() -> dict[str, Any]:
        """Expose the local, auditable communication model for future interface controls."""

        return active.profiler.profile.to_dict()

    @app.post("/api/v1/profile/communication/feedback")
    def communication_feedback(body: CommunicationFeedbackRequest) -> dict[str, Any]:
        preference = active.profiler.record_feedback(body.category, body.note, body.scope)
        return {
            "preference": preference.to_dict(),
            "profile": active.profiler.profile.to_dict(),
        }

    @app.post("/api/v1/conversations", status_code=201)
    def create_conversation(body: ConversationCreate) -> dict[str, str]:
        return {"id": active.store.create_session(title=body.title)}

    @app.get("/api/v1/conversations")
    def list_conversations(limit: int = 50) -> dict[str, Any]:
        return {"conversations": active.store.list_sessions(limit=min(max(limit, 1), 200))}

    @app.get("/api/v1/conversations/{session_id}")
    def get_conversation(session_id: str) -> dict[str, Any]:
        if not active.store.session_exists(session_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {
            "id": session_id,
            "messages": active.store.recent_messages(session_id, limit=500),
        }

    @app.get("/api/v1/projects")
    def list_projects(include_archived: bool = False) -> dict[str, Any]:
        return {
            "projects": orchestration().list_projects(
                include_archived=include_archived,
            )
        }

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
        project_id = orchestration().create_project(
            ProjectSpec(
                name=body.name,
                root_path=body.root_path,
                description=body.description,
                metadata=body.metadata,
            )
        )
        project = orchestration().get_project(project_id)
        if project is None:
            raise HTTPException(status_code=500, detail="Project persistence failed.")
        return project

    @app.post("/api/v1/projects/{project_id}/dreaming")
    def set_project_dreaming(
        project_id: str,
        body: ProjectDreamingRequest,
    ) -> dict[str, Any]:
        try:
            return orchestration().set_project_dreaming(
                project_id,
                body.enabled,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        project = orchestration().get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            **project,
            "runs": orchestration().list_runs(project_id, limit=100),
            "artifacts": orchestration().list_artifacts(project_id),
        }

    @app.get("/api/v1/projects/{project_id}/runs")
    def list_project_runs(project_id: str, limit: int = 100) -> dict[str, Any]:
        if orchestration().get_project(project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            "runs": orchestration().list_runs(
                project_id,
                limit=min(max(limit, 1), 500),
            )
        }

    @app.get("/api/v1/projects/{project_id}/knowledge")
    def list_project_knowledge(
        project_id: str,
        include_history: bool = False,
    ) -> dict[str, Any]:
        try:
            documents = knowledge_service().list_documents(
                project_id,
                include_history=include_history,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"documents": [item.to_dict() for item in documents]}

    @app.post("/api/v1/projects/{project_id}/knowledge/index")
    def index_project_knowledge(
        project_id: str,
        body: KnowledgeIndexRequest,
    ) -> dict[str, Any]:
        try:
            result = knowledge_service().index_project(
                project_id,
                relative_path=body.relative_path,
                prune=body.prune,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result.to_dict()

    @app.get("/api/v1/projects/{project_id}/knowledge/search")
    def search_project_knowledge(
        project_id: str,
        query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        try:
            hits = knowledge_service().search(
                query,
                project_id=project_id,
                limit=min(max(limit, 1), 50),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"query": query, "results": [item.to_dict() for item in hits]}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = orchestration().get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        project_id = str(run["project_id"])
        return {
            "run": run,
            "roles": orchestration().list_roles(run_id),
            "tasks": orchestration().list_tasks(run_id),
            "events": orchestration().list_events(run_id),
            "artifacts": orchestration().list_artifacts(project_id, run_id),
            "approvals": orchestration().list_approvals(
                status=None,
                run_id=run_id,
            ),
        }

    @app.get("/api/v1/runs/{run_id}/events")
    def list_run_events(
        run_id: str,
        after_id: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        if orchestration().get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "events": orchestration().list_events(
                run_id,
                after_id=max(after_id, 0),
                limit=min(max(limit, 1), 2_000),
            )
        }

    @app.get("/api/v1/approvals")
    def list_approvals(
        status: Literal[
            "pending",
            "approved",
            "rejected",
            "cancelled",
            "expired",
            "all",
        ] = "pending",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        selected_status = None if status == "all" else status
        return {
            "approvals": orchestration().list_approvals(
                status=selected_status,
                run_id=run_id,
            )
        }

    @app.post("/api/v1/approvals/{approval_id}/resolve")
    def resolve_approval(
        approval_id: str,
        body: ApprovalResolutionRequest,
    ) -> dict[str, Any]:
        try:
            orchestration().resolve_approval(
                approval_id,
                body.status,
                body.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"id": approval_id, "status": body.status, "note": body.note}

    @app.get("/api/v1/jobs")
    def list_jobs(
        state: Literal[
            "pending",
            "waiting_approval",
            "queued",
            "running",
            "blocked",
            "complete",
            "failed",
            "cancelled",
            "interrupted",
            "unknown",
            "all",
        ] = "all",
        limit: int = 100,
    ) -> dict[str, Any]:
        selected_state = None if state == "all" else state
        return {
            "jobs": orchestration().list_jobs(
                state=selected_state,
                limit=min(max(limit, 1), 500),
            )
        }

    @app.get("/api/v1/tools/status")
    def tool_status() -> dict[str, Any]:
        inventory = active.agent.tools.inventory()
        diagnostics: dict[str, dict[str, Any]] = {}
        safe_smoke_inputs: dict[str, dict[str, Any]] = {
            "calculator": {"expression": "6 * 7"},
            "current_time": {},
            "workspace_list": {"path": ".", "limit": 1},
        }
        for name, arguments in safe_smoke_inputs.items():
            if name not in active.agent.tools.names():
                continue
            ok, result = active.agent.tools.execute(name, arguments)
            diagnostics[name] = {"ok": ok, "result": result}
        return {
            "workspace_root": str(active.config.workspace_root.resolve()),
            "workspace_writes_enabled": active.config.allow_file_writes,
            "default_chat_policy": "read_only",
            "mutating_tools_require_explicit_chat_authorization": True,
            "tools": [
                {
                    **item,
                    "enabled": (
                        active.config.allow_file_writes
                        if item["name"] == "workspace_write"
                        else (
                            active.config.terminal_enabled
                            if item["name"] == "terminal_run"
                            else True
                        )
                    ),
                }
                for item in inventory
            ],
            "diagnostics": diagnostics,
            "terminal": (
                {
                    "enabled": active.config.terminal_enabled,
                    "network_enabled": active.config.terminal_network_enabled,
                    "sandbox": active.terminal.sandbox_kind,
                }
                if active.terminal is not None
                else {
                    "enabled": False,
                    "network_enabled": False,
                    "sandbox": "unavailable",
                }
            ),
        }

    @app.get("/api/v1/integrations/comfyui")
    def comfy_overview() -> dict[str, Any]:
        service, persistence = comfy_services()
        return {
            "support_available": True,
            "profiles": [
                profile.model_dump(mode="json")
                for profile in service.list_profiles()
            ],
            "workflows": [
                item.model_dump(mode="json")
                for item in persistence.list_workflows()
            ],
            "jobs": [
                job.model_dump(mode="json")
                for job in persistence.list()
            ],
        }

    @app.post("/api/v1/integrations/comfyui/profiles")
    def save_comfy_profile(body: ComfyProfileRequest) -> dict[str, Any]:
        service, persistence = comfy_services()
        try:
            profile = ComfyUIProfile.model_validate(body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = persistence.upsert_profile(profile)
        service.upsert_profile(saved)
        return saved.model_dump(mode="json")

    @app.get("/api/v1/integrations/comfyui/profiles/{profile_id}/status")
    async def comfy_connection_status(profile_id: str) -> dict[str, Any]:
        service, _persistence = comfy_services()
        try:
            status = await service.connection_status(profile_id)
        except UnknownProfileError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            return {
                "profile_id": profile_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        return status.model_dump(mode="json")

    @app.post("/api/v1/integrations/comfyui/workflows", status_code=201)
    def import_comfy_workflow(
        body: ComfyWorkflowImportRequest,
    ) -> dict[str, Any]:
        service, persistence = comfy_services()
        try:
            revision = service.import_workflow(
                body.name,
                body.workflow,
                body.bindings,
            )
        except WorkflowValidationError as exc:
            raise HTTPException(status_code=422, detail=list(exc.issues)) from exc
        stored = persistence.save_workflow(revision)
        return stored.model_dump(mode="json")

    @app.get("/api/v1/integrations/comfyui/workflows")
    def list_comfy_workflows() -> dict[str, Any]:
        _service, persistence = comfy_services()
        return {
            "workflows": [
                item.model_dump(mode="json")
                for item in persistence.list_workflows()
            ]
        }

    @app.get("/api/v1/integrations/comfyui/workflows/{revision_id}")
    def get_comfy_workflow(revision_id: str) -> dict[str, Any]:
        _service, persistence = comfy_services()
        try:
            stored = persistence.get_workflow(revision_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return stored.model_dump(mode="json")

    @app.post("/api/v1/integrations/comfyui/workflows/{revision_id}/decision")
    def decide_comfy_workflow(
        revision_id: str,
        body: ComfyWorkflowDecisionRequest,
    ) -> dict[str, Any]:
        _service, persistence = comfy_services()
        try:
            stored = persistence.decide_workflow(
                revision_id,
                body.trust_state,
                body.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return stored.model_dump(mode="json")

    @app.get(
        "/api/v1/integrations/comfyui/workflows/{revision_id}/compatibility/{profile_id}"
    )
    async def validate_comfy_workflow(
        revision_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        service, _persistence = comfy_services()
        try:
            result = await service.validate_compatibility(
                profile_id,
                revision_id,
            )
        except (UnknownProfileError, UnknownWorkflowError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"ComfyUI validation failed ({type(exc).__name__}).",
            ) from exc
        return result.model_dump(mode="json")

    @app.post("/api/v1/integrations/comfyui/jobs", status_code=202)
    async def create_comfy_job(body: ComfyJobCreateRequest) -> dict[str, Any]:
        service, persistence = comfy_services()
        try:
            workflow = persistence.get_workflow(body.workflow_revision_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if workflow.trust_state != "approved":
            raise HTTPException(
                status_code=403,
                detail="Approve this immutable workflow revision before queueing it.",
            )
        try:
            job = await service.submit_workflow(
                body.profile_id,
                body.workflow_revision_id,
                body.values,
            )
        except UnknownProfileError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (WorkflowValidationError, ComfyJobConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ComfyServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.get("/api/v1/integrations/comfyui/jobs")
    def list_comfy_jobs(profile_id: str | None = None) -> dict[str, Any]:
        _service, persistence = comfy_services()
        return {
            "jobs": [
                job.model_dump(mode="json")
                for job in persistence.list(profile_id=profile_id)
            ]
        }

    @app.get("/api/v1/integrations/comfyui/jobs/{job_id}")
    def get_comfy_job(job_id: str) -> dict[str, Any]:
        service, _persistence = comfy_services()
        try:
            job = service.job_status(job_id)
        except ComfyJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.post("/api/v1/integrations/comfyui/jobs/{job_id}/refresh")
    async def refresh_comfy_job(job_id: str) -> dict[str, Any]:
        service, _persistence = comfy_services()
        try:
            job = await service.refresh_job(job_id)
        except ComfyJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"ComfyUI refresh failed ({type(exc).__name__}).",
            ) from exc
        return job.model_dump(mode="json")

    @app.post("/api/v1/integrations/comfyui/jobs/{job_id}/cancel")
    async def cancel_comfy_job(job_id: str) -> dict[str, Any]:
        service, _persistence = comfy_services()
        try:
            job = await service.cancel_job(job_id)
        except ComfyJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ComfyServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.get(
        "/api/v1/integrations/comfyui/jobs/{job_id}/artifacts/"
        "{artifact_id}/content"
    )
    def get_comfy_artifact_content(
        job_id: str,
        artifact_id: str,
    ) -> FileResponse:
        service, _persistence = comfy_services()
        try:
            artifacts = service.artifacts(job_id)
        except ComfyJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        artifact = next((item for item in artifacts if item.id == artifact_id), None)
        if artifact is None:
            raise HTTPException(status_code=404, detail="ComfyUI artifact not found.")
        try:
            path = service.artifact_path(job_id, artifact_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="ComfyUI artifact failed local checksum verification.",
            ) from exc
        except ComfyServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=artifact.media_type,
            filename=artifact.original_filename,
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/v1/dreams")
    def dream_overview() -> dict[str, Any]:
        service = dream_service()
        executor = active.dream_background
        return {
            "proposal_only": True,
            "scheduled_execution_enabled": bool(executor and executor.running),
            "background_configured": executor is not None,
            "active_run_ids": list(executor.active_run_ids()) if executor else [],
            "recipes": [item.to_dict() for item in service.list_recipes()],
            "schedules": [item.to_dict() for item in service.list_schedules()],
            "runs": [item.to_dict() for item in service.list_runs(100)],
            "inbox": [item.to_dict() for item in service.list_inbox()],
        }

    @app.post("/api/v1/dreams/recipes", status_code=201)
    def save_dream_recipe(body: DreamRecipeRequest) -> dict[str, Any]:
        service = dream_service()
        try:
            recipe = DreamRecipe(
                recipe_id=body.recipe_id,
                name=body.name,
                kind=DreamRecipeKind.CUSTOM,
                objective=body.objective,
                role_angles=tuple(
                    RoleAngle(role=item.role, instruction=item.instruction)
                    for item in body.role_angles
                ),
                source_scopes=body.source_scopes,
            )
            stored = service.save_recipe(
                recipe,
                expected_version=body.expected_version,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return stored.to_dict()

    @app.get("/api/v1/dreams/schedules")
    def list_dream_schedules(enabled_only: bool = False) -> dict[str, Any]:
        return {
            "schedules": [
                item.to_dict()
                for item in dream_service().list_schedules(
                    enabled_only=enabled_only,
                )
            ]
        }

    @app.post("/api/v1/dreams/schedules", status_code=201)
    def save_dream_schedule(body: DreamScheduleRequest) -> dict[str, Any]:
        service = dream_service()
        try:
            existing = service.get_schedule(body.schedule_id)
        except DreamInboxError:
            existing = None
        created_at = (
            existing.created_at_utc if existing is not None else datetime.now(UTC)
        )
        try:
            schedule = DreamSchedule(
                schedule_id=body.schedule_id,
                recipe_id=body.recipe_id,
                timezone=body.timezone,
                local_time=body.local_time,
                created_at_utc=created_at,
                enabled=body.enabled,
                catch_up=body.catch_up,
                on_time_grace=timedelta(seconds=body.on_time_grace_seconds),
                max_lookback_days=body.max_lookback_days,
                max_catch_up_windows=body.max_catch_up_windows,
            )
            quiet = (
                QuietWindow(
                    timezone=body.quiet_window.timezone,
                    start_local=body.quiet_window.start_local,
                    end_local=body.quiet_window.end_local,
                    weekdays=body.quiet_window.weekdays,
                )
                if body.quiet_window is not None
                else None
            )
            rules = ResourceRules(**body.resource_rules.model_dump())
            if schedule.enabled:
                require_enabled_dream_schedule_ready(schedule)
            stored = service.save_schedule(
                schedule,
                resource_rules=rules,
                quiet_window=quiet,
                expected_version=body.expected_version,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return stored.to_dict()

    @app.get("/api/v1/dreams/schedules/{schedule_id}")
    def get_dream_schedule(schedule_id: str) -> dict[str, Any]:
        try:
            stored = dream_service().get_schedule(schedule_id)
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return stored.to_dict()

    @app.post("/api/v1/dreams/schedules/{schedule_id}/enabled")
    def set_dream_schedule_enabled(
        schedule_id: str,
        body: DreamScheduleEnabledRequest,
    ) -> dict[str, Any]:
        try:
            service = dream_service()
            if body.enabled:
                current = service.get_schedule(schedule_id)
                require_enabled_dream_schedule_ready(current.schedule)
            stored = service.set_schedule_enabled(
                schedule_id,
                body.enabled,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return stored.to_dict()

    @app.delete("/api/v1/dreams/schedules/{schedule_id}", status_code=204)
    def delete_dream_schedule(schedule_id: str) -> Response:
        try:
            dream_service().delete_schedule(schedule_id)
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get("/api/v1/dreams/events")
    def list_dream_events(
        run_id: str | None = None,
        schedule_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return {
            "events": [
                event.to_dict()
                for event in dream_service().list_events(
                    run_id=run_id,
                    schedule_id=schedule_id,
                    limit=min(max(limit, 1), 1_000),
                )
            ]
        }

    @app.post("/api/v1/dreams/runs/manual", status_code=202)
    def run_manual_dream(body: DreamManualRunRequest) -> dict[str, Any]:
        dream_service()
        executor = dream_executor(require_running=True)
        captured_at = datetime.now(UTC)
        sources = tuple(
            DreamSource(
                source_id=item.source_id,
                kind=item.kind,
                locator=item.locator,
                content=item.content,
                captured_at_utc=captured_at,
                sensitivity=item.sensitivity,
                allow_dreaming=item.allow_dreaming,
            )
            for item in body.sources
        )
        try:
            execution = executor.submit_manual(
                recipe_id=body.recipe_id,
                request_id=body.request_id,
                sources=sources,
                preferred_lead=body.preferred_lead,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            **execution.to_dict(),
            "accepted": True,
            "background": True,
        }

    @app.get("/api/v1/dreams/runs")
    def list_dream_runs(limit: int = 100) -> dict[str, Any]:
        return {
            "runs": [
                item.to_dict()
                for item in dream_service().list_runs(
                    min(max(limit, 1), 500),
                )
            ]
        }

    @app.get("/api/v1/dreams/runs/{run_id}")
    def get_dream_run(run_id: str) -> dict[str, Any]:
        try:
            run = dream_service().get_run(run_id)
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.to_dict()

    @app.post("/api/v1/dreams/runs/{run_id}/cancel")
    def cancel_dream_run(run_id: str) -> dict[str, Any]:
        try:
            run = dream_service().cancel(run_id)
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.to_dict()

    @app.get("/api/v1/dreams/inbox")
    def list_dream_inbox(
        disposition: Literal["pending", "promoted", "rejected", "all"] = "all",
    ) -> dict[str, Any]:
        selected = (
            None
            if disposition == "all"
            else DreamDisposition(disposition)
        )
        return {
            "items": [
                item.to_dict()
                for item in dream_service().list_inbox(selected)
            ]
        }

    @app.get("/api/v1/dreams/inbox/{item_id}")
    def get_dream_inbox_item(item_id: str) -> dict[str, Any]:
        try:
            item = dream_service().get_inbox_item(item_id)
        except DreamInboxError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return item.to_dict()

    @app.post("/api/v1/dreams/inbox/{item_id}/promote")
    def promote_dream_item(
        item_id: str,
        body: DreamPromotionRequest,
    ) -> dict[str, Any]:
        try:
            item, handoff = dream_service().promote(
                item_id,
                target=body.target,
                decided_by="local-user",
                rationale=body.rationale,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "item": item.to_dict(),
            "candidate_handoff": handoff.to_dict(),
            "downstream_mutation_performed": False,
        }

    @app.post("/api/v1/dreams/inbox/{item_id}/reject")
    def reject_dream_item(
        item_id: str,
        body: DreamRejectionRequest,
    ) -> dict[str, Any]:
        try:
            item = dream_service().reject(
                item_id,
                decided_by="local-user",
                rationale=body.rationale,
            )
        except DreamInboxError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return item.to_dict()

    @app.get("/api/v1/voice")
    def voice_overview() -> dict[str, Any]:
        service, persistence = voice_services()
        installed = {
            pack.template_id for pack in service.list_packs()
        }
        templates = []
        for template in (QWEN3_TTS_PACK_TEMPLATE, CHATTERBOX_PACK_TEMPLATE):
            templates.append(
                {
                    **template.model_dump(mode="json"),
                    "installed": template.id in installed,
                }
            )
        return {
            "support_available": True,
            "profiles": [
                item.model_dump(mode="json") for item in service.list_profiles()
            ],
            "projects": [
                item.model_dump(mode="json") for item in service.list_projects()
            ],
            "installed_packs": [
                item.model_dump(mode="json") for item in service.list_packs()
            ],
            "optional_pack_templates": templates,
            "references": [
                item.model_dump(mode="json")
                for item in persistence.list_references()
            ],
            "jobs": [
                item.model_dump(mode="json") for item in persistence.list_jobs()
            ],
            "artifacts": [
                item.model_dump(mode="json")
                for item in persistence.list_artifacts()
            ],
        }

    @app.get("/api/v1/voice/engines/{pack_id}/health")
    async def voice_engine_health(pack_id: str) -> dict[str, Any]:
        service, _persistence = voice_services()
        try:
            health = await service.engine_health(pack_id)
        except VoiceStudioError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return health.model_dump(mode="json")

    @app.post("/api/v1/voice/references", status_code=201)
    def import_voice_reference(body: VoiceReferenceImportRequest) -> dict[str, Any]:
        _service, persistence = voice_services()
        try:
            audio = base64.b64decode(
                body.audio_base64,
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Voice reference audio_base64 is invalid.",
            ) from exc
        try:
            reference = persistence.import_reference_wav(
                audio,
                original_name=body.file_name,
                transcript=body.transcript,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return reference.model_dump(mode="json")

    @app.post("/api/v1/voice/profiles/designed", status_code=201)
    def save_designed_voice_profile(
        body: VoiceDesignedProfileRequest,
    ) -> dict[str, Any]:
        service, persistence = voice_services()
        try:
            existing = persistence.get_profile(body.profile_id)
            revision = existing.revision + 1
        except KeyError:
            revision = 1
        now = datetime.now(UTC)
        try:
            profile = VoiceProfile.create(
                profile_id=body.profile_id,
                name=body.name,
                mode="designed",
                language=body.language,
                description=body.description,
                consent=ConsentRecord(
                    id=f"consent-{body.profile_id}-{revision}",
                    basis=RightsBasis.SYNTHETIC_DESIGN,
                    scopes=body.scopes,
                    subject_label="Synthetic designed voice",
                    attested_by_user=body.attested_by_user,
                    granted_at=now,
                    notes=body.notes,
                ),
                revision=revision,
                created_at=now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = persistence.save_profile(profile)
        service.upsert_profile(saved)
        return saved.model_dump(mode="json")

    @app.post("/api/v1/voice/profiles/reference", status_code=201)
    def save_reference_voice_profile(
        body: VoiceReferenceProfileRequest,
    ) -> dict[str, Any]:
        service, persistence = voice_services()
        try:
            references = tuple(
                persistence.get_reference(artifact_id)
                for artifact_id in body.reference_artifact_ids
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            existing = persistence.get_profile(body.profile_id)
            revision = existing.revision + 1
        except KeyError:
            revision = 1
        now = datetime.now(UTC)
        try:
            profile = VoiceProfile.create(
                profile_id=body.profile_id,
                name=body.name,
                mode="reference",
                language=body.language,
                description=body.description,
                references=references,
                consent=ConsentRecord(
                    id=f"consent-{body.profile_id}-{revision}",
                    basis=RightsBasis(body.rights_basis),
                    scopes=body.scopes,
                    subject_label=body.subject_label,
                    attested_by_user=body.attested_by_user,
                    granted_at=now,
                    evidence_artifact_ids=body.evidence_artifact_ids,
                    notes=body.notes,
                ),
                revision=revision,
                created_at=now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = persistence.save_profile(profile)
        service.upsert_profile(saved)
        return saved.model_dump(mode="json")

    @app.post("/api/v1/voice/projects", status_code=201)
    def save_voice_project(body: VoiceProjectRequest) -> dict[str, Any]:
        service, persistence = voice_services()
        try:
            persistence.get_profile(body.default_voice_profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            existing = persistence.get_project(body.project_id)
            revision = existing.revision + 1
        except KeyError:
            revision = 1
        try:
            project = VoiceProject.create(
                project_id=body.project_id,
                name=body.name,
                language=body.language,
                default_voice_profile_id=body.default_voice_profile_id,
                blocks=body.blocks,
                pronunciations=body.pronunciations,
                revision=revision,
                created_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = persistence.save_project(project)
        service.upsert_project(saved)
        return saved.model_dump(mode="json")

    @app.post("/api/v1/voice/jobs", status_code=201)
    def create_voice_job(body: VoiceRenderCreateRequest) -> dict[str, Any]:
        service, _persistence = voice_services()
        try:
            job = service.create_render_job(
                project_id=body.project_id,
                engine_pack_id=body.engine_pack_id,
                purpose=body.purpose,
                settings=body.settings,
            )
        except VoiceRightsError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except VoiceStudioError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.get("/api/v1/voice/jobs")
    def list_voice_jobs() -> dict[str, Any]:
        _service, persistence = voice_services()
        return {
            "jobs": [
                job.model_dump(mode="json") for job in persistence.list_jobs()
            ]
        }

    @app.get("/api/v1/voice/jobs/{job_id}")
    def get_voice_job(job_id: str) -> dict[str, Any]:
        service, _persistence = voice_services()
        try:
            job = service.job_status(job_id)
        except RenderJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.post("/api/v1/voice/jobs/{job_id}/run")
    async def run_voice_job(job_id: str) -> dict[str, Any]:
        service, _persistence = voice_services()
        try:
            job = await service.run_job(job_id)
        except RenderJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except VoiceRightsError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except VoiceStudioError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.post("/api/v1/voice/jobs/{job_id}/cancel")
    async def cancel_voice_job(job_id: str) -> dict[str, Any]:
        service, _persistence = voice_services()
        try:
            job, acknowledgement = await service.cancel_job(job_id)
        except RenderJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "job": job.model_dump(mode="json"),
            "acknowledgement": (
                acknowledgement.model_dump(mode="json")
                if acknowledgement is not None
                else None
            ),
        }

    @app.get("/api/v1/voice/artifacts/{artifact_id}")
    def get_voice_artifact(artifact_id: str) -> dict[str, Any]:
        _service, persistence = voice_services()
        artifact = persistence.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Voice artifact not found")
        return artifact.model_dump(mode="json")

    @app.get("/api/v1/voice/artifacts/{artifact_id}/content")
    def get_voice_artifact_content(artifact_id: str) -> Response:
        _service, persistence = voice_services()
        artifact = persistence.get_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Voice artifact not found")
        try:
            content = persistence.read_artifact(artifact_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="Voice artifact failed local checksum verification.",
            ) from exc
        return Response(
            content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": (
                    f'inline; filename="{artifact.id}.{artifact.format}"'
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/v1/chat")
    def chat(body: ChatRequest) -> dict[str, Any]:
        session_id = conversation_id(body)
        context = knowledge_context(body)
        direct_run_id = start_direct_run(body) if body.mode == "direct" else None
        try:
            begin_foreground_model_use()
            with project_tool_scope(body.project_id):
                if body.mode == "team" and active.team is not None:
                    team_response = active.team.respond(
                        session_id,
                        body.message,
                        preferred_lead=body.model,
                        supplemental_context=context,
                        project_id=body.project_id,
                        allow_mutations=body.allow_mutations,
                    )
                    if not team_response.answer:
                        raise HTTPException(
                            status_code=503,
                            detail="The local model team could not produce a usable response.",
                        )
                    answer = team_response.answer
                    tools = team_response.tools
                    team_payload: dict[str, Any] | None = {
                        "run_id": team_response.run_id,
                        "council": team_response.council.to_dict(),
                        "activities": team_response.activities,
                    }
                else:
                    answer, tools = active.agent_for_model(body.model).respond(
                        session_id,
                        body.message,
                        supplemental_context=context,
                        allow_mutations=body.allow_mutations,
                    )
                    team_payload = None
            finish_direct_run(direct_run_id, "complete")
        except OllamaError as exc:
            finish_direct_run(direct_run_id, "failed", message=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            finish_direct_run(
                direct_run_id,
                "failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            active.end_interactive_model_use()
        return {
            "conversation_id": session_id,
            "message": answer,
            "tools": [asdict(item) for item in tools],
            "audit": [asdict(item) for item in audit_response(answer)],
            "team": team_payload,
            "run_id": team_payload["run_id"] if team_payload else direct_run_id,
            "tool_authorization": {
                "chat_mode": body.mode,
                "allow_mutations": body.allow_mutations,
                "policy": (
                    "explicit_mutations_allowed"
                    if body.allow_mutations
                    else "read_only"
                ),
            },
        }

    @app.post("/api/v1/chat/stream")
    def chat_stream(body: ChatRequest) -> StreamingResponse:
        request_id = body.request_id or str(uuid4())
        try:
            cancellation = cancellations.register(request_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            session_id = conversation_id(body)
            context = knowledge_context(body)
            direct_run_id = start_direct_run(body) if body.mode == "direct" else None
        except Exception:
            cancellations.finish(request_id, cancellation)
            raise

        def events() -> Iterator[str]:
            foreground_started = False
            try:
                if not active.begin_interactive_model_use(timeout_seconds=15.0):
                    finish_direct_run(
                        direct_run_id,
                        "failed",
                        message="Local inference resource remained busy.",
                    )
                    yield _event(
                        {
                            "type": "error",
                            "error": (
                                "Local models are still finishing the previous "
                                "request (a cancelled run can take a moment to "
                                "wind down) — retry in a moment."
                            ),
                            "retryable": True,
                        }
                    )
                    return
                foreground_started = True
                yield _event(
                    {
                        "type": "start",
                        "conversation_id": session_id,
                        "request_id": request_id,
                        "mode": body.mode,
                        "allow_mutations": body.allow_mutations,
                        "tool_authorization": (
                            "explicit_mutations_allowed"
                            if body.allow_mutations
                            else "read_only"
                        ),
                    }
                )
                with project_tool_scope(body.project_id):
                    if body.mode == "team" and active.team is not None:
                        stream = active.team.respond_stream(
                            session_id,
                            body.message,
                            preferred_lead=body.model,
                            cancellation=cancellation,
                            supplemental_context=context,
                            project_id=body.project_id,
                            allow_mutations=body.allow_mutations,
                        )
                    else:
                        stream = active.agent_for_model(body.model).respond_stream(
                            session_id,
                            body.message,
                            cancellation=cancellation,
                            supplemental_context=context,
                            allow_mutations=body.allow_mutations,
                        )
                    for event in stream:
                        if event["type"] == "done":
                            event["conversation_id"] = session_id
                            event["audit"] = [
                                asdict(item)
                                for item in audit_response(str(event["content"]))
                            ]
                            event["run_id"] = event.get("run_id") or direct_run_id
                            event["tool_authorization"] = {
                                "chat_mode": body.mode,
                                "allow_mutations": body.allow_mutations,
                                "policy": (
                                    "explicit_mutations_allowed"
                                    if body.allow_mutations
                                    else "read_only"
                                ),
                            }
                            finish_direct_run(direct_run_id, "complete")
                        elif event["type"] == "cancelled":
                            finish_direct_run(
                                direct_run_id,
                                "cancelled",
                                message="Direct chat cancelled",
                            )
                        yield _event(event)
            except OllamaError as exc:
                finish_direct_run(direct_run_id, "failed", message=str(exc))
                yield _event({"type": "error", "error": str(exc), "retryable": True})
            except Exception as exc:
                finish_direct_run(
                    direct_run_id,
                    "failed",
                    message=f"{type(exc).__name__}: {exc}",
                )
                yield _event(
                    {
                        "type": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                    }
                )
            finally:
                if foreground_started:
                    active.end_interactive_model_use()
                cancellations.finish(request_id, cancellation)

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/v1/chat/cancel")
    def cancel_chat(body: ChatCancelRequest) -> dict[str, bool]:
        active_stream = cancellations.cancel(body.request_id)
        return {"accepted": True, "active": active_stream}

    return app


def _event(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str) + "\n"


class _LazyASGIApp:
    """Preserve ``project_master.api:app`` without constructing two heavy runtimes.

    The packaged sidecar imports ``create_app`` and builds its owned instance directly. Generic
    ASGI servers may still target ``project_master.api:app``; that path constructs exactly once
    when the server sends its first lifespan or HTTP scope.
    """

    def __init__(self) -> None:
        self._app: FastAPI | None = None
        self._lock = threading.Lock()

    def _get(self) -> FastAPI:
        if self._app is not None:
            return self._app
        with self._lock:
            if self._app is None:
                self._app = create_app()
            return self._app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        await self._get()(scope, receive, send)


app = _LazyASGIApp()
