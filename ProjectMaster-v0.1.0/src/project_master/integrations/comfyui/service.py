from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from project_master.integrations.comfyui.artifacts import (
    ComfyArtifact,
    ComfyArtifactProvenance,
    ComfyArtifactStore,
)
from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyInputImageProvenance,
    ComfyJob,
    InMemoryJobRepository,
    JobRepository,
    JobStatus,
)
from project_master.integrations.comfyui.profiles import ComfyUIProfile
from project_master.integrations.comfyui.transport import (
    MAX_COMFY_INPUT_IMAGE_BYTES,
    ComfyEvent,
    ComfyTransport,
    HistoryResult,
    OutputMetadata,
    QueueSnapshot,
)
from project_master.integrations.comfyui.workflow import (
    WorkflowBinding,
    WorkflowPurpose,
    WorkflowRevision,
    WorkflowValidationError,
)

TransportFactory = Callable[[ComfyUIProfile], ComfyTransport]
BeforeWorkflowSubmit = Callable[[], Awaitable[None]]
MAX_COMFY_INPUT_IMAGE_EDGE = 16_384
MAX_COMFY_INPUT_IMAGE_PIXELS = 64 * 1024 * 1024
_IMAGE_EXTENSION_BY_MEDIA_TYPE = {
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}
_LOADER_RESOURCE_INPUTS = {
    "CheckpointLoaderSimple": "ckpt_name",
    "UNETLoader": "unet_name",
    "UnetLoaderGGUF": "unet_name",
    "CLIPLoader": "clip_name",
    "CLIPLoaderGGUF": "clip_name",
    "VAELoader": "vae_name",
    "LoraLoaderModelOnly": "lora_name",
}


class ComfyServiceError(RuntimeError):
    pass


class UnknownProfileError(ComfyServiceError):
    pass


class UnknownWorkflowError(ComfyServiceError):
    pass


class ComfyInputImageError(ComfyServiceError):
    pass


class MissingWorkflowResource(BaseModel):
    """One fixed loader value absent from ComfyUI's advertised enum choices."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    class_type: str
    input_name: str
    resource_name: str


class WorkflowIncompatibleError(ComfyServiceError):
    def __init__(
        self,
        profile_id: str,
        workflow_revision_id: str,
        missing_node_types: Sequence[str],
        missing_resources: Sequence[MissingWorkflowResource] = (),
    ) -> None:
        self.profile_id = profile_id
        self.workflow_revision_id = workflow_revision_id
        self.missing_node_types = tuple(missing_node_types)
        self.missing_resources = tuple(missing_resources)
        if not self.missing_resources:
            joined = ", ".join(self.missing_node_types)
            super().__init__(
                f"ComfyUI profile {profile_id!r} is missing node types required by "
                f"workflow {workflow_revision_id!r}: {joined}."
            )
            return
        sections: list[str] = []
        if self.missing_node_types:
            sections.append(f"node types: {', '.join(self.missing_node_types)}")
        resource_summary = ", ".join(
            (
                f"{item.class_type}.{item.input_name}="
                f"{item.resource_name[:160]!r} (node {item.node_id!r})"
            )
            for item in self.missing_resources
        )
        sections.append(f"loader resources: {resource_summary}")
        super().__init__(
            f"ComfyUI profile {profile_id!r} is missing requirements for "
            f"workflow {workflow_revision_id!r}: {'; '.join(sections)}."
        )


class WorkflowRejectedError(ComfyServiceError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"ComfyUI rejected workflow submission for job {job_id}.")


class ConnectionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    ok: bool
    device_count: int = 0
    object_type_count: int = 0


class WorkflowCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    workflow_revision_id: str
    compatible: bool
    missing_node_types: tuple[str, ...] = ()
    missing_resources: tuple[MissingWorkflowResource, ...] = ()


class ComfyMemoryRelease(BaseModel):
    """Best-effort result of releasing idle ComfyUI model caches."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    active_profile_ids: tuple[str, ...] = ()
    released_profile_ids: tuple[str, ...] = ()
    unreachable_profile_ids: tuple[str, ...] = ()


