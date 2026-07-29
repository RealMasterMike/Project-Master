import asyncio
import hashlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest

from project_master.integrations.comfyui.artifacts import (
    FilesystemComfyArtifactStore,
)
from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyJob,
    InMemoryJobRepository,
    JobStatus,
)
from project_master.integrations.comfyui.profiles import ComfyUIProfile
from project_master.integrations.comfyui.service import (
    MAX_COMFY_INPUT_IMAGE_BYTES,
    MAX_COMFY_INPUT_IMAGE_EDGE,
    ComfyInputImageError,
    ComfyServiceError,
    ComfyUIService,
    MissingWorkflowResource,
    ResolvedInputImage,
    WorkflowIncompatibleError,
    WorkflowRejectedError,
)
from project_master.integrations.comfyui.transport import (
    ComfyEvent,
    DownloadedOutput,
    HistoryResult,
    InputRef,
    OutputMetadata,
    OutputRef,
    PromptSubmission,
    QueueEntry,
    QueueSnapshot,
)
from project_master.integrations.comfyui.workflow import (
    WorkflowBinding,
    WorkflowValidationError,
)


class FakeTransport:
    def __init__(self) -> None:
        self.snapshot = QueueSnapshot()
        self.histories: dict[str, HistoryResult] = {}
        self.next_submission = PromptSubmission(prompt_id="prompt-1", number=4)
        self.submitted_workflow: Mapping[str, Any] | None = None
        self.submitted_extra: Mapping[str, Any] | None = None
        self.deleted: list[str] = []
        self.interrupt_count = 0
        self.free_count = 0
        self.submit_count = 0
        self.fail_queue = False
        self.fail_free = False
        self.fail_submission = False
        self.fail_object_info = False
        self.fail_upload = False
        self.object_info_count = 0
        self.uploaded_images: list[tuple[bytes, str, str]] = []
        self.operation_order: list[str] = []
        self.download_failures: set[str] = set()
        self.download_count: dict[str, int] = {}
        self.object_types: dict[str, Any] = {
            "CLIPTextEncode": {},
            "KSampler": {},
            "SaveImage": {},
        }

    async def system_stats(self) -> Mapping[str, Any]:
        return {"devices": [{"name": "fake-gpu"}]}

    async def object_info(self) -> Mapping[str, Any]:
        self.object_info_count += 1
        self.operation_order.append("preflight")
        if self.fail_object_info:
            raise OSError("simulated object_info outage")
        return self.object_types

    async def queue(self) -> QueueSnapshot:
        if self.fail_queue:
            raise OSError("simulated queue outage")
        return self.snapshot

    async def free_models_and_memory(self) -> None:
        self.free_count += 1
        if self.fail_free:
            raise OSError("simulated free outage")

    async def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        extra_data: Mapping[str, Any] | None = None,
    ) -> PromptSubmission:
        self.operation_order.append("submit")
        self.submit_count += 1
        if self.fail_submission:
            raise RuntimeError("simulated lost submission response")
        self.submitted_workflow = workflow
        self.submitted_extra = extra_data
        self.snapshot = QueueSnapshot(
            queued=(
                QueueEntry(
                    prompt_id=self.next_submission.prompt_id,
                    number=self.next_submission.number,
                    state="queued",
                    client_id=client_id,
                ),
            )
        )
        return self.next_submission

    async def upload_image(
        self,
        content: bytes,
        *,
        filename: str,
        media_type: str,
    ) -> InputRef:
        self.operation_order.append("upload")
        self.uploaded_images.append((content, filename, media_type))
        if self.fail_upload:
            raise RuntimeError("simulated image upload failure")
        return InputRef(
            filename=filename,
            subfolder="project-master",
            type="input",
        )

    async def history(self, prompt_id: str) -> HistoryResult:
        return self.histories.get(prompt_id, HistoryResult(found=False))

    async def download_output(self, output: OutputMetadata) -> DownloadedOutput:
        filename = output.ref.filename
        self.download_count[filename] = self.download_count.get(filename, 0) + 1
        if filename in self.download_failures:
            raise RuntimeError("simulated artifact download failure")
        query = urlencode(
            {
                "filename": filename,
                "subfolder": output.ref.subfolder,
                "type": output.ref.type,
            }
        )
        return DownloadedOutput(
            content=f"verified:{filename}".encode(),
            media_type=output.media_type or "application/octet-stream",
            source_url=f"http://127.0.0.1:8188/view?{query}",
            fetched_at=datetime.now(UTC),
        )

    async def delete_queue_items(self, prompt_ids: Sequence[str]) -> None:
        self.deleted.extend(prompt_ids)
        deleted = set(prompt_ids)
        self.snapshot = QueueSnapshot(
            running=tuple(item for item in self.snapshot.running if item.prompt_id not in deleted),
            queued=tuple(item for item in self.snapshot.queued if item.prompt_id not in deleted),
        )

    async def interrupt(self) -> None:
        self.interrupt_count += 1
        self.snapshot = QueueSnapshot(queued=self.snapshot.queued)

    async def events(self, client_id: str) -> AsyncIterator[ComfyEvent]:
        yield ComfyEvent(type="status", data={"client_id": client_id})


