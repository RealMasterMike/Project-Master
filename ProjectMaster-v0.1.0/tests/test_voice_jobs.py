from datetime import UTC, datetime

import pytest

from project_master.integrations.voice.cache import VoiceChunkPlan
from project_master.integrations.voice.jobs import (
    InMemoryRenderJobRepository,
    RenderJob,
    RenderJobConflictError,
    RenderJobStateError,
    RenderJobStatus,
)
from project_master.integrations.voice.profiles import RenderPurpose
from project_master.integrations.voice.projects import RenderSettings

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def plan() -> VoiceChunkPlan:
    raw = {
        "schema_version": 1,
        "ordinal": 0,
        "block_id": "block-1",
        "block_chunk_index": 0,
        "text": "Hello",
        "language": "en",
        "voice_profile_id": "voice-1",
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
        "seed": 7,
        "normalize_loudness": True,
    }
    provisional = VoiceChunkPlan.model_construct(id="", cache_key="", **raw)
    return VoiceChunkPlan(
        id=f"voice-chunk-{provisional._instance_digest()[:32]}",
        cache_key=f"voice-cache-{provisional._cache_digest()[:32]}",
        **raw,
    )


def job() -> RenderJob:
    return RenderJob.new(
        job_id="voice-job-1",
        project_id="project-1",
        project_digest="2" * 64,
        engine_pack_id="pack-1",
        engine_pack_digest="3" * 64,
        purpose=RenderPurpose.PRIVATE,
        settings=RenderSettings(),
        plans=(plan(),),
        now=NOW,
    )


def test_job_repository_state_and_snapshot_round_trip() -> None:
    repository = InMemoryRenderJobRepository()
    original = repository.create(job())
    waiting = original.transition(RenderJobStatus.WAITING_RESOURCE, now=NOW)
    saved = repository.save(waiting, expected_version=original.version)

    assert saved.version == 2
    assert saved.status == RenderJobStatus.WAITING_RESOURCE
    with pytest.raises(RenderJobConflictError):
        repository.save(waiting, expected_version=original.version)
    restored = InMemoryRenderJobRepository.restore_snapshot(
        repository.export_snapshot()
    )
    assert restored.get(original.id) == saved


def test_job_completion_requires_completed_chunks_and_terminal_is_final() -> None:
    render_job = job().transition(RenderJobStatus.WAITING_RESOURCE, now=NOW)
    render_job = render_job.transition(RenderJobStatus.RUNNING, now=NOW)
    with pytest.raises(ValueError, match="incomplete"):
        render_job.transition(RenderJobStatus.SUCCEEDED, now=NOW)

    chunk = render_job.chunks[0].running(now=NOW).completed(
        "artifact-1", cached=False, now=NOW
    )
    completed = render_job.replace_chunk(chunk).transition(
        RenderJobStatus.SUCCEEDED, now=NOW
    )
    with pytest.raises(RenderJobStateError):
        completed.transition(RenderJobStatus.RUNNING, now=NOW)


def test_interruption_resets_inflight_chunk_for_explicit_resume() -> None:
    render_job = job().transition(RenderJobStatus.WAITING_RESOURCE, now=NOW)
    render_job = render_job.transition(RenderJobStatus.RUNNING, now=NOW)
    render_job = render_job.replace_chunk(render_job.chunks[0].running(now=NOW))

    interrupted = render_job.transition(RenderJobStatus.INTERRUPTED, now=NOW)

    assert interrupted.chunks[0].status == "pending"
    assert interrupted.chunks[0].attempts == 1
