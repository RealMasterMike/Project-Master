import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
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
    ComfyServiceError,
    ComfyUIService,
    WorkflowRejectedError,
)
from project_master.integrations.comfyui.transport import (
    ComfyEvent,
    DownloadedOutput,
    HistoryResult,
    OutputMetadata,
    OutputRef,
    PromptSubmission,
    QueueEntry,
    QueueSnapshot,
)
from project_master.integrations.comfyui.workflow import WorkflowBinding


class FakeTransport:
    def __init__(self) -> None:
        self.snapshot = QueueSnapshot()
        self.histories: dict[str, HistoryResult] = {}
        self.next_submission = PromptSubmission(prompt_id="prompt-1", number=4)
        self.submitted_workflow: Mapping[str, Any] | None = None
        self.submitted_extra: Mapping[str, Any] | None = None
        self.deleted: list[str] = []
        self.interrupt_count = 0
        self.submit_count = 0
        self.fail_submission = False
        self.download_failures: set[str] = set()
        self.download_count: dict[str, int] = {}

    async def system_stats(self) -> Mapping[str, Any]:
        return {"devices": [{"name": "fake-gpu"}]}

    async def object_info(self) -> Mapping[str, Any]:
        return {"KSampler": {}, "SaveImage": {}}

    async def queue(self) -> QueueSnapshot:
        return self.snapshot

    async def submit_prompt(
        self,
        workflow: Mapping[str, Any],
        *,
        client_id: str,
        extra_data: Mapping[str, Any] | None = None,
    ) -> PromptSubmission:
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


def make_service(
    *,
    artifact_store: FilesystemComfyArtifactStore | None = None,
    jobs: InMemoryJobRepository | None = None,
) -> tuple[ComfyUIService, FakeTransport, str]:
    transport = FakeTransport()
    service = ComfyUIService(
        [ComfyUIProfile(id="local", name="Local")],
        lambda _profile: transport,
        jobs=jobs,
        artifact_store=artifact_store,
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
        assert status.object_type_count == 2
        compatibility = await service.validate_compatibility("local", revision_id)
        assert not compatibility.compatible
        assert compatibility.missing_node_types == ("CLIPTextEncode",)

        job = await service.submit_workflow("local", revision_id, {"prompt": "a moonlit city"})
        assert job.status == JobStatus.QUEUED
        assert job.remote_prompt_id == "prompt-1"
        assert transport.submitted_workflow is not None
        assert transport.submitted_workflow["1"]["inputs"]["text"] == "a moonlit city"
        assert transport.submitted_extra is not None
        provenance = transport.submitted_extra["project_master"]
        assert provenance["job_id"] == job.id
        assert provenance["workflow_revision_id"] == revision_id

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
