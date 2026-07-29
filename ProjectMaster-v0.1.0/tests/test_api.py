import base64
import json
import threading
import wave
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from project_master.agent import ProjectMasterAgent
from project_master.api import create_app
from project_master.config import MasterConfig
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.dreams import DreamService, DreamStore
from project_master.integrations.comfyui import (
    ComfyUIProfile,
    ComfyUIService,
    DownloadedOutput,
    FilesystemComfyArtifactStore,
    MissingWorkflowResource,
    OutputMetadata,
    OutputRef,
    QueueSnapshot,
    SQLiteComfyStore,
    WorkflowIncompatibleError,
    WorkflowPurpose,
)
from project_master.integrations.comfyui.defaults import load_bundled_workflows
from project_master.integrations.comfyui.jobs import (
    JobNotFoundError as ComfyJobNotFoundError,
)
from project_master.integrations.comfyui.transport import (
    HistoryResult,
    PromptSubmission,
)
from project_master.integrations.voice import (
    EspeakNgAdapter,
    GovernorVoiceLeaseProvider,
    SQLiteVoiceStore,
    VoiceResourceRequest,
    VoiceStudioService,
    discover_espeak_pack,
)
from project_master.knowledge import KnowledgeService, KnowledgeStore
from project_master.llm.ollama import OllamaError
from project_master.media import (
    FilesystemMediaArtifactStore,
    MediaLibraryService,
    MediaMetadata,
    SQLiteMediaCatalog,
)
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ApprovalSpec, ProjectSpec, RunSpec
from project_master.orchestration.resource import ResourceGovernor
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime
from project_master.team.models import CatalogModel, ModelDetails
from project_master.tools.base import Tool
from project_master.tools.builtin import build_registry
from project_master.tools.search import register_search_tools


class FakeProvider:
    model = "test-model"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "models": [self.model], "configured_model": self.model}

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        return Message(role="assistant", content="Test response")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        yield Message(role="assistant", content="Test ")
        yield Message(role="assistant", content="response")


class VisionCaptureProvider(FakeProvider):
    def __init__(self) -> None:
        self.seen_messages: list[Message] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        del tools
        self.seen_messages = list(messages)
        return Message(role="assistant", content="The attached image is visible.")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        del tools, cancellation
        self.seen_messages = list(messages)
        yield Message(role="assistant", content="The attached image is visible.")


class MultiModelProvider(FakeProvider):
    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "models": ["aaa-first", "test-model", "embedding"],
            "configured_model": self.model,
        }


class StaticModelCatalog:
    def __init__(self, models: tuple[CatalogModel, ...]) -> None:
        self.models = models

    def load(self, *, refresh: bool = False) -> tuple[CatalogModel, ...]:
        del refresh
        return self.models


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        assert cancellation is not None
        self.started.set()
        cancellation.wait(timeout=5)
        if cancellation.cancelled:
            self.stopped.set()
            return
        yield Message(role="assistant", content="Cancellation failed")


class OfflineProvider(FakeProvider):
    def health(self) -> dict[str, Any]:
        raise OllamaError("Ollama is offline")


class ShutdownTrackingProvider(FakeProvider):
    def __init__(self) -> None:
        self.unload_calls = 0

    def unload_active_model(self) -> str:
        self.unload_calls += 1
        return "test-model"


class WorkspaceToolProvider(FakeProvider):
    def __init__(self) -> None:
        self.round = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        self.round += 1
        if self.round == 1:
            return Message(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "function": {
                            "name": "workspace_read",
                            "arguments": {"path": "selected.txt"},
                        }
                    }
                ],
            )
        assert any("selected project content" in message.content for message in messages)
        return Message(role="assistant", content="Read the selected project.")


class ToolSchemaProvider(FakeProvider):
    def __init__(self) -> None:
        self.seen_tool_names: list[set[str]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        self.seen_tool_names.append(
            {
                str(schema["function"]["name"])
                for schema in tools or []
            }
        )
        return Message(role="assistant", content="Policy observed.")


class ToolScopeProvider(ToolSchemaProvider):
    def __init__(self) -> None:
        super().__init__()
        self.registry: Any | None = None
        self.seen_scopes: list[tuple[str | None, bool]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        assert self.registry is not None
        self.seen_scopes.append(
            (
                self.registry.project_id,
                self.registry.workspace_available,
            )
        )
        return super().chat(messages, tools)


class ArtifactComfyService:
    def __init__(self, path: Path, *, corrupt: bool = False) -> None:
        self.path = path
        self.corrupt = corrupt
        self.artifact = SimpleNamespace(
            id="comfy-artifact-" + "a" * 40,
            media_type="image/png",
            original_filename="render.png",
        )

    def list_profiles(self) -> tuple[Any, ...]:
        return ()

    def artifacts(self, job_id: str) -> tuple[Any, ...]:
        if job_id == "missing":
            raise ComfyJobNotFoundError(job_id)
        return (self.artifact,)

    def artifact_path(self, job_id: str, artifact_id: str) -> Path:
        del job_id, artifact_id
        if self.corrupt:
            raise ValueError("checksum mismatch")
        return self.path


class ReconcilingComfyService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_profiles(self) -> tuple[Any, ...]:
        return (SimpleNamespace(id="available"), SimpleNamespace(id="offline"))

    async def reconcile(self, profile_id: str) -> tuple[Any, ...]:
        self.calls.append(profile_id)
        if profile_id == "offline":
            raise OSError("offline")
        return ()


_GENERATED_PNG = b"\x89PNG\r\n\x1a\n" + b"project-master-generated-image"
_GENERATED_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


class ProjectScopedComfyTransport:
    def __init__(self) -> None:
        self.submitted_extra: Mapping[str, Any] | None = None
        self.submit_count = 0
        self.history_result = HistoryResult(found=False)
        self.object_types: dict[str, Any] = {"SaveImage": {}, "SaveVideo": {}}
        self.fail_object_info = False

    async def object_info(self) -> Mapping[str, Any]:
        if self.fail_object_info:
            raise OSError("simulated object_info outage")
        return self.object_types

    async def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        extra_data: Mapping[str, Any] | None = None,
    ) -> PromptSubmission:
        del workflow, client_id
        self.submit_count += 1
        self.submitted_extra = extra_data
        return PromptSubmission(prompt_id="creator-prompt-1", number=1)

    async def queue(self) -> QueueSnapshot:
        return QueueSnapshot()

    async def history(self, prompt_id: str) -> HistoryResult:
        assert prompt_id == "creator-prompt-1"
        return self.history_result

    async def download_output(self, output: OutputMetadata) -> DownloadedOutput:
        query = urlencode(
            {
                "filename": output.ref.filename,
                "subfolder": output.ref.subfolder,
                "type": output.ref.type,
            }
        )
        content = _GENERATED_MP4 if output.media_type == "video/mp4" else _GENERATED_PNG
        return DownloadedOutput(
            content=content,
            media_type=output.media_type or "image/png",
            source_url=f"http://127.0.0.1:8188/view?{query}",
            fetched_at=datetime.now(UTC),
        )


def make_runtime(tmp_path: Path, provider: FakeProvider | None = None) -> MasterRuntime:
    config = MasterConfig(
        model="test-model",
        db_path=tmp_path / "test.db",
        workspace_root=tmp_path / "workspace",
        num_ctx=32768,
    )
    store = SQLiteStore(config.db_path)
    profiler = StyleProfiler(store)
    provider = provider or FakeProvider()
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
        provider,
        agent,
        orchestration=OrchestrationStore(store),
        model_catalog=StaticModelCatalog(
            (
                CatalogModel(
                    physical_id="digest:test-model",
                    tags=("test-model",),
                    digest="test-model",
                    size_bytes=1,
                    capabilities=frozenset({"completion", "tools"}),
                    details=ModelDetails(family="test"),
                    automatic_eligible=True,
                    curated_purposes=frozenset({"chat"}),
                ),
            )
        ),
    )  # type: ignore[arg-type]