class ResolvedInputImage(BaseModel):
    """Verified app-owned image bytes returned by the runtime's narrow resolver."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    asset_id: str = Field(pattern=r"^media-asset-[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["image", "video", "audio"]
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    content: bytes


InputImageResolver = Callable[[str, str], ResolvedInputImage]


class ComfyUIService:
    """Typed application boundary suitable for future tool adapters.

    This class intentionally does not register Project Master tools. Callers can expose selected
    methods only after applying their own permission and approval policy.
    """

    def __init__(
        self,
        profiles: Sequence[ComfyUIProfile],
        transport_factory: TransportFactory,
        *,
        jobs: JobRepository | None = None,
        artifact_store: ComfyArtifactStore | None = None,
        input_image_resolver: InputImageResolver | None = None,
        before_workflow_submit: BeforeWorkflowSubmit | None = None,
    ) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("ComfyUI profile IDs must be unique.")
        self._transport_factory = transport_factory
        self.jobs = jobs or InMemoryJobRepository()
        self._artifact_store = artifact_store
        self._input_image_resolver = input_image_resolver
        self._before_workflow_submit = before_workflow_submit
        self._workflows: dict[str, WorkflowRevision] = {}

    def list_profiles(self) -> tuple[ComfyUIProfile, ...]:
        return tuple(sorted(self._profiles.values(), key=lambda item: item.id))

    def add_profile(self, profile: ComfyUIProfile) -> None:
        if profile.id in self._profiles:
            raise ValueError(f"ComfyUI profile {profile.id!r} already exists.")
        self._profiles[profile.id] = profile

    def upsert_profile(self, profile: ComfyUIProfile) -> None:
        self._profiles[profile.id] = profile.model_copy(deep=True)

    def list_workflows(self) -> tuple[WorkflowRevision, ...]:
        return tuple(
            item.model_copy(deep=True)
            for item in sorted(
                self._workflows.values(),
                key=lambda revision: (revision.created_at, revision.id),
                reverse=True,
            )
        )

    def add_workflow(self, revision: WorkflowRevision) -> None:
        validated = WorkflowRevision.model_validate(revision.model_dump())
        existing = self._workflows.get(validated.id)
        if existing is not None and existing.digest != validated.digest:
            raise ComfyServiceError("Workflow revision ID collision.")
        self._workflows[validated.id] = validated.model_copy(deep=True)

    def import_workflow(
        self,
        name: str,
        source: str | bytes | Mapping[str, Any],
        bindings: Sequence[WorkflowBinding] = (),
        *,
        purpose: WorkflowPurpose = "general",
    ) -> WorkflowRevision:
        revision = WorkflowRevision.import_json(
            name,
            source,
            tuple(bindings),
            purpose=purpose,
        )
        self.add_workflow(revision)
        return revision.model_copy(deep=True)

    def get_workflow(self, revision_id: str) -> WorkflowRevision:
        try:
            return self._workflows[revision_id].model_copy(deep=True)
        except KeyError as exc:
            raise UnknownWorkflowError(
                f"ComfyUI workflow revision {revision_id!r} does not exist."
            ) from exc

    async def connection_status(self, profile_id: str) -> ConnectionStatus:
        transport = self._transport(profile_id)
        stats = await transport.system_stats()
        objects = await transport.object_info()
        devices = stats.get("devices", [])
        device_count = len(devices) if isinstance(devices, list) else 0
        return ConnectionStatus(
            profile_id=profile_id,
            ok=True,
            device_count=device_count,
            object_type_count=len(objects),
        )

    async def queue_status(self, profile_id: str) -> QueueSnapshot:
        return await self._transport(profile_id).queue()

    async def release_idle_models(
        self,
        *,
        profile_timeout_seconds: float | None = None,
    ) -> ComfyMemoryRelease:
        """Release caches only when every reachable configured queue is idle.

        ComfyUI is optional. A profile that cannot be reached or authenticated is recorded and
        skipped, while any observed running or queued prompt prevents `/free` on every profile.
        """
        if profile_timeout_seconds is not None and (
            not isinstance(profile_timeout_seconds, (int, float))
            or isinstance(profile_timeout_seconds, bool)
            or profile_timeout_seconds <= 0
        ):
            raise ValueError("profile_timeout_seconds must be positive")

        async def observe(
            profile: ComfyUIProfile,
        ) -> tuple[str, ComfyTransport | None, QueueSnapshot | None]:
            try:
                transport = self._transport_factory(profile)
                operation = transport.queue()
                snapshot = (
                    await operation
                    if profile_timeout_seconds is None
                    else await asyncio.wait_for(
                        operation,
                        timeout=profile_timeout_seconds,
                    )
                )
            except Exception:
                return profile.id, None, None
            return profile.id, transport, snapshot

        observations = await asyncio.gather(
            *(observe(profile) for profile in self.list_profiles())
        )
        unreachable = {
            profile_id
            for profile_id, transport, snapshot in observations
            if transport is None or snapshot is None
        }
        active = {
            profile_id
            for profile_id, _transport, snapshot in observations
            if snapshot is not None and (snapshot.running or snapshot.queued)
        }
        if active:
            return ComfyMemoryRelease(
                ready=False,
                active_profile_ids=tuple(sorted(active)),
                unreachable_profile_ids=tuple(sorted(unreachable)),
            )

        async def release(profile_id: str, transport: ComfyTransport) -> str | None:
            try:
                operation = transport.free_models_and_memory()
                if profile_timeout_seconds is None:
                    await operation
                else:
                    await asyncio.wait_for(
                        operation,
                        timeout=profile_timeout_seconds,
                    )
            except Exception:
                unreachable.add(profile_id)
                return None
            return profile_id

        released = await asyncio.gather(
            *(
                release(profile_id, transport)
                for profile_id, transport, snapshot in observations
                if transport is not None and snapshot is not None
            )
        )
        return ComfyMemoryRelease(
            ready=True,
            released_profile_ids=tuple(sorted(item for item in released if item is not None)),
            unreachable_profile_ids=tuple(sorted(unreachable)),
        )

    async def validate_compatibility(
        self, profile_id: str, workflow_revision_id: str
    ) -> WorkflowCompatibility:
        revision = self.get_workflow(workflow_revision_id)
        object_info = await self._transport(profile_id).object_info()
        missing_node_types = _missing_node_types(revision, object_info)
        bound_targets = {(binding.node_id, binding.input_name) for binding in revision.bindings}
        missing_resources = _missing_fixed_loader_resources(
            revision.workflow,
            object_info,
            ignored_targets=bound_targets,
        )
        return WorkflowCompatibility(
            profile_id=profile_id,
            workflow_revision_id=workflow_revision_id,
            compatible=not missing_node_types and not missing_resources,
            missing_node_types=missing_node_types,
            missing_resources=missing_resources,
        )

    async def submit_workflow(
        self,
        profile_id: str,
        workflow_revision_id: str,
        values: Mapping[str, Any] | None = None,
        *,
        project_id: str | None = None,
    ) -> ComfyJob:
        profile = self._profile(profile_id)
        revision = self.get_workflow(workflow_revision_id)
        rendered = revision.render(values)
        image_bindings = tuple(
            binding for binding in revision.bindings if binding.value_type == "image_asset"
        )
        if image_bindings and project_id is None:
            raise WorkflowValidationError(
                "A project_id is required when a workflow uses project media."
            )
        transport = self._transport_factory(profile)
        try:
            object_info = await transport.object_info()
        except Exception as exc:
            raise ComfyServiceError(
                f"ComfyUI compatibility preflight failed for profile {profile.id!r}; "
                "no job was created or submitted."
            ) from exc
        missing_node_types = _missing_node_types(revision, object_info)
        missing_resources = _missing_fixed_loader_resources(rendered, object_info)
        if missing_node_types or missing_resources:
            raise WorkflowIncompatibleError(
                profile.id,
                revision.id,
                missing_node_types,
                missing_resources,
            )
        input_images = await self._stage_input_images(
            image_bindings,
            rendered,
            project_id,
            transport,
        )
        if self._before_workflow_submit is not None:
            try:
                await self._before_workflow_submit()
            except Exception as exc:
                raise ComfyServiceError(
                    "Local model handoff failed; no job was created or submitted."
                ) from exc
        unique = uuid4().hex
        job = ComfyJob.new(
            job_id=f"comfy-job-{unique}",
            profile_id=profile.id,
            workflow_revision_id=revision.id,
            client_id=f"project-master-{unique}",
            project_id=project_id,
            input_images=input_images,
        )
        job = self.jobs.create(job)
        project_master_metadata = {
            "job_id": job.id,
            "workflow_revision_id": revision.id,
        }
        if job.project_id is not None:
            project_master_metadata["project_id"] = job.project_id
        try:
            submission = await transport.submit_prompt(
                rendered,
                client_id=job.client_id,
                extra_data={"project_master": project_master_metadata},
            )
        except Exception as exc:
            uncertain = job.transition(
                JobStatus.ORPHANED,
                status_detail=(
                    "Submission outcome is unknown after a transport failure; "
                    "Project Master did not resubmit it."
                ),
            )
            self.jobs.save(uncertain, expected_version=job.version)
            raise ComfyServiceError(
                f"ComfyUI submission outcome is unknown for job {job.id}; "
                "it was not automatically resubmitted."
            ) from exc

        if submission.node_errors:
            failed = job.transition(
                JobStatus.FAILED,
                remote_prompt_id=submission.prompt_id,
                error="ComfyUI rejected one or more workflow nodes.",
                status_detail=f"{len(submission.node_errors)} node error(s)",
            )
            self.jobs.save(failed, expected_version=job.version)
            raise WorkflowRejectedError(job.id)

        queued = job.transition(
            JobStatus.QUEUED,
            remote_prompt_id=submission.prompt_id,
            queue_number=submission.number,
            status_detail="Accepted by ComfyUI.",
        )
        return self.jobs.save(queued, expected_version=job.version)

    async def _stage_input_images(
        self,
        bindings: Sequence[WorkflowBinding],
        rendered: dict[str, dict[str, Any]],
        project_id: str | None,
        transport: ComfyTransport,
    ) -> tuple[ComfyInputImageProvenance, ...]:
        if not bindings:
            return ()
        if project_id is None:  # guarded before compatibility preflight
            raise WorkflowValidationError(
                "A project_id is required when a workflow uses project media."
            )
        if self._input_image_resolver is None:
            raise ComfyInputImageError(
                "Project media input is not configured; no job was created or submitted."
            )

        staged_by_content: dict[tuple[str, str], str] = {}
        provenance: list[ComfyInputImageProvenance] = []
        for binding in bindings:
            requested_asset_id = rendered[binding.node_id]["inputs"][binding.input_name]
            try:
                resolved = await asyncio.to_thread(
                    self._input_image_resolver,
                    project_id,
                    requested_asset_id,
                )
            except Exception as exc:
                raise ComfyInputImageError(
                    f"Project image for binding {binding.id!r} could not be resolved; "
                    "no job was created or submitted."
                ) from exc
            image = _validate_resolved_input_image(resolved, requested_asset_id)
            cache_key = (image.sha256, image.media_type)
            locator = staged_by_content.get(cache_key)
            if locator is None:
                filename = f"{image.sha256}{_IMAGE_EXTENSION_BY_MEDIA_TYPE[image.media_type]}"
                try:
                    uploaded = await transport.upload_image(
                        image.content,
                        filename=filename,
                        media_type=image.media_type,
                    )
                except Exception as exc:
                    raise ComfyInputImageError(
                        f"Project image for binding {binding.id!r} could not be staged; "
                        "no job was created or submitted."
                    ) from exc
                locator = uploaded.relative_locator
                staged_by_content[cache_key] = locator
            rendered[binding.node_id]["inputs"][binding.input_name] = locator
            provenance.append(
                ComfyInputImageProvenance(
                    binding_id=binding.id,
                    source_asset_id=image.asset_id,
                    source_sha256=image.sha256,
                    source_name=image.name,
                )
            )
        return tuple(provenance)

    def job_status(self, job_id: str) -> ComfyJob:
        return self.jobs.get(job_id)

    async def refresh_job(self, job_id: str) -> ComfyJob:
        job = self.jobs.get(job_id)
        retry_artifacts = (
            job.status == JobStatus.SUCCEEDED and job.artifact_status != ArtifactStatus.READY
        )
        if (job.status.terminal and not retry_artifacts) or job.remote_prompt_id is None:
            return job
        transport = self._transport(job.profile_id)
        queue = QueueSnapshot() if retry_artifacts else await transport.queue()
        history = await transport.history(job.remote_prompt_id)
        return await self._reconcile_observation(job, queue, history, transport)

    async def reconcile(self, profile_id: str) -> tuple[ComfyJob, ...]:
        self._profile(profile_id)
        transport: ComfyTransport | None = None
        queue: QueueSnapshot | None = None
        reconciled: list[ComfyJob] = []
        for job in self.jobs.list(profile_id=profile_id):
            if job.remote_prompt_id is None and job.status in {
                JobStatus.SUBMITTING,
                JobStatus.ORPHANED,
            }:
                if job.status == JobStatus.SUBMITTING:
                    uncertain = job.transition(
                        JobStatus.ORPHANED,
                        status_detail=(
                            "Recovered an ambiguous submission after restart; "
                            "Project Master did not resubmit it."
                        ),
                    )
                    job = self.jobs.save(uncertain, expected_version=job.version)
                if transport is None:
                    transport = self._transport(profile_id)
                if queue is None:
                    queue = await transport.queue()
                matches = queue.for_client(job.client_id)
                if len(matches) == 1:
                    entry = matches[0]
                    recovered = job.transition(
                        (JobStatus.RUNNING if entry.state == "running" else JobStatus.QUEUED),
                        remote_prompt_id=entry.prompt_id,
                        queue_number=entry.number,
                        status_detail=(
                            "Recovered the exact ComfyUI prompt by its unique client ID."
                        ),
                    )
                    job = self.jobs.save(recovered, expected_version=job.version)
                reconciled.append(job)
                continue
            retry_artifacts = (
                job.status == JobStatus.SUCCEEDED and job.artifact_status != ArtifactStatus.READY
            )
            if (job.status.terminal and not retry_artifacts) or job.remote_prompt_id is None:
                reconciled.append(job)
                continue
            if transport is None:
                transport = self._transport(profile_id)
            if not retry_artifacts and queue is None:
                queue = await transport.queue()
            history = await transport.history(job.remote_prompt_id)
            reconciled.append(
                await self._reconcile_observation(
                    job,
                    QueueSnapshot() if retry_artifacts else (queue or QueueSnapshot()),
                    history,
                    transport,
                )
            )
        return tuple(reconciled)

    async def cancel_job(self, job_id: str) -> ComfyJob:
        job = self.jobs.get(job_id)
        if job.status.terminal:
            return job
        if job.remote_prompt_id is None:
            cancelled = job.transition(
                JobStatus.CANCELLED,
                status_detail="Cancelled before ComfyUI accepted the prompt.",
            )
            return self.jobs.save(cancelled, expected_version=job.version)

        transport = self._transport(job.profile_id)
        queue = await transport.queue()
        entry = queue.find(job.remote_prompt_id)
        if entry is None:
            history = await transport.history(job.remote_prompt_id)
            return await self._reconcile_observation(job, queue, history, transport)
        if entry.state == "running" and (
            len(queue.running) != 1 or queue.running[0].prompt_id != job.remote_prompt_id
        ):
            raise ComfyServiceError(
                "ComfyUI interrupt is global; refusing to interrupt while another prompt "
                "is also running."
            )

        requested = job.transition(
            JobStatus.CANCEL_REQUESTED,
            queue_number=entry.number,
            status_detail="Cancellation requested.",
        )
        job = self.jobs.save(requested, expected_version=job.version)
        if entry.state == "queued":
            await transport.delete_queue_items([job.remote_prompt_id])
        elif entry.state == "running":
            # `/interrupt` is global in ComfyUI. Only use it after the queue proves this exact
            # Project Master prompt is currently running.
            await transport.interrupt()

        refreshed_queue = await transport.queue()
        history = await transport.history(job.remote_prompt_id)
        return await self._reconcile_observation(
            job,
            refreshed_queue,
            history,
            transport,
        )

    def outputs(self, job_id: str) -> tuple[OutputMetadata, ...]:
        return self.jobs.get(job_id).outputs

    def artifacts(self, job_id: str) -> tuple[ComfyArtifact, ...]:
        return self.jobs.get(job_id).artifacts

    def artifact_path(self, job_id: str, artifact_id: str) -> Path:
        if self._artifact_store is None:
            raise ComfyServiceError("ComfyUI artifact storage is not configured.")
        job = self.jobs.get(job_id)
        artifact = next(
            (item for item in job.artifacts if item.id == artifact_id),
            None,
        )
        if artifact is None:
            raise ComfyServiceError(
                f"ComfyUI artifact {artifact_id!r} does not belong to job {job_id!r}."
            )
        return self._artifact_store.path_for(artifact)

    async def events(self, profile_id: str, client_id: str) -> AsyncIterator[ComfyEvent]:
        async for event in self._transport(profile_id).events(client_id):
            yield event

    def _profile(self, profile_id: str) -> ComfyUIProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise UnknownProfileError(f"ComfyUI profile {profile_id!r} does not exist.") from exc

    def _transport(self, profile_id: str) -> ComfyTransport:
        return self._transport_factory(self._profile(profile_id))

    async def _reconcile_observation(
        self,
        job: ComfyJob,
        queue: QueueSnapshot,
        history: HistoryResult,
        transport: ComfyTransport,
    ) -> ComfyJob:
        retry_artifacts = (
            job.status == JobStatus.SUCCEEDED and job.artifact_status != ArtifactStatus.READY
        )
        if job.status.terminal and not retry_artifacts:
            return job
        if retry_artifacts and not (history.found and history.completed):
            # Remote execution success is immutable locally. A temporarily missing or
            # inconsistent history response must not downgrade a completed job.
            return job
        entry = queue.find(job.remote_prompt_id or "")
        if history.found and history.failed:
            observed = job.transition(
                JobStatus.FAILED,
                error="ComfyUI reported that the prompt failed.",
                status_detail=_bounded_detail(history.status_text),
                outputs=history.outputs,
            )
        elif history.found and history.completed:
            artifacts, artifact_status, artifact_error = await self._materialize_outputs(
                job,
                history,
                transport,
            )
            observed = job.transition(
                JobStatus.SUCCEEDED,
                status_detail=_completion_detail(
                    history.status_text,
                    artifact_status,
                    len(artifacts),
                ),
                outputs=history.outputs,
                artifacts=artifacts,
                artifact_status=artifact_status,
                artifact_error=artifact_error,
            )
        elif entry is not None and entry.state == "running":
            observed = job.transition(
                JobStatus.RUNNING,
                queue_number=entry.number,
                status_detail="Executing in ComfyUI.",
            )
        elif entry is not None:
            target = (
                JobStatus.CANCEL_REQUESTED
                if job.status == JobStatus.CANCEL_REQUESTED
                else JobStatus.QUEUED
            )
            observed = job.transition(
                target,
                queue_number=entry.number,
                status_detail=(
                    "Cancellation requested."
                    if target == JobStatus.CANCEL_REQUESTED
                    else "Queued in ComfyUI."
                ),
            )
        elif job.status == JobStatus.CANCEL_REQUESTED:
            observed = job.transition(
                JobStatus.CANCELLED,
                status_detail="Prompt no longer appears in the ComfyUI queue.",
            )
        else:
            observed = job.transition(
                JobStatus.ORPHANED,
                status_detail=(
                    _bounded_detail(history.status_text)
                    or "Prompt is absent from both ComfyUI queue and completed history."
                ),
            )
        if observed == job:
            return job
        return self.jobs.save(observed, expected_version=job.version)

    async def _materialize_outputs(
        self,
        job: ComfyJob,
        history: HistoryResult,
        transport: ComfyTransport,
    ) -> tuple[tuple[ComfyArtifact, ...], ArtifactStatus, str | None]:
        if self._artifact_store is None:
            return (
                (),
                ArtifactStatus.UNAVAILABLE,
                "ComfyUI artifact storage is not configured.",
            )
        if job.remote_prompt_id is None:
            return (
                (),
                ArtifactStatus.FAILED,
                "ComfyUI output provenance is missing its remote prompt ID.",
            )
        try:
            revision = self.get_workflow(job.workflow_revision_id)
        except UnknownWorkflowError:
            return (
                (),
                ArtifactStatus.FAILED,
                "ComfyUI output provenance is missing its workflow revision.",
            )
        history_sha256 = history.history_sha256 or _fallback_history_digest(history)
        profile = self._profile(job.profile_id)
        imported: list[ComfyArtifact] = []
        failures: list[str] = []
        for output in history.outputs:
            existing = next(
                (
                    artifact
                    for artifact in job.artifacts
                    if artifact.provenance.output == output
                    and artifact.provenance.history_sha256 == history_sha256
                ),
                None,
            )
            if existing is not None:
                try:
                    await asyncio.to_thread(self._artifact_store.path_for, existing)
                except (OSError, ValueError):
                    existing = None
                else:
                    imported.append(existing)
                    continue
            try:
                download = await transport.download_output(output)
                provenance = ComfyArtifactProvenance(
                    job_id=job.id,
                    profile_id=job.profile_id,
                    workflow_revision_id=job.workflow_revision_id,
                    workflow_digest=revision.digest,
                    remote_prompt_id=job.remote_prompt_id,
                    output=output,
                    history_sha256=history_sha256,
                    history_status=_bounded_detail(history.status_text),
                    source_base_url=profile.base_url,
                    source_url=download.source_url,
                    fetched_at=download.fetched_at,
                )
                artifact = await asyncio.to_thread(
                    self._artifact_store.store,
                    download,
                    provenance,
                )
                imported.append(artifact)
            except Exception as exc:
                failures.append(type(exc).__name__)

        imported_tuple = tuple(imported)
        if not failures:
            return imported_tuple, ArtifactStatus.READY, None
        types = ", ".join(sorted(set(failures)))
        error = (
            f"Failed to import {len(failures)} of {len(history.outputs)} "
            f"ComfyUI output(s) ({types})."
        )[:1_000]
        status = ArtifactStatus.PARTIAL if imported_tuple else ArtifactStatus.FAILED
        return imported_tuple, status, error


def _missing_node_types(
    revision: WorkflowRevision,
    object_info: Mapping[str, Any],
) -> tuple[str, ...]:
    used_types = {
        node["class_type"]
        for node in revision.workflow.values()
        if isinstance(node.get("class_type"), str)
    }
    return tuple(sorted(used_types - set(object_info)))


def _missing_fixed_loader_resources(
    workflow: Mapping[str, Mapping[str, Any]],
    object_info: Mapping[str, Any],
    *,
    ignored_targets: set[tuple[str, str]] | None = None,
) -> tuple[MissingWorkflowResource, ...]:
    ignored = ignored_targets or set()
    missing: list[MissingWorkflowResource] = []
    for node_id, node in workflow.items():
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        input_name = _LOADER_RESOURCE_INPUTS.get(class_type)
        if input_name is None or (node_id, input_name) in ignored:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        resource_name = inputs.get(input_name)
        if not isinstance(resource_name, str):
            # Connections and other dynamic values are not fixed resource claims.
            continue
        choices = _object_info_string_enum_choices(
            object_info,
            class_type,
            input_name,
        )
        if choices is None or resource_name in choices:
            continue
        missing.append(
            MissingWorkflowResource(
                node_id=node_id,
                class_type=class_type,
                input_name=input_name,
                resource_name=resource_name,
            )
        )
    return tuple(
        sorted(
            missing,
            key=lambda item: (
                item.class_type,
                item.input_name,
                item.resource_name,
                item.node_id,
            ),
        )
    )


def _object_info_string_enum_choices(
    object_info: Mapping[str, Any],
    class_type: str,
    input_name: str,
) -> frozenset[str] | None:
    node_info = object_info.get(class_type)
    if not isinstance(node_info, Mapping):
        return None
    inputs = node_info.get("input")
    if not isinstance(inputs, Mapping):
        return None
    for group_name in ("required", "optional"):
        group = inputs.get(group_name)
        if not isinstance(group, Mapping) or input_name not in group:
            continue
        input_spec = group[input_name]
        if (
            not isinstance(input_spec, Sequence)
            or isinstance(input_spec, (str, bytes, bytearray))
            or not input_spec
        ):
            return None
        enum_values = input_spec[0]
        if (
            not isinstance(enum_values, Sequence)
            or isinstance(enum_values, (str, bytes, bytearray))
            or any(not isinstance(value, str) for value in enum_values)
        ):
            return None
        return frozenset(enum_values)
    return None


def _validate_resolved_input_image(
    resolved: ResolvedInputImage,
    requested_asset_id: str,
) -> ResolvedInputImage:
    try:
        image = ResolvedInputImage.model_validate(resolved)
    except ValueError as exc:
        raise ComfyInputImageError(
            "Project media resolver returned invalid image metadata."
        ) from exc
    if image.asset_id != requested_asset_id:
        raise ComfyInputImageError("Project media resolver returned a different asset.")
    if image.kind != "image" or image.media_type not in _IMAGE_EXTENSION_BY_MEDIA_TYPE:
        raise ComfyInputImageError("Only supported project image assets can be staged.")
    if (
        image.name != image.name.strip()
        or image.name in {".", ".."}
        or "/" in image.name
        or "\\" in image.name
        or any(ord(character) < 32 or ord(character) == 127 for character in image.name)
    ):
        raise ComfyInputImageError("Project image has an invalid source name.")
    if image.size_bytes > MAX_COMFY_INPUT_IMAGE_BYTES:
        raise ComfyInputImageError("Project image exceeds ComfyUI's 50 MiB input limit.")
    if len(image.content) != image.size_bytes:
        raise ComfyInputImageError("Project image size does not match its verified metadata.")
    if hashlib.sha256(image.content).hexdigest() != image.sha256:
        raise ComfyInputImageError("Project image failed SHA-256 verification.")
    if image.width is None or image.height is None:
        raise ComfyInputImageError("Project image dimensions are required for ComfyUI staging.")
    if image.width > MAX_COMFY_INPUT_IMAGE_EDGE or image.height > MAX_COMFY_INPUT_IMAGE_EDGE:
        raise ComfyInputImageError("Project image exceeds ComfyUI's 16384-pixel edge limit.")
    if image.width * image.height > MAX_COMFY_INPUT_IMAGE_PIXELS:
        raise ComfyInputImageError("Project image exceeds ComfyUI's 64-megapixel limit.")
    return image


def _bounded_detail(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:500] or None


def _completion_detail(
    history_status: str | None,
    artifact_status: ArtifactStatus,
    artifact_count: int,
) -> str:
    remote = _bounded_detail(history_status) or "Completed."
    if artifact_status == ArtifactStatus.READY:
        local = f"{artifact_count} artifact(s) imported."
    elif artifact_status == ArtifactStatus.PARTIAL:
        local = f"{artifact_count} artifact(s) imported; remaining imports need retry."
    elif artifact_status == ArtifactStatus.UNAVAILABLE:
        local = "Artifact storage needs runtime configuration."
    else:
        local = "Artifact import needs retry."
    return f"{remote} {local}"[:500]


def _fallback_history_digest(history: HistoryResult) -> str:
    payload = history.model_dump(
        mode="json",
        exclude={"history_sha256"},
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
