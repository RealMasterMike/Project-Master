import base64
import json
import threading
import wave
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    SQLiteComfyStore,
)
from project_master.integrations.comfyui.jobs import (
    JobNotFoundError as ComfyJobNotFoundError,
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
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ApprovalSpec, ProjectSpec, RunSpec
from project_master.orchestration.resource import ResourceGovernor
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.runtime import MasterRuntime
from project_master.tools.builtin import build_registry


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
    )  # type: ignore[arg-type]


def test_health_and_model_status(tmp_path: Path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    assert client.get("/api/v1/health").json()["ok"] is True
    status = client.get("/api/v1/models/status").json()
    assert status["configured_model"] == "test-model"
    assert status["num_ctx"] == 32768
    assert status["models"] == ["test-model"]


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
            "metadata": {"local_only": True},
        },
    )
    assert created.status_code == 201
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
        "chat_mode": "direct",
        "tool_authorization": "read_only",
    }
    events = runtime.orchestration.list_events(run_id)
    authorization_event = next(
        event for event in events if event["event_type"] == "tool_authorization"
    )
    assert authorization_event["payload"]["allow_mutations"] is False


def test_chat_mutation_authorization_is_explicit_and_audited(tmp_path: Path) -> None:
    provider = ToolSchemaProvider()
    runtime = make_runtime(tmp_path, provider)
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

    assert default_response.status_code == 200
    assert authorized_response.status_code == 200
    assert "workspace_write" not in provider.seen_tool_names[0]
    assert "memory_remember" not in provider.seen_tool_names[0]
    assert "workspace_write" in provider.seen_tool_names[1]
    assert "memory_remember" in provider.seen_tool_names[1]
    assert default_response.json()["tool_authorization"]["policy"] == "read_only"
    assert (
        authorized_response.json()["tool_authorization"]["policy"]
        == "explicit_mutations_allowed"
    )
    run = runtime.orchestration.get_run(authorized_response.json()["run_id"])
    assert run is not None
    assert run["metadata"]["chat_mode"] == "direct"
    assert run["metadata"]["allow_mutations"] is True
    assert run["metadata"]["tool_authorization"] == "explicit_mutations_allowed"


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
            "name": "Daily image",
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
    assert imported.json()["trust_state"] == "pending"
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
    overview = client.get("/api/v1/integrations/comfyui").json()
    assert overview["support_available"] is True
    assert overview["profiles"][0]["auth"] is None
    assert overview["workflows"][0]["revision"]["id"] == revision_id


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