def workflow() -> dict:
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "original"},
        }
    }


_IMAGE_ASSET_ID = f"media-asset-{'a' * 32}"
_IMAGE_CONTENT = b"verified project image"
_IMAGE_SHA256 = hashlib.sha256(_IMAGE_CONTENT).hexdigest()


def resolved_image(**overrides: Any) -> ResolvedInputImage:
    values: dict[str, Any] = {
        "asset_id": _IMAGE_ASSET_ID,
        "name": "source.png",
        "kind": "image",
        "media_type": "image/png",
        "sha256": _IMAGE_SHA256,
        "size_bytes": len(_IMAGE_CONTENT),
        "width": 1_024,
        "height": 768,
        "content": _IMAGE_CONTENT,
    }
    values.update(overrides)
    return ResolvedInputImage(**values)


def add_image_workflow(service: ComfyUIService) -> str:
    revision = service.import_workflow(
        "Image-to-image input",
        {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "placeholder.png"},
            }
        },
        [
            WorkflowBinding(
                id="source_image",
                node_id="1",
                input_name="image",
                value_type="image_asset",
            )
        ],
        purpose="image",
    )
    return revision.id


def add_loader_workflow(
    service: ComfyUIService,
    *,
    class_type: str,
    input_name: str,
    resource_name: Any,
    bindings: Sequence[WorkflowBinding] = (),
) -> str:
    revision = service.import_workflow(
        "Loader resource",
        {
            "10": {
                "class_type": class_type,
                "inputs": {input_name: resource_name},
            }
        },
        bindings,
    )
    return revision.id


def make_service(
    *,
    artifact_store: FilesystemComfyArtifactStore | None = None,
    jobs: InMemoryJobRepository | None = None,
    input_image_resolver: Callable[[str, str], ResolvedInputImage] | None = None,
    before_workflow_submit: Callable[[], Any] | None = None,
) -> tuple[ComfyUIService, FakeTransport, str]:
    transport = FakeTransport()
    service = ComfyUIService(
        [ComfyUIProfile(id="local", name="Local")],
        lambda _profile: transport,
        jobs=jobs,
        artifact_store=artifact_store,
        input_image_resolver=input_image_resolver,
        before_workflow_submit=before_workflow_submit,
    )
    revision = service.import_workflow(
        "Prompt",
        workflow(),
        [
            WorkflowBinding(
                id="prompt",
                node_id="1",
                input_name="text",
                value_type="string",
            )
        ],
    )
    return service, transport, revision.id


def test_connection_and_submission_are_typed_and_provenanced() -> None:
    service, transport, revision_id = make_service()

    async def exercise() -> None:
        status = await service.connection_status("local")
        assert status.ok
        assert status.device_count == 1
        assert status.object_type_count == 3
        compatibility = await service.validate_compatibility("local", revision_id)
        assert compatibility.compatible
        assert compatibility.missing_node_types == ()
        assert compatibility.missing_resources == ()

        job = await service.submit_workflow("local", revision_id, {"prompt": "a moonlit city"})
        assert job.status == JobStatus.QUEUED
        assert job.remote_prompt_id == "prompt-1"
        assert transport.submitted_workflow is not None
        assert transport.submitted_workflow["1"]["inputs"]["text"] == "a moonlit city"
        assert transport.submitted_extra is not None
        provenance = transport.submitted_extra["project_master"]
        assert provenance["job_id"] == job.id
        assert provenance["workflow_revision_id"] == revision_id
        assert "project_id" not in provenance

    asyncio.run(exercise())