def make_comfy_media_runtime(
    tmp_path: Path,
    *,
    workflow_purpose: WorkflowPurpose = "image",
) -> tuple[MasterRuntime, str, str, ProjectScopedComfyTransport]:
    runtime = make_runtime(tmp_path)
    assert runtime.orchestration is not None
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Creator studio", project_type="creator")
    )
    media_catalog = SQLiteMediaCatalog(runtime.store)
    runtime.media_catalog = media_catalog
    runtime.media = MediaLibraryService(
        media_catalog,
        FilesystemMediaArtifactStore(tmp_path / "media-artifacts"),
        project_exists=lambda candidate: (
            runtime.orchestration is not None
            and runtime.orchestration.get_project(candidate) is not None
        ),
        metadata_probe=lambda _path: MediaMetadata(width=64, height=64),
    )

    persistence = SQLiteComfyStore(runtime.store)
    profile = persistence.upsert_profile(ComfyUIProfile(id="local", name="Local ComfyUI"))
    transport = ProjectScopedComfyTransport()
    service = ComfyUIService(
        [profile],
        lambda _profile: transport,  # type: ignore[arg-type]
        jobs=persistence,
        artifact_store=FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts"),
    )
    revision = service.import_workflow(
        f"Creator {workflow_purpose}",
        {
            "1": {
                "class_type": "SaveVideo" if workflow_purpose == "video" else "SaveImage",
                "inputs": {"media": "placeholder"},
            }
        },
        purpose=workflow_purpose,
    )
    persistence.save_workflow(revision)
    persistence.decide_workflow(
        revision.id,
        "approved",
        "Reviewed exact creator workflow.",
    )
    runtime.comfy_store = persistence
    runtime.comfy = service
    return runtime, project_id, revision.id, transport


def make_image_chat_runtime(
    tmp_path: Path,
) -> tuple[MasterRuntime, VisionCaptureProvider, str, Any, bytes]:
    provider = VisionCaptureProvider()
    runtime = make_runtime(tmp_path, provider)
    assert runtime.orchestration is not None
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Vision studio", project_type="creator")
    )
    media_catalog = SQLiteMediaCatalog(runtime.store)
    runtime.media_catalog = media_catalog
    runtime.media = MediaLibraryService(
        media_catalog,
        FilesystemMediaArtifactStore(tmp_path / "vision-media-artifacts"),
        project_exists=lambda candidate: (
            runtime.orchestration is not None
            and runtime.orchestration.get_project(candidate) is not None
        ),
        metadata_probe=lambda _path: MediaMetadata(width=64, height=64),
    )
    image_bytes = _GENERATED_PNG + b"-chat"
    staged = tmp_path / "chat-image.png"
    staged.write_bytes(image_bytes)
    asset = runtime.media.import_staged_file(
        project_id,
        staged,
        file_name="chat-image.png",
        declared_media_type="image/png",
    )
    runtime.model_catalog = StaticModelCatalog(  # type: ignore[assignment]
        (
            CatalogModel(
                physical_id="digest:vision",
                tags=("test-model",),
                digest="vision",
                size_bytes=1,
                capabilities=frozenset({"completion", "vision"}),
                details=ModelDetails(family="test"),
            ),
        )
    )
    return runtime, provider, project_id, asset, image_bytes


def test_health_and_model_status(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    assert client.get("/api/v1/health").json()["ok"] is True
    status = client.get("/api/v1/models/status").json()
    assert status["configured_model"] == "test-model"
    assert status["num_ctx"] == 32768
    assert status["models"] == ["test-model"]


def test_model_status_recommends_a_capable_installed_model_for_stale_config(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path, MultiModelProvider())
    runtime.config.model = "not-installed"
    runtime.model_catalog = StaticModelCatalog(  # type: ignore[assignment]
        (
            CatalogModel(
                physical_id="digest:first",
                tags=("aaa-first",),
                digest="first",
                size_bytes=1,
                capabilities=frozenset({"completion", "tools"}),
                details=ModelDetails(family="test"),
                automatic_eligible=True,
                curated_purposes=frozenset({"chat", "team"}),
            ),
            CatalogModel(
                physical_id="digest:recommended",
                tags=("test-model",),
                digest="recommended",
                size_bytes=10,
                capabilities=frozenset({"completion", "tools", "thinking"}),
                details=ModelDetails(family="test"),
                automatic_eligible=True,
                curated_purposes=frozenset({"chat", "team"}),
            ),
            CatalogModel(
                physical_id="digest:embedding",
                tags=("embedding",),
                digest="embedding",
                size_bytes=100,
                capabilities=frozenset({"embedding"}),
                details=ModelDetails(family="test"),
            ),
        )
    )

    status = TestClient(create_app(runtime)).get("/api/v1/models/status").json()

    assert status["configured_model"] == "not-installed"
    assert status["recommended_model"] == "test-model"
    assert [item["primary_tag"] for item in status["catalog"]] == [
        "aaa-first",
        "test-model",
        "embedding",
    ]


def test_readiness_is_authenticated_but_independent_of_ollama(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            make_runtime(tmp_path, OfflineProvider()),
            session_token="desktop-token",
        )
    )

    assert client.get("/api/v1/ready").status_code == 401
    ready = client.get(
        "/api/v1/ready",
        headers={"X-Project-Master-Token": "desktop-token"},
    )
    health = client.get(
        "/api/v1/health",
        headers={"X-Project-Master-Token": "desktop-token"},
    )

    assert ready.json()["ok"] is True
    assert ready.json()["service"] == "ready"
    assert health.json()["ok"] is False


def test_packaged_api_requires_its_per_launch_session_token(tmp_path: Path) -> None:
    client = TestClient(
        create_app(make_runtime(tmp_path), session_token="test-session-secret")
    )

    unauthorized = client.get("/api/v1/health")
    assert unauthorized.status_code == 401
    authorized = client.get(
        "/api/v1/health",
        headers={"X-Project-Master-Token": "test-session-secret"},
    )
    assert authorized.status_code == 200


