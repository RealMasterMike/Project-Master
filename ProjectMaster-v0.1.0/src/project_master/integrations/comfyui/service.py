from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from project_master.integrations.comfyui.artifacts import (
    ComfyArtifact,
    ComfyArtifactProvenance,
    ComfyArtifactStore,
)
from project_master.integrations.comfyui.jobs import (
    ArtifactStatus,
    ComfyJob,
    InMemoryJobRepository,
    JobRepository,
    JobStatus,
)
from project_master.integrations.comfyui.profiles import ComfyUIProfile
from project_master.integrations.comfyui.transport import (
    ComfyEvent,
    ComfyTransport,
    HistoryResult,
    OutputMetadata,
    QueueSnapshot,
)
from project_master.integrations.comfyui.workflow import WorkflowBinding, WorkflowRevision

TransportFactory = Callable[[ComfyUIProfile], ComfyTransport]


class ComfyServiceError(RuntimeError):
    pass


class UnknownProfileError(ComfyServiceError):
    pass


class UnknownWorkflowError(ComfyServiceError):
    pass


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
    ) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("ComfyUI profile IDs must be unique.")
        self._transport_factory = transport_factory
        self.jobs = jobs or InMemoryJobRepository()
        self._artifact_store = artifact_store
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
    ) -> WorkflowRevision:
        revision = WorkflowRevision.import_json(name, source, tuple(bindings))
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

    async def validate_compatibility(
        self, profile_id: str, workflow_revision_id: str
    ) -> WorkflowCompatibility:
        revision = self.get_workflow(workflow_revision_id)
        object_info = await self._transport(profile_id).object_info()
        used_types = {
            node["class_type"]
            for node in revision.workflow.values()
            if isinstance(node.get("class_type"), str)
        }
        missing = tuple(sorted(used_types - set(object_info)))
        return WorkflowCompatibility(
            profile_id=profile_id,
            workflow_revision_id=workflow_revision_id,
            compatible=not missing,
            missing_node_types=missing,
        )

    async def submit_workflow(
        self,
        profile_id: str,
        workflow_revision_id: str,
        values: Mapping[str, Any] | None = None,
    ) -> ComfyJob:
        profile = self._profile(profile_id)
        revision = self.get_workflow(workflow_revision_id)
        rendered = revision.render(values)
        unique = uuid4().hex
        job = ComfyJob.new(
            job_id=f"comfy-job-{unique}",
            profile_id=profile.id,
            workflow_revision_id=revision.id,
            client_id=f"project-master-{unique}",
        )
        job = self.jobs.create(job)
        transport = self._transport_factory(profile)
        try:
            submission = await transport.submit_prompt(
                rendered,
                client_id=job.client_id,
                extra_data={
                    "project_master": {
                        "job_id": job.id,
                        "workflow_revision_id": revision.id,
                    }
                },
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