def test_idle_model_release_checks_every_queue_before_freeing_any_profile() -> None:
    profiles = (
        ComfyUIProfile(id="active", name="Active"),
        ComfyUIProfile(id="idle", name="Idle"),
        ComfyUIProfile(id="offline", name="Offline"),
    )
    transports = {profile.id: FakeTransport() for profile in profiles}
    transports["active"].snapshot = QueueSnapshot(
        running=(
            QueueEntry(
                prompt_id="prompt-active",
                state="running",
            ),
        )
    )
    transports["offline"].fail_queue = True
    service = ComfyUIService(
        profiles,
        lambda profile: transports[profile.id],
    )

    async def exercise() -> None:
        busy = await service.release_idle_models()

        assert not busy.ready
        assert busy.active_profile_ids == ("active",)
        assert busy.released_profile_ids == ()
        assert busy.unreachable_profile_ids == ("offline",)
        assert all(transport.free_count == 0 for transport in transports.values())

        transports["active"].snapshot = QueueSnapshot()
        idle = await service.release_idle_models()

        assert idle.ready
        assert idle.active_profile_ids == ()
        assert idle.released_profile_ids == ("active", "idle")
        assert idle.unreachable_profile_ids == ("offline",)
        assert transports["active"].free_count == 1
        assert transports["idle"].free_count == 1
        assert transports["offline"].free_count == 0

    asyncio.run(exercise())


def test_unreachable_optional_comfy_profile_does_not_block_model_handoff() -> None:
    transport = FakeTransport()
    transport.fail_queue = True
    service = ComfyUIService(
        [ComfyUIProfile(id="offline", name="Offline")],
        lambda _profile: transport,
    )

    result = asyncio.run(
        service.release_idle_models(profile_timeout_seconds=0.1)
    )

    assert result.ready
    assert result.released_profile_ids == ()
    assert result.unreachable_profile_ids == ("offline",)
    assert transport.free_count == 0


def test_workflow_submission_awaits_local_model_handoff_before_queueing() -> None:
    events: list[str] = []
    transport_ref: FakeTransport | None = None

    async def unload_local_model() -> None:
        assert transport_ref is not None
        transport_ref.operation_order.append("handoff")
        events.append("unload")

    service, transport, revision_id = make_service(
        before_workflow_submit=unload_local_model
    )
    transport_ref = transport

    async def exercise() -> None:
        job = await service.submit_workflow(
            "local",
            revision_id,
            {"prompt": "handoff"},
        )

        assert job.status == JobStatus.QUEUED
        assert events == ["unload"]
        assert transport.operation_order == ["preflight", "handoff", "submit"]

    asyncio.run(exercise())


def test_failed_local_model_handoff_cannot_create_or_submit_a_job() -> None:
    async def fail_handoff() -> None:
        raise RuntimeError("simulated Ollama unload failure")

    service, transport, revision_id = make_service(
        before_workflow_submit=fail_handoff
    )

    async def exercise() -> None:
        with pytest.raises(ComfyServiceError, match="handoff failed"):
            await service.submit_workflow(
                "local",
                revision_id,
                {"prompt": "not submitted"},
            )

        assert service.jobs.list() == ()
        assert transport.submit_count == 0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("class_type", "input_name"),
    [
        ("CheckpointLoaderSimple", "ckpt_name"),
        ("UNETLoader", "unet_name"),
        ("UnetLoaderGGUF", "unet_name"),
        ("CLIPLoader", "clip_name"),
        ("CLIPLoaderGGUF", "clip_name"),
        ("VAELoader", "vae_name"),
        ("LoraLoaderModelOnly", "lora_name"),
    ],
)
def test_fixed_audited_loader_resource_must_be_advertised_by_comfyui(
    class_type: str,
    input_name: str,
) -> None:
    service, transport, _ = make_service()
    requested = "missing/model.safetensors"
    revision_id = add_loader_workflow(
        service,
        class_type=class_type,
        input_name=input_name,
        resource_name=requested,
    )
    transport.object_types[class_type] = {
        "input": {
            "required": {
                input_name: [
                    ["available/model.safetensors"],
                    {"tooltip": "Installed models"},
                ]
            }
        }
    }
    expected = MissingWorkflowResource(
        node_id="10",
        class_type=class_type,
        input_name=input_name,
        resource_name=requested,
    )

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)

        assert not compatibility.compatible
        assert compatibility.missing_node_types == ()
        assert compatibility.missing_resources == (expected,)
        assert compatibility.model_dump(mode="json")["missing_resources"] == [
            {
                "node_id": "10",
                "class_type": class_type,
                "input_name": input_name,
                "resource_name": requested,
            }
        ]

        with pytest.raises(WorkflowIncompatibleError, match="loader resources") as caught:
            await service.submit_workflow("local", revision_id)

        assert caught.value.missing_node_types == ()
        assert caught.value.missing_resources == (expected,)
        assert requested in str(caught.value)
        assert service.jobs.list() == ()
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_available_fixed_loader_resource_remains_compatible_and_submits() -> None:
    service, transport, _ = make_service()
    requested = "installed/model.safetensors"
    revision_id = add_loader_workflow(
        service,
        class_type="CheckpointLoaderSimple",
        input_name="ckpt_name",
        resource_name=requested,
    )
    transport.object_types["CheckpointLoaderSimple"] = {
        "input": {
            "required": {
                "ckpt_name": [["other.safetensors", requested]],
            }
        }
    }

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)
        job = await service.submit_workflow("local", revision_id)

        assert compatibility.compatible
        assert compatibility.missing_resources == ()
        assert job.status == JobStatus.QUEUED
        assert transport.submit_count == 1

    asyncio.run(exercise())


