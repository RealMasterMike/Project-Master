from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from project_master.integrations.voice import (
    ConsentRecord,
    ConsentScope,
    EspeakNgAdapter,
    RenderPurpose,
    ResourceLease,
    RightsBasis,
    ScriptBlock,
    SQLiteVoiceStore,
    VoiceProfile,
    VoiceProject,
    VoiceResourceRequest,
    VoiceStudioService,
    discover_espeak_pack,
)
from project_master.integrations.voice.jobs import RenderJobStatus
from project_master.memory.store import SQLiteStore

NOW = datetime(2026, 7, 27, tzinfo=UTC)


class LeaseProvider:
    async def acquire(
        self,
        _request: VoiceResourceRequest,
        *,
        owner_id: str,
    ) -> ResourceLease:
        return ResourceLease(
            id=f"lease-{owner_id}",
            owner_id=owner_id,
            resource_id="test-cpu",
            acquired_at=datetime.now(UTC),
        )

    async def release(self, _lease: ResourceLease) -> None:
        return None


def designed_profile() -> VoiceProfile:
    return VoiceProfile.create(
        profile_id="narrator",
        name="Local narrator",
        mode="designed",
        language="en-US",
        description="voice=en-us+f3; pitch=55; amplitude=110",
        consent=ConsentRecord(
            id="consent-narrator",
            basis=RightsBasis.SYNTHETIC_DESIGN,
            scopes=(ConsentScope.VOICE_GENERATION,),
            subject_label="Synthetic local voice",
            attested_by_user=True,
            granted_at=NOW,
        ),
        created_at=NOW,
    )


def voice_project() -> VoiceProject:
    return VoiceProject.create(
        project_id="daily-brief",
        name="Daily brief",
        language="en-US",
        default_voice_profile_id="narrator",
        blocks=(
            ScriptBlock(
                id="intro",
                text="Project Master voice output is working locally.",
                speed=1.1,
                pause_after_ms=100,
            ),
        ),
        created_at=NOW,
    )


@pytest.mark.skipif(
    discover_espeak_pack() is None,
    reason="The system eSpeak NG plus ffmpeg fallback is not installed.",
)
def test_espeak_render_and_all_state_survive_restart(tmp_path) -> None:
    database_path = tmp_path / "master.db"
    storage = SQLiteVoiceStore(
        SQLiteStore(database_path),
        tmp_path / "voice-artifacts",
    )
    profile = storage.save_profile(designed_profile())
    project = storage.save_project(voice_project())
    adapter = EspeakNgAdapter()
    pack = discover_espeak_pack(adapter)
    assert pack is not None
    storage.upsert_pack(pack)
    service = VoiceStudioService(
        profiles=storage.list_profiles(),
        projects=storage.list_projects(),
        packs=storage.list_packs(),
        adapters={"espeak-ng": adapter},
        resource_leases=LeaseProvider(),
        resource_requests={
            pack.id: VoiceResourceRequest(kind="cpu", minimum_memory_mb=64)
        },
        jobs=storage.jobs,
        cache=storage.cache,
        artifacts=storage.artifacts,
    )

    async def render() -> str:
        health = await service.engine_health(pack.id)
        assert health.available is True
        job = service.create_render_job(
            project_id=project.id,
            engine_pack_id=pack.id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        completed = await service.run_job(job.id)
        assert completed.status is RenderJobStatus.SUCCEEDED
        return completed.artifact_ids[0]

    artifact_id = asyncio.run(render())
    artifact = storage.get_artifact(artifact_id)
    assert artifact is not None
    assert artifact.media_type == "audio/wav"
    assert artifact.sample_rate_hz == 24_000
    assert storage.read_artifact(artifact_id).startswith(b"RIFF")

    restarted = SQLiteVoiceStore(
        SQLiteStore(database_path),
        tmp_path / "voice-artifacts",
    )
    assert restarted.list_profiles() == (profile,)
    assert restarted.list_projects() == (project,)
    assert restarted.list_packs() == (pack,)
    assert restarted.list_jobs()[0].status is RenderJobStatus.SUCCEEDED
    assert restarted.get_artifact(artifact_id) == artifact