def test_communication_profile_and_explicit_feedback_api(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    client = TestClient(create_app(runtime))

    profile = client.get("/api/v1/profile/communication")
    assert profile.status_code == 200
    assert any(item["key"] == "semantic_fidelity" for item in profile.json()["preferences"])

    response = client.post(
        "/api/v1/profile/communication/feedback",
        json={
            "category": "avoid_unsolicited_advice",
            "note": "Analyze the question before recommending a next step.",
            "scope": "global",
        },
    )

    assert response.status_code == 200
    assert response.json()["preference"]["source"] == "explicit_user_feedback"
    assert response.json()["preference"]["supporting_examples"] == [
        "Analyze the question before recommending a next step."
    ]
    reloaded = StyleProfiler(runtime.store)
    assert reloaded.profile.corrections[-1].preference_key == "avoid_unsolicited_advice"


def test_chat_stream_persists_conversation(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    response = client.post("/api/v1/chat/stream", json={"message": "Hello"})
    events = [line for line in response.iter_lines() if line]
    assert response.status_code == 200
    assert '"type": "start"' in events[0]
    assert any('"type": "token"' in line for line in events)
    assert '"type": "done"' in events[-1]

    start = json.loads(events[0])
    conversation_id = start["conversation_id"]
    conversation = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert [item["role"] for item in conversation["messages"]] == ["user", "assistant"]


def test_model_less_direct_chat_fails_closed_without_a_curated_chat_identity(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.model_catalog = StaticModelCatalog(  # type: ignore[assignment]
        (
            CatalogModel(
                physical_id="digest:team-only",
                tags=("test-model",),
                digest="team-only",
                size_bytes=1,
                capabilities=frozenset({"completion", "tools"}),
                details=ModelDetails(family="test"),
                automatic_eligible=True,
                curated_purposes=frozenset({"team"}),
            ),
        )
    )
    client = TestClient(create_app(runtime))

    automatic = client.post("/api/v1/chat", json={"message": "Use the default."})
    explicit = client.post(
        "/api/v1/chat",
        json={"message": "Use my manual model.", "model": "test-model"},
    )

    assert automatic.status_code == 503
    assert "curated automatic chat" in automatic.json()["detail"]
    assert explicit.status_code == 200


def test_conversation_and_non_streaming_chat_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))
    created = client.post("/api/v1/conversations", json={"title": "API test"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": conversation_id, "message": "Hello"},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Test response"
    assert response.json()["conversation_id"] == conversation_id

    listed = client.get("/api/v1/conversations").json()["conversations"]
    assert listed[0]["id"] == conversation_id
    assert listed[0]["message_count"] == 2


def test_project_image_chat_is_verified_transient_and_streamed_to_vision_model(
    tmp_path: Path,
) -> None:
    runtime, provider, project_id, asset, image_bytes = make_image_chat_runtime(tmp_path)
    client = TestClient(create_app(runtime))

    response = client.post(
        "/api/v1/chat/stream",
        json={
            "message": "Describe this project image.",
            "model": "test-model",
            "mode": "direct",
            "project_id": project_id,
            "image_asset_ids": [asset.id],
        },
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.iter_lines() if line]
    assert events[-1]["type"] == "done"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    image_messages = [
        message for message in provider.seen_messages if message.images
    ]
    assert len(image_messages) == 1
    assert image_messages[0].role == "user"
    assert image_messages[0].content == "Describe this project image."
    assert image_messages[0].images == (encoded,)

    conversation_id = events[0]["conversation_id"]
    persisted = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert persisted["messages"] == [
        {"role": "user", "content": "Describe this project image."},
        {"role": "assistant", "content": "The attached image is visible."},
    ]
    run = runtime.orchestration.get_run(events[-1]["run_id"])
    assert run is not None
    persisted_text = json.dumps({"conversation": persisted, "run": run})
    assert asset.id not in persisted_text
    assert encoded not in persisted_text


def test_project_image_chat_rejects_unscoped_nonvision_and_invalid_media(
    tmp_path: Path,
) -> None:
    runtime, _provider, project_id, asset, _image_bytes = make_image_chat_runtime(
        tmp_path
    )
    assert runtime.orchestration is not None
    assert runtime.media is not None
    client = TestClient(create_app(runtime))
    request = {
        "message": "Analyze this.",
        "model": "test-model",
        "mode": "direct",
        "project_id": project_id,
        "image_asset_ids": [asset.id],
    }

    team = client.post("/api/v1/chat", json={**request, "mode": "team"})
    missing_model = client.post(
        "/api/v1/chat",
        json={key: value for key, value in request.items() if key != "model"},
    )
    assert team.status_code == 422
    assert "Direct mode only" in team.json()["detail"]
    assert missing_model.status_code == 503
    assert "curated automatic vision" in missing_model.json()["detail"]

    general_project_id = runtime.orchestration.create_project(
        ProjectSpec(name="General", project_type="general")
    )
    general = client.post(
        "/api/v1/chat",
        json={**request, "project_id": general_project_id},
    )
    assert general.status_code == 422
    assert "Creator project" in general.json()["detail"]

    other_creator_id = runtime.orchestration.create_project(
        ProjectSpec(name="Other creator", project_type="creator")
    )
    cross_project = client.post(
        "/api/v1/chat",
        json={**request, "project_id": other_creator_id},
    )
    assert cross_project.status_code == 422
    assert "selected project" in cross_project.json()["detail"]

    video_path = tmp_path / "not-an-image.mp4"
    video_path.write_bytes(_GENERATED_MP4)
    video = runtime.media.import_staged_file(
        project_id,
        video_path,
        file_name="not-an-image.mp4",
        declared_media_type="video/mp4",
    )
    non_image = client.post(
        "/api/v1/chat",
        json={**request, "image_asset_ids": [video.id]},
    )
    assert non_image.status_code == 422
    assert "Only Creator Media image assets" in non_image.json()["detail"]

    with runtime.store.connection() as conn:
        conn.execute(
            "UPDATE media_assets SET width = NULL WHERE id = ?",
            (asset.id,),
        )
    missing_dimensions = client.post("/api/v1/chat", json=request)
    assert missing_dimensions.status_code == 422
    assert "verified dimensions" in missing_dimensions.json()["detail"]

    runtime.model_catalog = StaticModelCatalog(  # type: ignore[assignment]
        (
            CatalogModel(
                physical_id="digest:text-only",
                tags=("test-model",),
                digest="text-only",
                size_bytes=1,
                capabilities=frozenset({"completion"}),
                details=ModelDetails(family="test"),
            ),
        )
    )
    nonvision = client.post(
        "/api/v1/chat",
        json={**request, "image_asset_ids": [video.id]},
    )
    assert nonvision.status_code == 422
    assert "reported vision capability" in nonvision.json()["detail"]


def test_project_image_chat_enforces_count_size_total_and_integrity(
    tmp_path: Path,
) -> None:
    runtime, _provider, project_id, asset, _image_bytes = make_image_chat_runtime(
        tmp_path
    )
    assert runtime.media is not None
    client = TestClient(create_app(runtime))
    base_request = {
        "message": "Analyze these.",
        "model": "test-model",
        "mode": "direct",
        "project_id": project_id,
    }

    too_many = client.post(
        "/api/v1/chat",
        json={
            **base_request,
            "image_asset_ids": [
                f"media-asset-{index:032x}" for index in range(4)
            ],
        },
    )
    duplicate = client.post(
        "/api/v1/chat",
        json={**base_request, "image_asset_ids": [asset.id, asset.id]},
    )
    assert too_many.status_code == 422
    assert duplicate.status_code == 422
    assert "selected only once" in duplicate.json()["detail"]

    with runtime.store.connection() as conn:
        conn.execute(
            "UPDATE media_assets SET size_bytes = ? WHERE id = ?",
            (20 * 1024 * 1024 + 1, asset.id),
        )
    oversized = client.post(
        "/api/v1/chat",
        json={**base_request, "image_asset_ids": [asset.id]},
    )
    assert oversized.status_code == 422
    assert "20 MiB or smaller" in oversized.json()["detail"]

    total_assets = []
    for index in range(3):
        staged = tmp_path / f"total-{index}.png"
        staged.write_bytes(_GENERATED_PNG + bytes([index]))
        total_assets.append(
            runtime.media.import_staged_file(
                project_id,
                staged,
                file_name=f"total-{index}.png",
                declared_media_type="image/png",
            )
        )
    with runtime.store.connection() as conn:
        conn.executemany(
            "UPDATE media_assets SET size_bytes = ? WHERE id = ?",
            [(14 * 1024 * 1024, item.id) for item in total_assets],
        )
    over_total = client.post(
        "/api/v1/chat",
        json={
            **base_request,
            "image_asset_ids": [item.id for item in total_assets],
        },
    )
    assert over_total.status_code == 422
    assert "total 40 MiB or less" in over_total.json()["detail"]

    fresh_path = tmp_path / "corrupt-me.png"
    fresh_path.write_bytes(_GENERATED_PNG + b"-corrupt")
    fresh = runtime.media.import_staged_file(
        project_id,
        fresh_path,
        file_name="corrupt-me.png",
        declared_media_type="image/png",
    )
    _verified, stored_path = runtime.media.verified_content_path(fresh.id)
    stored_path.write_bytes(b"tampered")
    corrupt = client.post(
        "/api/v1/chat",
        json={**base_request, "image_asset_ids": [fresh.id]},
    )
    assert corrupt.status_code == 409
    assert "integrity verification" in corrupt.json()["detail"]


def test_chat_stream_can_be_cancelled_and_releases_provider(tmp_path: Path) -> None:
    provider = BlockingProvider()
    client = TestClient(create_app(make_runtime(tmp_path, provider)))
    result: dict[str, Any] = {}

    def run_stream() -> None:
        result["response"] = client.post(
            "/api/v1/chat/stream",
            json={"message": "Keep generating", "request_id": "cancel-test"},
        )

    worker = threading.Thread(target=run_stream, daemon=True)
    worker.start()
    assert provider.started.wait(timeout=2)

    cancelled = client.post("/api/v1/chat/cancel", json={"request_id": "cancel-test"})
    assert cancelled.status_code == 200
    assert cancelled.json() == {"accepted": True, "active": True}

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert provider.stopped.is_set()
    events = [json.loads(line) for line in result["response"].iter_lines() if line]
    assert events[-1]["type"] == "cancelled"


def test_cancel_before_stream_registration_is_not_lost(tmp_path: Path) -> None:
    provider = BlockingProvider()
    client = TestClient(create_app(make_runtime(tmp_path, provider)))

    cancelled = client.post("/api/v1/chat/cancel", json={"request_id": "early-cancel"})
    assert cancelled.json() == {"accepted": True, "active": False}
    response = client.post(
        "/api/v1/chat/stream",
        json={"message": "Do not start", "request_id": "early-cancel"},
    )

    assert provider.stopped.is_set()
    events = [json.loads(line) for line in response.iter_lines() if line]
    assert events[-1]["type"] == "cancelled"


def test_project_run_and_approval_control_plane(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    client = TestClient(create_app(runtime))

    created = client.post(
        "/api/v1/projects",
        json={
            "name": "Daily Driver",
            "description": "Mike's local project",
            "project_type": "creator",
            "metadata": {"local_only": True, "project_type": "general"},
        },
    )
    assert created.status_code == 201
    assert created.json()["project_type"] == "creator"
    assert created.json()["metadata"]["project_type"] == "creator"
    project_id = created.json()["id"]

    assert runtime.orchestration is not None
    run_id = runtime.orchestration.create_run(
        RunSpec(
            project_id=project_id,
            kind="feature",
            objective="Verify durable control-plane APIs",
        )
    )
    approval_id = runtime.orchestration.request_approval(
        ApprovalSpec(
            run_id=run_id,
            action_kind="workspace_write",
            target="notes.md",
            request={"path": "notes.md"},
            reversible=True,
        )
    )

    projects = client.get("/api/v1/projects").json()["projects"]
    assert projects[0]["name"] == "Daily Driver"
    assert client.get(
        "/api/v1/projects?project_type=creator"
    ).json()["projects"][0]["id"] == project_id
    assert client.get("/api/v1/projects?project_type=general").json()["projects"] == []
    run_payload = client.get(f"/api/v1/runs/{run_id}").json()
    assert run_payload["run"]["objective"] == "Verify durable control-plane APIs"
    assert run_payload["approvals"][0]["id"] == approval_id

    pending = client.get("/api/v1/approvals").json()["approvals"]
    assert [item["id"] for item in pending] == [approval_id]
    resolved = client.post(
        f"/api/v1/approvals/{approval_id}/resolve",
        json={"status": "approved", "note": "Approved in the local desktop."},
    )
    assert resolved.status_code == 200
    assert client.get("/api/v1/approvals").json()["approvals"] == []
    all_approvals = client.get("/api/v1/approvals?status=all").json()["approvals"]
    assert all_approvals[0]["status"] == "approved"


def test_project_binder_indexes_searches_and_supplies_chat_context(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    assert runtime.orchestration is not None
    root = tmp_path / "binder"
    root.mkdir()
    (root / "PROJECT.md").write_text(
        "The daily driver must use every installed Ollama model as an attributed team.",
        encoding="utf-8",
    )
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Project Master", root_path=str(root))
    )
    runtime.knowledge_store = KnowledgeStore(runtime.store)
    runtime.knowledge = KnowledgeService(
        runtime.knowledge_store,
        runtime.orchestration,
    )
    client = TestClient(create_app(runtime))

    indexed = client.post(
        f"/api/v1/projects/{project_id}/knowledge/index",
        json={"relative_path": ".", "prune": True},
    )
    searched = client.get(
        f"/api/v1/projects/{project_id}/knowledge/search",
        params={"query": "installed Ollama model"},
    )
    chatted = client.post(
        "/api/v1/chat",
        json={
            "message": "Which installed Ollama model policy did we choose?",
            "project_id": project_id,
        },
    )

    assert indexed.status_code == 200
    assert indexed.json()["indexed"] == 1
    assert searched.status_code == 200
    assert searched.json()["results"][0]["citation"].startswith("PROJECT.md:")
    assert chatted.status_code == 200
    listed = client.get(f"/api/v1/projects/{project_id}/knowledge").json()
    assert listed["documents"][0]["relative_path"] == "PROJECT.md"


def test_selected_project_scopes_tools_and_persists_direct_run(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, WorkspaceToolProvider())
    assert runtime.orchestration is not None
    project_root = tmp_path / "selected-project"
    project_root.mkdir()
    (project_root / "selected.txt").write_text(
        "selected project content",
        encoding="utf-8",
    )
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Selected", root_path=str(project_root))
    )
    client = TestClient(create_app(runtime))

    response = client.post(
        "/api/v1/chat",
        json={
            "message": "Read the selected file",
            "project_id": project_id,
            "mode": "direct",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Read the selected project."
    assert payload["tools"][0]["ok"] is True
    run_id = payload["run_id"]
    run = runtime.orchestration.get_run(run_id)
    assert run is not None
    assert run["project_id"] == project_id
    assert run["status"] == "complete"
    assert run["metadata"] == {
        "allow_mutations": False,
        "allow_web_search": False,
        "chat_mode": "direct",
        "online_search_authorization": "local_only",
        "tool_authorization": "read_only",
    }
    events = runtime.orchestration.list_events(run_id)
    authorization_event = next(
        event for event in events if event["event_type"] == "tool_authorization"
    )
    assert authorization_event["payload"]["allow_mutations"] is False


def test_rootless_creator_chat_hides_workspace_tools_and_cleans_request_scope(
    tmp_path: Path,
) -> None:
    provider = ToolScopeProvider()
    runtime = make_runtime(tmp_path, provider)
    provider.registry = runtime.agent.tools
    assert runtime.orchestration is not None
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Creator", project_type="creator")
    )
    client = TestClient(create_app(runtime))

    selected_response = client.post(
        "/api/v1/chat",
        json={
            "message": "Plan an image",
            "project_id": project_id,
            "mode": "direct",
        },
    )
    global_response = client.post(
        "/api/v1/chat",
        json={"message": "Inspect the global workspace", "mode": "direct"},
    )

    assert selected_response.status_code == 200
    assert global_response.status_code == 200
    assert provider.seen_scopes == [
        (project_id, False),
        (None, True),
    ]
    assert "calculator" in provider.seen_tool_names[0]
    assert "workspace_list" not in provider.seen_tool_names[0]
    assert "workspace_read" not in provider.seen_tool_names[0]
    assert "workspace_list" in provider.seen_tool_names[1]
    assert "workspace_read" in provider.seen_tool_names[1]
    assert runtime.agent.tools.project_id is None
    assert runtime.agent.tools.workspace_available is True


def test_chat_mutation_authorization_is_explicit_and_audited(tmp_path: Path) -> None:
    provider = ToolSchemaProvider()
    runtime = make_runtime(tmp_path, provider)
    runtime.agent.tools.register(
        Tool(
            name="network_probe",
            description="Test-only external network tool.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {"ok": True},
            external_network=True,
        )
    )
    assert runtime.orchestration is not None
    project_root = tmp_path / "authorized-project"
    project_root.mkdir()
    project_id = runtime.orchestration.create_project(
        ProjectSpec(name="Authorized", root_path=str(project_root))
    )
    client = TestClient(create_app(runtime))

    default_response = client.post(
        "/api/v1/chat",
        json={"message": "Inspect only", "project_id": project_id},
    )
    authorized_response = client.post(
        "/api/v1/chat",
        json={
            "message": "You may update this project",
            "project_id": project_id,
            "allow_mutations": True,
        },
    )
    online_response = client.post(
        "/api/v1/chat",
        json={
            "message": "You may search online",
            "project_id": project_id,
            "allow_web_search": True,
        },
    )

    assert default_response.status_code == 200
    assert authorized_response.status_code == 200
    assert online_response.status_code == 200
    assert "workspace_write" not in provider.seen_tool_names[0]
    assert "memory_remember" not in provider.seen_tool_names[0]
    assert "workspace_write" in provider.seen_tool_names[1]
    assert "memory_remember" in provider.seen_tool_names[1]
    assert "network_probe" not in provider.seen_tool_names[0]
    assert "network_probe" not in provider.seen_tool_names[1]
    assert "network_probe" in provider.seen_tool_names[2]
    assert default_response.json()["tool_authorization"]["policy"] == "read_only"
    assert (
        authorized_response.json()["tool_authorization"]["policy"]
        == "explicit_mutations_allowed"
    )
    assert (
        online_response.json()["tool_authorization"]["online_search_policy"]
        == "explicit_online_search_allowed"
    )
    run = runtime.orchestration.get_run(authorized_response.json()["run_id"])
    assert run is not None
    assert run["metadata"]["chat_mode"] == "direct"
    assert run["metadata"]["allow_mutations"] is True
    assert run["metadata"]["tool_authorization"] == "explicit_mutations_allowed"
    online_run = runtime.orchestration.get_run(online_response.json()["run_id"])
    assert online_run is not None
    assert online_run["metadata"]["allow_web_search"] is True
    assert (
        online_run["metadata"]["online_search_authorization"]
        == "explicit_online_search_allowed"
    )


def test_comfy_artifact_download_is_authenticated_and_checksum_guarded(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    artifact_path = tmp_path / "render.png"
    artifact_path.write_bytes(b"\x89PNG\r\n\x1a\nverified")
    runtime.comfy = ArtifactComfyService(artifact_path)  # type: ignore[assignment]
    runtime.comfy_store = object()  # type: ignore[assignment]
    client = TestClient(create_app(runtime, session_token="desktop-token"))
    route = (
        "/api/v1/integrations/comfyui/jobs/job-one/artifacts/"
        f"{runtime.comfy.artifact.id}/content"
    )

    assert client.get(route).status_code == 401
    response = client.get(
        route,
        headers={"X-Project-Master-Token": "desktop-token"},
    )

    assert response.status_code == 200
    assert response.content == artifact_path.read_bytes()
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    missing = client.get(
        "/api/v1/integrations/comfyui/jobs/job-one/artifacts/"
        f"{'comfy-artifact-' + 'b' * 40}/content",
        headers={"X-Project-Master-Token": "desktop-token"},
    )
    assert missing.status_code == 404

    runtime.comfy.corrupt = True
    corrupt = client.get(
        route,
        headers={"X-Project-Master-Token": "desktop-token"},
    )
    assert corrupt.status_code == 409


def test_lifespan_best_effort_reconciles_every_comfy_profile(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    service = ReconcilingComfyService()
    runtime.comfy = service  # type: ignore[assignment]
    runtime.comfy_store = object()  # type: ignore[assignment]

    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/ready").status_code == 200

    assert service.calls == ["available", "offline"]


def test_lifespan_unloads_runtime_ollama_model_on_shutdown(tmp_path: Path) -> None:
    provider = ShutdownTrackingProvider()
    runtime = make_runtime(tmp_path, provider)

    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/ready").status_code == 200

    assert provider.unload_calls == 1


def test_tool_status_runs_only_safe_diagnostics(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    client = TestClient(create_app(runtime))

    response = client.get("/api/v1/tools/status")

    assert response.status_code == 200
    payload = response.json()
    assert {item["name"] for item in payload["tools"]} >= {
        "calculator",
        "current_time",
        "workspace_list",
        "workspace_read",
        "workspace_write",
    }
    assert payload["workspace_writes_enabled"] is False
    assert payload["default_chat_policy"] == "read_only"
    assert payload["mutating_tools_require_explicit_chat_authorization"] is True
    tools = {item["name"]: item for item in payload["tools"]}
    assert tools["workspace_read"]["risk"] == "read_only"
    assert tools["workspace_read"]["mutating"] is False
    assert tools["workspace_write"]["risk"] == "mutating"
    assert tools["workspace_write"]["mutating"] is True
    assert tools["workspace_write"]["available_in_default_chat"] is False
    assert payload["diagnostics"]["calculator"]["ok"] is True
    assert payload["diagnostics"]["workspace_list"]["ok"] is True
    assert "memory_remember" not in payload["diagnostics"]


def test_tool_status_reports_web_fetch_and_search_configuration_truthfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)
    register_search_tools(
        runtime.agent.tools,
        runtime.store,
        runtime.config.workspace_root,
    )
    client = TestClient(create_app(runtime))
    monkeypatch.delenv("MASTER_SEARXNG_URL", raising=False)

    tools = {
        item["name"]: item
        for item in client.get("/api/v1/tools/status").json()["tools"]
    }

    assert tools["web_fetch"]["enabled"] is True
    assert tools["web_search"]["enabled"] is False

    monkeypatch.setenv("MASTER_SEARXNG_URL", "https://search.example.test")
    configured = {
        item["name"]: item
        for item in client.get("/api/v1/tools/status").json()["tools"]
    }
    assert configured["web_search"]["enabled"] is True


def test_comfyui_workflow_import_requires_explicit_approval(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    persistence = SQLiteComfyStore(runtime.store)
    profile = persistence.upsert_profile(
        ComfyUIProfile(id="local", name="Local ComfyUI")
    )
    runtime.comfy_store = persistence
    runtime.comfy = ComfyUIService(
        [profile],
        lambda _profile: object(),  # type: ignore[arg-type, return-value]
        jobs=persistence,
    )
    client = TestClient(create_app(runtime))

    imported = client.post(
        "/api/v1/integrations/comfyui/workflows",
        json={
            "name": "Daily video",
            "purpose": "video",
            "workflow": {
                "1": {
                    "class_type": "PreviewImage",
                    "inputs": {"images": "placeholder"},
                }
            },
        },
    )

    assert imported.status_code == 201
    revision_id = imported.json()["revision"]["id"]
    assert imported.json()["revision"]["schema_version"] == 2
    assert imported.json()["revision"]["purpose"] == "video"
    assert imported.json()["trust_state"] == "pending"
    assert imported.json()["curated_default"] is False
    blocked = client.post(
        "/api/v1/integrations/comfyui/jobs",
        json={
            "profile_id": "local",
            "workflow_revision_id": revision_id,
            "values": {},
        },
    )
    assert blocked.status_code == 403

    approved = client.post(
        f"/api/v1/integrations/comfyui/workflows/{revision_id}/decision",
        json={"trust_state": "approved", "note": "Reviewed the exact graph."},
    )
    assert approved.status_code == 200
    assert approved.json()["trust_state"] == "approved"
    assert approved.json()["curated_default"] is False
    overview = client.get("/api/v1/integrations/comfyui").json()
    assert overview["support_available"] is True
    assert overview["profiles"][0]["auth"] is None
    assert overview["workflows"][0]["revision"]["id"] == revision_id
    assert overview["workflows"][0]["revision"]["purpose"] == "video"
    assert overview["workflows"][0]["curated_default"] is False

    fetched = client.get(f"/api/v1/integrations/comfyui/workflows/{revision_id}")
    assert fetched.status_code == 200
    assert fetched.json()["revision"]["purpose"] == "video"
    assert fetched.json()["curated_default"] is False

    recategorized = client.post(
        "/api/v1/integrations/comfyui/workflows",
        json={
            "name": "Daily image",
            "purpose": "image",
            "workflow": imported.json()["revision"]["workflow"],
        },
    )
    assert recategorized.status_code == 201
    assert recategorized.json()["revision"]["id"] != revision_id
    assert recategorized.json()["trust_state"] == "pending"
    assert recategorized.json()["curated_default"] is False


def test_comfy_workflow_api_derives_curated_defaults_from_bundled_ids(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    persistence = SQLiteComfyStore(runtime.store)
    runtime.comfy_store = persistence
    runtime.comfy = ComfyUIService([], lambda _profile: object())  # type: ignore[arg-type]
    curated_revision = load_bundled_workflows()[0]
    persistence.save_workflow(curated_revision)
    persistence.decide_workflow(
        curated_revision.id,
        "approved",
        "Bundled default decision.",
    )
    client = TestClient(create_app(runtime))

    imported = client.post(
        "/api/v1/integrations/comfyui/workflows",
        json={
            "name": "Manual image workflow",
            "purpose": "image",
            "workflow": {
                "1": {
                    "class_type": "PreviewImage",
                    "inputs": {"images": "placeholder"},
                }
            },
        },
    )
    manual_id = imported.json()["revision"]["id"]

    overview = client.get("/api/v1/integrations/comfyui").json()
    listed = client.get("/api/v1/integrations/comfyui/workflows").json()
    fetched = client.get(
        f"/api/v1/integrations/comfyui/workflows/{curated_revision.id}"
    )
    decided = client.post(
        f"/api/v1/integrations/comfyui/workflows/{curated_revision.id}/decision",
        json={"trust_state": "approved", "note": "Keep curated default."},
    )

    expected = {
        curated_revision.id: True,
        manual_id: False,
    }
    assert {
        item["revision"]["id"]: item["curated_default"]
        for item in overview["workflows"]
    } == expected
    assert {
        item["revision"]["id"]: item["curated_default"]
        for item in listed["workflows"]
    } == expected
    assert fetched.json()["curated_default"] is True
    assert decided.json()["curated_default"] is True


def test_comfyui_workflow_import_rejects_invalid_purpose(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    persistence = SQLiteComfyStore(runtime.store)
    runtime.comfy_store = persistence
    runtime.comfy = ComfyUIService([], lambda _profile: object())  # type: ignore[arg-type]
    client = TestClient(create_app(runtime))

    imported = client.post(
        "/api/v1/integrations/comfyui/workflows",
        json={
            "name": "Invalid category",
            "purpose": "motion-picture",
            "workflow": {
                "1": {
                    "class_type": "SaveVideo",
                    "inputs": {"video": "placeholder"},
                }
            },
        },
    )

    assert imported.status_code == 422
    assert persistence.list_workflows() == ()


def test_comfy_job_api_propagates_creator_project_to_job_and_remote_metadata(
    tmp_path: Path,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)

    with TestClient(create_app(runtime)) as client:
        created = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )

    assert created.status_code == 202
    assert created.json()["project_id"] == project_id
    assert transport.submitted_extra is not None
    assert transport.submitted_extra["project_master"]["project_id"] == project_id
    assert runtime.comfy_store is not None
    assert runtime.comfy_store.get(created.json()["id"]).project_id == project_id


def test_comfy_job_api_rejects_unknown_project_before_remote_submission(
    tmp_path: Path,
) -> None:
    runtime, _project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": "project-missing",
                "values": {},
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
    assert transport.submit_count == 0
    assert runtime.comfy_store is not None
    assert runtime.comfy_store.list() == ()


def test_comfy_job_api_rejects_missing_nodes_before_job_creation(
    tmp_path: Path,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)
    transport.object_types.pop("SaveImage")

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["missing_node_types"] == ["SaveImage"]
    assert "missing node types" in response.json()["detail"]["message"]
    assert transport.submit_count == 0
    assert runtime.comfy_store is not None
    assert runtime.comfy_store.list() == ()


def test_comfy_job_api_preserves_missing_resource_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)
    assert runtime.comfy is not None
    missing = MissingWorkflowResource(
        node_id="12",
        class_type="UNETLoader",
        input_name="unet_name",
        resource_name="wan2.2_i2v_high_noise.gguf",
    )

    async def reject_missing_resource(*_args: Any, **_kwargs: Any) -> None:
        raise WorkflowIncompatibleError(
            "local",
            revision_id,
            (),
            (missing,),
        )

    monkeypatch.setattr(runtime.comfy, "submit_workflow", reject_missing_resource)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["missing_resources"] == [
        {
            "node_id": "12",
            "class_type": "UNETLoader",
            "input_name": "unet_name",
            "resource_name": "wan2.2_i2v_high_noise.gguf",
        }
    ]
    assert transport.submit_count == 0


def test_comfy_job_api_reports_preflight_outage_without_creating_job(
    tmp_path: Path,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)
    transport.fail_object_info = True

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )

    assert response.status_code == 503
    assert "no job was created or submitted" in response.json()["detail"]
    assert transport.submit_count == 0
    assert runtime.comfy_store is not None
    assert runtime.comfy_store.list() == ()


def test_successful_project_comfy_job_is_cataloged_once_as_generated_media(
    tmp_path: Path,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(tmp_path)
    output = OutputMetadata(
        node_id="1",
        category="images",
        ref=OutputRef(filename="generated.png"),
        media_type="image/png",
        width=64,
        height=64,
    )

    with TestClient(create_app(runtime)) as client:
        created = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )
        assert created.status_code == 202
        transport.history_result = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(output,),
        )

        refreshed = client.post(f"/api/v1/integrations/comfyui/jobs/{created.json()['id']}/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["artifact_status"] == "ready"

        first_listing = client.get(f"/api/v1/projects/{project_id}/media").json()["assets"]
        repeated = client.post(f"/api/v1/integrations/comfyui/jobs/{created.json()['id']}/refresh")
        second_listing = client.get(f"/api/v1/projects/{project_id}/media").json()["assets"]

        assert repeated.status_code == 200
        assert len(first_listing) == 1
        assert second_listing == first_listing
        asset = first_listing[0]
        assert asset["project_ids"] == [project_id]
        assert asset["name"] == "generated.png"
        assert asset["kind"] == "image"
        assert asset["source"] == "comfyui"
        assert asset["width"] == 64
        assert asset["height"] == 64
        content = client.get(f"/api/v1/media/assets/{asset['id']}/content")
        assert content.status_code == 200
        assert content.content == _GENERATED_PNG


def test_approved_video_workflow_reaches_verified_project_media_once(
    tmp_path: Path,
) -> None:
    runtime, project_id, revision_id, transport = make_comfy_media_runtime(
        tmp_path,
        workflow_purpose="video",
    )
    output = OutputMetadata(
        node_id="1",
        category="videos",
        ref=OutputRef(filename="generated.mp4"),
        media_type="video/mp4",
        width=1280,
        height=720,
        duration_seconds=2.0,
    )

    with TestClient(create_app(runtime)) as client:
        created = client.post(
            "/api/v1/integrations/comfyui/jobs",
            json={
                "profile_id": "local",
                "workflow_revision_id": revision_id,
                "project_id": project_id,
                "values": {},
            },
        )
        assert created.status_code == 202
        transport.history_result = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(output,),
        )

        refreshed = client.post(
            f"/api/v1/integrations/comfyui/jobs/{created.json()['id']}/refresh"
        )
        repeated = client.post(
            f"/api/v1/integrations/comfyui/jobs/{created.json()['id']}/refresh"
        )
        assets = client.get(f"/api/v1/projects/{project_id}/media").json()["assets"]

        assert refreshed.status_code == 200
        assert refreshed.json()["artifact_status"] == "ready"
        assert repeated.status_code == 200
        assert len(assets) == 1
        assert assets[0]["kind"] == "video"
        assert assets[0]["source"] == "comfyui"
        content = client.get(f"/api/v1/media/assets/{assets[0]['id']}/content")
        assert content.status_code == 200
        assert content.content == _GENERATED_MP4


def test_dream_lab_exposes_proposal_only_builtin_and_custom_recipes(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    dream_store = DreamStore(runtime.store)
    runtime.dream_store = dream_store
    runtime.dream = DreamService(
        dream_store,
        object(),  # type: ignore[arg-type]
    )
    client = TestClient(create_app(runtime))

    overview = client.get("/api/v1/dreams")
    assert overview.status_code == 200
    assert overview.json()["proposal_only"] is True
    assert overview.json()["scheduled_execution_enabled"] is False
    assert {item["recipe_id"] for item in overview.json()["recipes"]} >= {
        "idea-garden",
        "memory-gardener",
        "risk-scan",
    }

    created = client.post(
        "/api/v1/dreams/recipes",
        json={
            "recipe_id": "release-reflection",
            "name": "Release Reflection",
            "objective": "Propose release risks for explicit review.",
            "expected_version": 0,
        },
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "custom"
    assert created.json()["version"] == 1
    assert client.get("/api/v1/dreams/inbox").json()["items"] == []


@pytest.mark.skipif(
    discover_espeak_pack() is None,
    reason="The system eSpeak NG plus ffmpeg fallback is not installed.",
)
def test_voice_api_builds_and_renders_a_local_designed_voice(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    storage = SQLiteVoiceStore(runtime.store, tmp_path / "voice-artifacts")
    adapter = EspeakNgAdapter()
    pack = discover_espeak_pack(adapter)
    assert pack is not None
    storage.upsert_pack(pack)
    runtime.voice_store = storage
    runtime.voice = VoiceStudioService(
        profiles=(),
        projects=(),
        packs=(pack,),
        adapters={"espeak-ng": adapter},
        resource_leases=GovernorVoiceLeaseProvider(
            ResourceGovernor(runtime.store)
        ),
        resource_requests={
            pack.id: VoiceResourceRequest(
                kind="cpu",
                minimum_memory_mb=64,
                exclusive=False,
            )
        },
        jobs=storage.jobs,
        cache=storage.cache,
        artifacts=storage.artifacts,
    )
    client = TestClient(create_app(runtime))

    profile = client.post(
        "/api/v1/voice/profiles/designed",
        json={
            "profile_id": "daily-narrator",
            "name": "Daily narrator",
            "language": "en-US",
            "description": "voice=en-us+f3; pitch=55",
            "attested_by_user": True,
        },
    )
    assert profile.status_code == 201
    project = client.post(
        "/api/v1/voice/projects",
        json={
            "project_id": "daily-brief",
            "name": "Daily brief",
            "language": "en-US",
            "default_voice_profile_id": "daily-narrator",
            "blocks": [
                {
                    "id": "intro",
                    "text": "The Project Master voice API is working.",
                }
            ],
        },
    )
    assert project.status_code == 201
    created = client.post(
        "/api/v1/voice/jobs",
        json={
            "project_id": "daily-brief",
            "engine_pack_id": pack.id,
            "purpose": "private",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    rendered = client.post(f"/api/v1/voice/jobs/{job_id}/run")

    assert rendered.status_code == 200
    assert rendered.json()["status"] == "succeeded"
    artifact_id = rendered.json()["chunks"][0]["artifact_id"]
    content = client.get(f"/api/v1/voice/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "audio/wav"
    assert content.content.startswith(b"RIFF")


def test_voice_api_persists_truthful_synthetic_reference_basis(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    storage = SQLiteVoiceStore(runtime.store, tmp_path / "voice-artifacts")
    runtime.voice_store = storage
    runtime.voice = VoiceStudioService(
        profiles=(),
        projects=(),
        packs=(),
        adapters={},
        resource_leases=GovernorVoiceLeaseProvider(
            ResourceGovernor(runtime.store)
        ),
        jobs=storage.jobs,
        cache=storage.cache,
        artifacts=storage.artifacts,
    )
    client = TestClient(create_app(runtime))
    wav = BytesIO()
    with wave.open(wav, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(b"\0\0" * 2_400)
    imported = client.post(
        "/api/v1/voice/references",
        json={
            "file_name": "generated.wav",
            "audio_base64": base64.b64encode(wav.getvalue()).decode("ascii"),
            "transcript": "Generated locally; no real person is represented.",
        },
    )
    assert imported.status_code == 201
    reference_id = imported.json()["artifact_id"]

    profile = client.post(
        "/api/v1/voice/profiles/reference",
        json={
            "profile_id": "synthetic-reference",
            "name": "Synthetic reference",
            "language": "en-US",
            "description": "Generated audio reference.",
            "reference_artifact_ids": [reference_id],
            "rights_basis": "synthetic_reference",
            "subject_label": "Synthetic generated voice",
            "attested_by_user": True,
            "evidence_artifact_ids": [],
        },
    )

    assert profile.status_code == 201
    assert profile.json()["consent"]["basis"] == "synthetic_reference"
    assert (
        storage.get_profile("synthetic-reference").consent.basis.value
        == "synthetic_reference"
    )

    unsupported_license = client.post(
        "/api/v1/voice/profiles/reference",
        json={
            "profile_id": "license-without-evidence",
            "name": "Unproven license",
            "language": "en-US",
            "reference_artifact_ids": [reference_id],
            "rights_basis": "licensed_voice",
            "subject_label": "Licensed narrator",
            "attested_by_user": True,
            "evidence_artifact_ids": [],
        },
    )
    assert unsupported_license.status_code == 422
    assert "evidence artifact" in unsupported_license.json()["detail"]


def _speech_runtime(tmp_path: Path):
    """A runtime with only the CPU eSpeak engine installed."""
    runtime = make_runtime(tmp_path)
    storage = SQLiteVoiceStore(runtime.store, tmp_path / "voice-artifacts")
    adapter = EspeakNgAdapter()
    pack = discover_espeak_pack(adapter)
    assert pack is not None
    storage.upsert_pack(pack)
    runtime.voice_store = storage
    runtime.voice = VoiceStudioService(
        profiles=(),
        projects=(),
        packs=(pack,),
        adapters={"espeak-ng": adapter},
        resource_leases=GovernorVoiceLeaseProvider(ResourceGovernor(runtime.store)),
        resource_requests={
            pack.id: VoiceResourceRequest(
                kind="cpu", minimum_memory_mb=64, exclusive=False
            )
        },
        jobs=storage.jobs,
        cache=storage.cache,
        artifacts=storage.artifacts,
    )
    return runtime, pack


def test_speak_endpoint_renders_one_message_and_picks_an_engine(
    tmp_path: Path,
) -> None:
    """Chat TTS renders a single message without authoring a project."""
    runtime, _pack = _speech_runtime(tmp_path)
    client = TestClient(create_app(runtime))
    assert (
        client.post(
            "/api/v1/voice/profiles/designed",
            json={
                "profile_id": "chat-narrator",
                "name": "Chat narrator",
                "language": "en-US",
                "description": "voice=en-us+f3; pitch=55",
                "attested_by_user": True,
            },
        ).status_code
        == 201
    )

    spoken = client.post(
        "/api/v1/voice/speak",
        json={"text": "Speaking one chat message.", "profile_id": "chat-narrator"},
    )

    assert spoken.status_code == 201
    payload = spoken.json()
    # The caller never names an engine; the endpoint resolves one by capability.
    assert payload["engine_pack_id"]
    assert payload["duration_seconds"] > 0
    content = client.get(f"/api/v1/voice/artifacts/{payload['artifact_id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "audio/wav"
    assert content.content.startswith(b"RIFF")


def test_speaking_identical_text_reuses_the_render_cache(tmp_path: Path) -> None:
    runtime, _pack = _speech_runtime(tmp_path)
    client = TestClient(create_app(runtime))
    client.post(
        "/api/v1/voice/profiles/designed",
        json={
            "profile_id": "chat-narrator",
            "name": "Chat narrator",
            "language": "en-US",
            "description": "voice=en-us+f3; pitch=55",
            "attested_by_user": True,
        },
    )
    body = {"text": "Repeated line.", "profile_id": "chat-narrator"}

    first = client.post("/api/v1/voice/speak", json=body)
    second = client.post("/api/v1/voice/speak", json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    # Content-addressed: the same text in the same voice is not re-synthesized.
    assert first.json()["artifact_id"] == second.json()["artifact_id"]


def test_speak_rejects_an_unknown_profile(tmp_path: Path) -> None:
    runtime, _pack = _speech_runtime(tmp_path)
    client = TestClient(create_app(runtime))

    response = client.post(
        "/api/v1/voice/speak",
        json={"text": "No such voice.", "profile_id": "does-not-exist"},
    )

    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


def test_speak_returns_every_chunk_for_a_long_message(tmp_path: Path) -> None:
    """A long message must not be silently truncated to its first chunk."""
    runtime, _pack = _speech_runtime(tmp_path)
    client = TestClient(create_app(runtime))
    client.post(
        "/api/v1/voice/profiles/designed",
        json={
            "profile_id": "chat-narrator",
            "name": "Chat narrator",
            "language": "en-US",
            "description": "voice=en-us+f3; pitch=55",
            "attested_by_user": True,
        },
    )
    # Comfortably past the 500-character default chunk size.
    long_text = "The council reaches a verdict and records it. " * 24

    spoken = client.post(
        "/api/v1/voice/speak",
        json={"text": long_text, "profile_id": "chat-narrator"},
    )

    assert spoken.status_code == 201
    payload = spoken.json()
    assert len(payload["artifact_ids"]) > 1
    assert payload["artifact_id"] == payload["artifact_ids"][0]
    # Duration covers the whole message, not just the first chunk.
    first = client.get(
        f"/api/v1/voice/artifacts/{payload['artifact_ids'][0]}"
    ).json()
    assert payload["duration_seconds"] > first["duration_seconds"]
    for artifact_id in payload["artifact_ids"]:
        content = client.get(f"/api/v1/voice/artifacts/{artifact_id}/content")
        assert content.status_code == 200
        assert content.content.startswith(b"RIFF")


def test_speak_uses_profile_language_and_keeps_internal_work_out_of_studio(
    tmp_path: Path,
) -> None:
    runtime, pack = _speech_runtime(tmp_path)
    client = TestClient(create_app(runtime))
    created = client.post(
        "/api/v1/voice/profiles/designed",
        json={
            "profile_id": "british-narrator",
            "name": "British narrator",
            "language": "en-GB",
            "description": "voice=en-gb; pitch=55",
            "attested_by_user": True,
        },
    )
    assert created.status_code == 201

    spoken = client.post(
        "/api/v1/voice/speak",
        json={"text": "Use the profile language.", "profile_id": "british-narrator"},
    )

    assert spoken.status_code == 201
    payload = spoken.json()
    assert runtime.voice_store is not None
    internal_job = runtime.voice_store.get_job(payload["job_id"])
    internal_project = runtime.voice_store.get_project(internal_job.project_id)
    assert internal_project.origin == "chat_speech"
    assert internal_job.origin == "chat_speech"
    assert internal_project.language == "en-GB"
    assert {chunk.plan.language for chunk in internal_job.chunks} == {"en-GB"}

    overview = client.get("/api/v1/voice").json()
    assert overview["projects"] == []
    assert overview["jobs"] == []
    assert payload["artifact_id"] in {
        artifact["id"] for artifact in overview["artifacts"]
    }
    assert client.get("/api/v1/voice/jobs").json()["jobs"] == []
    assert client.get(f"/api/v1/voice/jobs/{payload['job_id']}").status_code == 404
    assert (
        client.post(
            "/api/v1/voice/jobs",
            json={
                "project_id": internal_project.id,
                "engine_pack_id": pack.id,
                "purpose": "private",
            },
        ).status_code
        == 409
    )
    artifact = client.get(
        f"/api/v1/voice/artifacts/{payload['artifact_id']}/content"
    )
    assert artifact.status_code == 200
    assert artifact.content.startswith(b"RIFF")

    overridden = client.post(
        "/api/v1/voice/speak",
        json={
            "text": "Use the caller language.",
            "profile_id": "british-narrator",
            "language": "de-DE",
        },
    )
    assert overridden.status_code == 201
    override_job = runtime.voice_store.get_job(overridden.json()["job_id"])
    override_project = runtime.voice_store.get_project(override_job.project_id)
    assert override_project.language == "de-DE"
    assert {chunk.plan.language for chunk in override_job.chunks} == {"de-DE"}