def test_missing_loader_resource_rejects_before_project_image_resolution_or_upload() -> None:
    resolver_calls: list[tuple[str, str]] = []

    def resolver(project_id: str, asset_id: str) -> ResolvedInputImage:
        resolver_calls.append((project_id, asset_id))
        return resolved_image()

    service, transport, _ = make_service(input_image_resolver=resolver)
    revision = service.import_workflow(
        "Image workflow with missing checkpoint",
        {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "missing.safetensors"},
            },
            "10": {
                "class_type": "LoadImage",
                "inputs": {"image": "placeholder.png"},
            },
        },
        [
            WorkflowBinding(
                id="source_image",
                node_id="10",
                input_name="image",
                value_type="image_asset",
            )
        ],
        purpose="image",
    )
    transport.object_types.update(
        {
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [["installed.safetensors"]],
                    }
                }
            },
            "LoadImage": {},
        }
    )

    async def exercise() -> None:
        with pytest.raises(WorkflowIncompatibleError) as caught:
            await service.submit_workflow(
                "local",
                revision.id,
                {"source_image": _IMAGE_ASSET_ID},
                project_id="project-creator-1",
            )

        assert caught.value.missing_resources[0].resource_name == "missing.safetensors"
        assert resolver_calls == []
        assert transport.uploaded_images == []
        assert transport.operation_order == ["preflight"]
        assert service.jobs.list() == ()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "loader_info",
    [
        {},
        {"input": {"required": {"ckpt_name": ["STRING", {"default": ""}]}}},
        {"input": {"required": {"ckpt_name": [[[1, 2, 3]]]}}},
    ],
)
def test_legacy_or_non_enum_loader_metadata_does_not_create_false_missing_resource(
    loader_info: dict[str, Any],
) -> None:
    service, transport, _ = make_service()
    revision_id = add_loader_workflow(
        service,
        class_type="CheckpointLoaderSimple",
        input_name="ckpt_name",
        resource_name="not-advertised.safetensors",
    )
    transport.object_types["CheckpointLoaderSimple"] = loader_info

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)
        job = await service.submit_workflow("local", revision_id)

        assert compatibility.compatible
        assert compatibility.missing_resources == ()
        assert job.status == JobStatus.QUEUED

    asyncio.run(exercise())


def test_dynamic_loader_input_is_not_treated_as_a_fixed_resource() -> None:
    service, transport, _ = make_service()
    revision = service.import_workflow(
        "Dynamic loader",
        {
            "9": {
                "class_type": "ModelNameProvider",
                "inputs": {},
            },
            "10": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": ["9", 0]},
            },
        },
    )
    transport.object_types.update(
        {
            "ModelNameProvider": {},
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [["only-fixed-names-are-compared.safetensors"]],
                    }
                }
            },
        }
    )

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision.id)
        job = await service.submit_workflow("local", revision.id)

        assert compatibility.compatible
        assert compatibility.missing_resources == ()
        assert job.status == JobStatus.QUEUED

    asyncio.run(exercise())


def test_bound_loader_is_dynamic_for_general_compatibility_but_rendered_value_is_checked() -> None:
    service, transport, _ = make_service()
    binding = WorkflowBinding(
        id="checkpoint",
        node_id="10",
        input_name="ckpt_name",
        value_type="string",
    )
    revision_id = add_loader_workflow(
        service,
        class_type="CheckpointLoaderSimple",
        input_name="ckpt_name",
        resource_name="placeholder.safetensors",
        bindings=(binding,),
    )
    transport.object_types["CheckpointLoaderSimple"] = {
        "input": {
            "required": {
                "ckpt_name": [["installed.safetensors"]],
            }
        }
    }

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)
        assert compatibility.compatible
        assert compatibility.missing_resources == ()

        with pytest.raises(WorkflowIncompatibleError) as caught:
            await service.submit_workflow(
                "local",
                revision_id,
                {"checkpoint": "missing.safetensors"},
            )

        assert caught.value.missing_resources[0].resource_name == "missing.safetensors"
        assert service.jobs.list() == ()
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_non_audited_loader_resource_is_not_inferred_from_arbitrary_enum_metadata() -> None:
    service, transport, _ = make_service()
    revision_id = add_loader_workflow(
        service,
        class_type="ThirdPartyLoader",
        input_name="model_name",
        resource_name="missing.safetensors",
    )
    transport.object_types["ThirdPartyLoader"] = {
        "input": {
            "required": {
                "model_name": [["installed.safetensors"]],
            }
        }
    }

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)
        job = await service.submit_workflow("local", revision_id)

        assert compatibility.compatible
        assert compatibility.missing_resources == ()
        assert job.status == JobStatus.QUEUED

    asyncio.run(exercise())


def test_missing_node_preflight_rejects_before_persisting_or_submitting_job() -> None:
    service, transport, revision_id = make_service()
    transport.object_types.pop("CLIPTextEncode")

    async def exercise() -> None:
        compatibility = await service.validate_compatibility("local", revision_id)
        assert not compatibility.compatible
        assert compatibility.missing_node_types == ("CLIPTextEncode",)

        with pytest.raises(WorkflowIncompatibleError, match="CLIPTextEncode") as caught:
            await service.submit_workflow("local", revision_id, {"prompt": "not submitted"})

        assert caught.value.missing_node_types == ("CLIPTextEncode",)
        assert service.jobs.list() == ()
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_failed_compatibility_preflight_cannot_create_or_submit_a_job() -> None:
    service, transport, revision_id = make_service()
    transport.fail_object_info = True

    async def exercise() -> None:
        with pytest.raises(ComfyServiceError, match="no job was created or submitted"):
            await service.submit_workflow("local", revision_id, {"prompt": "not submitted"})

        assert service.jobs.list() == ()
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_submission_preserves_creator_project_association_in_metadata() -> None:
    service, transport, revision_id = make_service()

    async def exercise() -> None:
        job = await service.submit_workflow(
            "local",
            revision_id,
            {"prompt": "creator asset"},
            project_id="project-creator-1",
        )

        assert job.project_id == "project-creator-1"
        assert transport.submitted_extra is not None
        assert transport.submitted_extra["project_master"]["project_id"] == job.project_id

    asyncio.run(exercise())


def test_project_image_is_verified_staged_and_provenanced_before_submission() -> None:
    resolver_calls: list[tuple[str, str]] = []
    transport_ref: FakeTransport | None = None

    def resolver(project_id: str, asset_id: str) -> ResolvedInputImage:
        resolver_calls.append((project_id, asset_id))
        assert transport_ref is not None
        transport_ref.operation_order.append("resolve")
        return resolved_image()

    service, transport, _ = make_service(input_image_resolver=resolver)
    transport_ref = transport
    transport.object_types["LoadImage"] = {}
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        job = await service.submit_workflow(
            "local",
            revision_id,
            {"source_image": _IMAGE_ASSET_ID},
            project_id="project-creator-1",
        )

        filename = f"{_IMAGE_SHA256}.png"
        assert job.status == JobStatus.QUEUED
        assert job.project_id == "project-creator-1"
        assert resolver_calls == [("project-creator-1", _IMAGE_ASSET_ID)]
        assert transport.uploaded_images == [(_IMAGE_CONTENT, filename, "image/png")]
        assert transport.submitted_workflow is not None
        assert transport.submitted_workflow["1"]["inputs"]["image"] == f"project-master/{filename}"
        assert transport.operation_order == [
            "preflight",
            "resolve",
            "upload",
            "submit",
        ]
        assert len(job.input_images) == 1
        provenance = job.input_images[0]
        assert provenance.binding_id == "source_image"
        assert provenance.source_asset_id == _IMAGE_ASSET_ID
        assert provenance.source_sha256 == _IMAGE_SHA256
        assert provenance.source_name == "source.png"

    asyncio.run(exercise())


def test_project_image_binding_requires_project_before_compatibility_preflight() -> None:
    service, transport, _ = make_service(
        input_image_resolver=lambda _project, _asset: resolved_image()
    )
    transport.object_types["LoadImage"] = {}
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        with pytest.raises(WorkflowValidationError, match="project_id is required"):
            await service.submit_workflow(
                "local",
                revision_id,
                {"source_image": _IMAGE_ASSET_ID},
            )

        assert transport.object_info_count == 0
        assert transport.uploaded_images == []
        assert transport.submit_count == 0
        assert service.jobs.list() == ()

    asyncio.run(exercise())


def test_image_node_compatibility_is_checked_before_resolving_or_uploading() -> None:
    resolver_calls: list[tuple[str, str]] = []

    def resolver(project_id: str, asset_id: str) -> ResolvedInputImage:
        resolver_calls.append((project_id, asset_id))
        return resolved_image()

    service, transport, _ = make_service(input_image_resolver=resolver)
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        with pytest.raises(WorkflowIncompatibleError, match="LoadImage"):
            await service.submit_workflow(
                "local",
                revision_id,
                {"source_image": _IMAGE_ASSET_ID},
                project_id="project-creator-1",
            )

        assert resolver_calls == []
        assert transport.uploaded_images == []
        assert transport.submit_count == 0
        assert service.jobs.list() == ()

    asyncio.run(exercise())


def test_unowned_or_missing_project_image_cannot_create_or_submit_a_job() -> None:
    def resolver(_project_id: str, _asset_id: str) -> ResolvedInputImage:
        raise KeyError("asset does not belong to project")

    service, transport, _ = make_service(input_image_resolver=resolver)
    transport.object_types["LoadImage"] = {}
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        with pytest.raises(ComfyInputImageError, match="could not be resolved"):
            await service.submit_workflow(
                "local",
                revision_id,
                {"source_image": _IMAGE_ASSET_ID},
                project_id="project-creator-1",
            )

        assert transport.uploaded_images == []
        assert transport.submit_count == 0
        assert service.jobs.list() == ()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"asset_id": f"media-asset-{'b' * 32}"}, "different asset"),
        ({"kind": "video"}, "Only supported project image"),
        ({"media_type": "image/svg+xml"}, "Only supported project image"),
        ({"name": "../source.png"}, "invalid source name"),
        ({"sha256": "b" * 64}, "SHA-256 verification"),
        (
            {"size_bytes": MAX_COMFY_INPUT_IMAGE_BYTES + 1},
            "50 MiB input limit",
        ),
        ({"size_bytes": len(_IMAGE_CONTENT) + 1}, "size does not match"),
        ({"width": None}, "dimensions are required"),
        (
            {"width": MAX_COMFY_INPUT_IMAGE_EDGE + 1, "height": 1},
            "16384-pixel edge limit",
        ),
        ({"width": 9_000, "height": 8_000}, "64-megapixel limit"),
    ],
)
def test_invalid_project_image_metadata_is_rejected_before_upload(
    overrides: dict[str, Any],
    message: str,
) -> None:
    service, transport, _ = make_service(
        input_image_resolver=lambda _project, _asset: resolved_image(**overrides)
    )
    transport.object_types["LoadImage"] = {}
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        with pytest.raises(ComfyInputImageError, match=message):
            await service.submit_workflow(
                "local",
                revision_id,
                {"source_image": _IMAGE_ASSET_ID},
                project_id="project-creator-1",
            )

        assert transport.uploaded_images == []
        assert transport.submit_count == 0
        assert service.jobs.list() == ()

    asyncio.run(exercise())


def test_failed_project_image_upload_cannot_create_or_submit_a_job() -> None:
    service, transport, _ = make_service(
        input_image_resolver=lambda _project, _asset: resolved_image()
    )
    transport.object_types["LoadImage"] = {}
    transport.fail_upload = True
    revision_id = add_image_workflow(service)

    async def exercise() -> None:
        with pytest.raises(ComfyInputImageError, match="could not be staged"):
            await service.submit_workflow(
                "local",
                revision_id,
                {"source_image": _IMAGE_ASSET_ID},
                project_id="project-creator-1",
            )

        assert len(transport.uploaded_images) == 1
        assert transport.submit_count == 0
        assert service.jobs.list() == ()

    asyncio.run(exercise())


def test_refresh_reconciles_running_and_completed_output_metadata() -> None:
    service, transport, revision_id = make_service()
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="final.png", subfolder="daily"),
        media_type="image/png",
        width=1024,
        height=1024,
    )

    async def exercise() -> None:
        job = await service.submit_workflow("local", revision_id, {"prompt": "test"})
        transport.snapshot = QueueSnapshot(
            running=(QueueEntry(prompt_id="prompt-1", number=4, state="running"),)
        )
        running = await service.refresh_job(job.id)
        assert running.status == JobStatus.RUNNING
        assert running.started_at is not None

        transport.snapshot = QueueSnapshot()
        transport.histories["prompt-1"] = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(output,),
        )
        completed = await service.refresh_job(job.id)
        assert completed.status == JobStatus.SUCCEEDED
        assert completed.outputs == (output,)
        assert service.outputs(job.id)[0].ref.filename == "final.png"

    asyncio.run(exercise())


def test_cancel_scopes_queue_delete_and_running_interrupt_to_owned_prompt() -> None:
    service, transport, revision_id = make_service()

    async def exercise() -> None:
        queued = await service.submit_workflow("local", revision_id, {"prompt": "queued"})
        cancelled = await service.cancel_job(queued.id)
        assert cancelled.status == JobStatus.CANCELLED
        assert transport.deleted == ["prompt-1"]
        assert transport.interrupt_count == 0

        transport.next_submission = PromptSubmission(prompt_id="prompt-2", number=5)
        running = await service.submit_workflow("local", revision_id, {"prompt": "running"})
        transport.snapshot = QueueSnapshot(
            running=(QueueEntry(prompt_id="prompt-2", number=5, state="running"),)
        )
        cancelled_running = await service.cancel_job(running.id)
        assert cancelled_running.status == JobStatus.CANCELLED
        assert transport.interrupt_count == 1

    asyncio.run(exercise())


def test_cancel_refuses_global_interrupt_when_multiple_prompts_are_running() -> None:
    service, transport, revision_id = make_service()

    async def exercise() -> None:
        running = await service.submit_workflow("local", revision_id, {"prompt": "running"})
        transport.snapshot = QueueSnapshot(
            running=(
                QueueEntry(prompt_id="someone-elses-prompt", number=1, state="running"),
                QueueEntry(prompt_id="prompt-1", number=2, state="running"),
            )
        )
        with pytest.raises(ComfyServiceError, match="interrupt is global"):
            await service.cancel_job(running.id)
        assert transport.interrupt_count == 0
        assert service.job_status(running.id).status == JobStatus.QUEUED

    asyncio.run(exercise())


def test_node_rejection_is_recorded_as_failed_job() -> None:
    service, transport, revision_id = make_service()
    transport.next_submission = PromptSubmission(
        prompt_id="rejected",
        node_errors={"1": {"errors": ["missing model"]}},
    )

    async def exercise() -> None:
        with pytest.raises(WorkflowRejectedError) as caught:
            await service.submit_workflow("local", revision_id, {"prompt": "test"})
        failed = service.job_status(caught.value.job_id)
        assert failed.status == JobStatus.FAILED
        assert failed.finished_at is not None
        assert failed.error == "ComfyUI rejected one or more workflow nodes."

    asyncio.run(exercise())


def test_reconcile_marks_untracked_remote_prompt_orphaned_and_streams_events() -> None:
    service, transport, revision_id = make_service()

    async def exercise() -> None:
        job = await service.submit_workflow("local", revision_id, {"prompt": "test"})
        transport.snapshot = QueueSnapshot()
        reconciled = await service.reconcile("local")
        assert reconciled[0].id == job.id
        assert reconciled[0].status == JobStatus.ORPHANED

        events = [event async for event in service.events("local", "project-master-client")]
        assert events[0].type == "status"
        assert events[0].data["client_id"] == "project-master-client"

    asyncio.run(exercise())


def test_completed_outputs_are_imported_with_durable_job_provenance(tmp_path) -> None:
    artifact_store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    service, transport, revision_id = make_service(artifact_store=artifact_store)
    output = OutputMetadata(
        node_id="9",
        category="images",
        ref=OutputRef(filename="final.png", subfolder="daily"),
        media_type="image/png",
        width=1024,
        height=1024,
    )

    async def exercise() -> None:
        job = await service.submit_workflow("local", revision_id, {"prompt": "test"})
        transport.snapshot = QueueSnapshot()
        transport.histories["prompt-1"] = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(output,),
        )
        completed = await service.refresh_job(job.id)

        assert completed.status == JobStatus.SUCCEEDED
        assert completed.artifact_status == ArtifactStatus.READY
        assert len(completed.artifacts) == 1
        artifact = completed.artifacts[0]
        assert artifact.provenance.job_id == job.id
        assert artifact.provenance.remote_prompt_id == "prompt-1"
        assert artifact.provenance.workflow_digest == service.get_workflow(revision_id).digest
        assert service.artifacts(job.id) == (artifact,)
        assert service.artifact_path(job.id, artifact.id).read_bytes() == (b"verified:final.png")

    asyncio.run(exercise())


def test_partial_artifact_import_retries_without_redownloading_verified_files(
    tmp_path,
) -> None:
    artifact_store = FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts")
    service, transport, revision_id = make_service(artifact_store=artifact_store)
    first = OutputMetadata(
        node_id="9",
        category="images",
        output_index=0,
        ref=OutputRef(filename="first.png"),
        media_type="image/png",
    )
    second = OutputMetadata(
        node_id="9",
        category="images",
        output_index=1,
        ref=OutputRef(filename="second.png"),
        media_type="image/png",
    )

    async def exercise() -> None:
        job = await service.submit_workflow("local", revision_id, {"prompt": "test"})
        transport.snapshot = QueueSnapshot()
        transport.histories["prompt-1"] = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(first, second),
        )
        transport.download_failures.add("second.png")
        partial = await service.refresh_job(job.id)
        assert partial.status == JobStatus.SUCCEEDED
        assert partial.artifact_status == ArtifactStatus.PARTIAL
        assert len(partial.artifacts) == 1

        transport.download_failures.clear()
        ready = await service.refresh_job(job.id)
        assert ready.artifact_status == ArtifactStatus.READY
        assert len(ready.artifacts) == 2
        assert transport.download_count == {"first.png": 1, "second.png": 2}

    asyncio.run(exercise())


def test_submission_transport_loss_is_orphaned_without_automatic_resubmission() -> None:
    service, transport, revision_id = make_service()
    transport.fail_submission = True

    async def exercise() -> None:
        with pytest.raises(ComfyServiceError, match="outcome is unknown"):
            await service.submit_workflow("local", revision_id, {"prompt": "test"})
        uncertain = service.jobs.list()[0]
        assert uncertain.status == JobStatus.ORPHANED
        assert uncertain.remote_prompt_id is None
        assert "did not resubmit" in (uncertain.status_detail or "")

        reconciled = await service.reconcile("local")
        assert reconciled[0].status == JobStatus.ORPHANED
        assert transport.submit_count == 1

    asyncio.run(exercise())


def test_restart_marks_persisted_submitting_job_ambiguous_without_resubmission() -> None:
    service, transport, revision_id = make_service()
    submitting = service.jobs.create(
        ComfyJob.new(
            job_id="comfy-job-crash-window",
            profile_id="local",
            workflow_revision_id=revision_id,
            client_id="project-master-crash-window",
        )
    )

    async def exercise() -> None:
        reconciled = await service.reconcile("local")
        recovered = next(item for item in reconciled if item.id == submitting.id)

        assert recovered.status == JobStatus.ORPHANED
        assert recovered.remote_prompt_id is None
        assert "did not resubmit" in (recovered.status_detail or "")
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_restart_recovers_unique_queued_prompt_by_persisted_client_id() -> None:
    service, transport, revision_id = make_service()
    submitting = service.jobs.create(
        ComfyJob.new(
            job_id="comfy-job-recoverable",
            profile_id="local",
            workflow_revision_id=revision_id,
            client_id="project-master-recoverable",
        )
    )
    transport.snapshot = QueueSnapshot(
        queued=(
            QueueEntry(
                prompt_id="recovered-prompt",
                number=7,
                state="queued",
                client_id=submitting.client_id,
            ),
        )
    )

    async def exercise() -> None:
        reconciled = await service.reconcile("local")
        recovered = next(item for item in reconciled if item.id == submitting.id)

        assert recovered.status == JobStatus.QUEUED
        assert recovered.remote_prompt_id == "recovered-prompt"
        assert recovered.queue_number == 7
        assert "unique client ID" in (recovered.status_detail or "")
        assert transport.submit_count == 0

    asyncio.run(exercise())


def test_restart_reconciles_known_prompt_and_materializes_history_outputs(
    tmp_path,
) -> None:
    service, transport, revision_id = make_service()
    revision = service.get_workflow(revision_id)
    output = OutputMetadata(
        node_id="9",
        category="files",
        ref=OutputRef(filename="result.json"),
        media_type="application/json",
    )

    async def exercise() -> None:
        queued = await service.submit_workflow("local", revision_id, {"prompt": "test"})
        restored_jobs = InMemoryJobRepository.restore_snapshot(service.jobs.export_snapshot())
        restarted = ComfyUIService(
            [ComfyUIProfile(id="local", name="Local")],
            lambda _profile: transport,
            jobs=restored_jobs,
            artifact_store=FilesystemComfyArtifactStore(tmp_path / "comfy-artifacts"),
        )
        restarted.add_workflow(revision)
        transport.snapshot = QueueSnapshot()
        transport.histories["prompt-1"] = HistoryResult(
            found=True,
            completed=True,
            status_text="success",
            outputs=(output,),
        )

        reconciled = await restarted.reconcile("local")

        assert reconciled[0].id == queued.id
        assert reconciled[0].status == JobStatus.SUCCEEDED
        assert reconciled[0].artifact_status == ArtifactStatus.READY, reconciled[0].artifact_error
        assert len(reconciled[0].artifacts) == 1
        assert transport.submit_count == 1

    asyncio.run(exercise())
