import asyncio
from datetime import UTC, datetime

import pytest

from project_master.integrations.voice.engine import (
    CancellationAck,
    EngineHealth,
    EngineRecovery,
    EngineRenderRequest,
    RenderedAudio,
)
from project_master.integrations.voice.jobs import RenderJobStatus
from project_master.integrations.voice.manifests import (
    CHATTERBOX_PACK_TEMPLATE,
    QWEN3_TTS_PACK_TEMPLATE,
    EngineCapability,
    InstalledEnginePack,
    ModelAsset,
    ModelAssetFormat,
)
from project_master.integrations.voice.profiles import (
    ConsentRecord,
    ConsentScope,
    RenderPurpose,
    RightsBasis,
    VoiceProfile,
    VoiceReference,
    VoiceRightsError,
)
from project_master.integrations.voice.projects import (
    RenderSettings,
    ScriptBlock,
    VoiceProject,
)
from project_master.integrations.voice.resources import (
    ResourceLease,
    VoiceResourceRequest,
)
from project_master.integrations.voice.service import (
    VoiceCompatibilityError,
    VoiceStudioService,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def model_assets() -> tuple[ModelAsset, ...]:
    return (
        ModelAsset(
            logical_name="model_weights",
            relative_path="model.safetensors",
            format=ModelAssetFormat.SAFETENSORS,
            sha256="1" * 64,
            size_bytes=100,
        ),
        ModelAsset(
            logical_name="model_config",
            relative_path="config.json",
            format=ModelAssetFormat.JSON,
            sha256="2" * 64,
            size_bytes=100,
        ),
        ModelAsset(
            logical_name="tokenizer",
            relative_path="tokenizer.json",
            format=ModelAssetFormat.TOKENIZER_JSON,
            sha256="3" * 64,
            size_bytes=100,
        ),
    )


def pack(
    template=QWEN3_TTS_PACK_TEMPLATE,
) -> InstalledEnginePack:
    assets = model_assets()
    if template is not QWEN3_TTS_PACK_TEMPLATE:
        extensions = {
            ModelAssetFormat.SAFETENSORS: ".safetensors",
            ModelAssetFormat.PYTORCH_CHECKPOINT: ".pt",
            ModelAssetFormat.JSON: ".json",
            ModelAssetFormat.TOKENIZER_JSON: ".json",
            ModelAssetFormat.SENTENCEPIECE: ".model",
        }
        assets = tuple(
            ModelAsset(
                logical_name=requirement.logical_name,
                relative_path=(
                    f"{requirement.logical_name}"
                    f"{extensions[requirement.allowed_formats[0]]}"
                ),
                format=requirement.allowed_formats[0],
                sha256=f"{index:x}" * 64,
                size_bytes=100,
            )
            for index, requirement in enumerate(
                template.asset_requirements,
                start=1,
            )
        )
    return InstalledEnginePack.from_template(
        template,
        installed_version="test-version",
        assets=assets,
    )


def profile(
    *,
    scopes: tuple[ConsentScope, ...] = (ConsentScope.VOICE_GENERATION,),
    basis: RightsBasis = RightsBasis.SELF_VOICE,
) -> VoiceProfile:
    consent = ConsentRecord(
        id="consent-1",
        basis=basis,
        scopes=scopes,
        subject_label="Owner",
        attested_by_user=True,
        granted_at=NOW,
    )
    return VoiceProfile.create(
        profile_id="voice-1",
        name="Voice",
        mode="reference",
        language="en",
        consent=consent,
        references=(
            VoiceReference(
                artifact_id="reference-1",
                sha256="a" * 64,
                media_type="audio/wav",
                duration_seconds=10,
                sample_rate_hz=24_000,
                channels=1,
            ),
        ),
        created_at=NOW,
    )


def project() -> VoiceProject:
    return VoiceProject.create(
        project_id="project-1",
        name="Daily narration",
        language="en",
        default_voice_profile_id="voice-1",
        blocks=(
            ScriptBlock(
                id="block-1",
                text="This is a deterministic Voice Studio render.",
            ),
        ),
        created_at=NOW,
    )


class FakeEngine:
    engine_id = "qwen3-tts"
    capabilities = frozenset(QWEN3_TTS_PACK_TEMPLATE.capabilities)
    max_chunk_characters = 500

    def __init__(self) -> None:
        self.requests: list[EngineRenderRequest] = []
        self.cancel_ack = CancellationAck(
            accepted=True, confirmed=True, detail="cancelled"
        )
        self.recovery = EngineRecovery(status="not_found")
        self.started = asyncio.Event()
        self.release_render = asyncio.Event()
        self.block = False
        self.output_sample_rate: int | None = None

    async def health(self, _pack: InstalledEnginePack) -> EngineHealth:
        return EngineHealth(available=True, status="ready")

    async def render_chunk(self, request: EngineRenderRequest) -> RenderedAudio:
        self.requests.append(request)
        self.started.set()
        if self.block:
            await self.release_render.wait()
        return RenderedAudio(
            content=f"audio:{request.chunk.cache_key}".encode(),
            format=request.chunk.output_format,
            media_type="audio/wav",
            sample_rate_hz=self.output_sample_rate or request.chunk.sample_rate_hz,
            channels=request.chunk.channels,
            duration_seconds=1.25,
            engine_run_id=f"engine-{request.chunk.id}",
        )

    async def cancel(self, _job_id: str) -> CancellationAck:
        self.release_render.set()
        return self.cancel_ack

    async def recover(self, _job_id: str) -> EngineRecovery:
        return self.recovery


class FakeLeaseProvider:
    def __init__(self) -> None:
        self.acquired: list[tuple[VoiceResourceRequest, str]] = []
        self.released: list[ResourceLease] = []

    async def acquire(
        self, request: VoiceResourceRequest, *, owner_id: str
    ) -> ResourceLease:
        self.acquired.append((request, owner_id))
        return ResourceLease(
            id=f"lease-{owner_id}",
            owner_id=owner_id,
            resource_id="fake-gpu",
            acquired_at=datetime.now(UTC),
        )

    async def release(self, lease: ResourceLease) -> None:
        self.released.append(lease)


def make_service(
    *,
    voice: VoiceProfile | None = None,
    engine: FakeEngine | None = None,
) -> tuple[VoiceStudioService, FakeEngine, FakeLeaseProvider]:
    fake_engine = engine or FakeEngine()
    leases = FakeLeaseProvider()
    service = VoiceStudioService(
        profiles=[voice or profile()],
        projects=[project()],
        packs=[pack()],
        adapters={"qwen3-tts": fake_engine},
        resource_leases=leases,
    )
    return service, fake_engine, leases


def test_render_uses_lease_adapter_and_records_verified_provenance() -> None:
    service, engine, leases = make_service()

    async def exercise() -> None:
        health = await service.engine_health(pack().id)
        assert health.available
        job = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            settings=RenderSettings(format="wav", sample_rate_hz=24_000),
            now=NOW,
        )
        completed = await service.run_job(job.id)
        assert completed.status == RenderJobStatus.SUCCEEDED
        assert len(completed.artifact_ids) == 1
        artifact = service.artifact(completed.artifact_ids[0])
        assert artifact is not None
        assert artifact.verified
        assert artifact.provenance.voice_profile_id == "voice-1"
        assert artifact.provenance.model_asset_digests == (
            "1" * 64,
            "2" * 64,
            "3" * 64,
        )
        assert engine.requests[0].reference_artifact_ids == ("reference-1",)

    asyncio.run(exercise())
    assert len(leases.acquired) == 1
    assert len(leases.released) == 1


def test_render_preserves_synthetic_reference_provenance() -> None:
    service, _engine, _leases = make_service(
        voice=profile(basis=RightsBasis.SYNTHETIC_REFERENCE)
    )

    async def exercise() -> None:
        job = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        completed = await service.run_job(job.id)
        artifact = service.artifact(completed.artifact_ids[0])
        assert artifact is not None
        assert artifact.provenance.rights_basis == RightsBasis.SYNTHETIC_REFERENCE

    asyncio.run(exercise())


def test_second_identical_render_is_cache_only_and_skips_resource_lease() -> None:
    service, engine, leases = make_service()

    async def exercise() -> None:
        first = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        await service.run_job(first.id)
        second = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        completed = await service.run_job(second.id)
        assert completed.status == RenderJobStatus.SUCCEEDED
        assert completed.chunks[0].status == "cached"

    asyncio.run(exercise())
    assert len(engine.requests) == 1
    assert len(leases.acquired) == 1


def test_commercial_render_is_blocked_without_commercial_rights() -> None:
    service, _engine, _leases = make_service()

    with pytest.raises(VoiceRightsError, match="commercial_use"):
        service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.COMMERCIAL,
            now=NOW,
        )


def test_pack_and_adapter_capabilities_must_agree() -> None:
    engine = FakeEngine()
    engine.capabilities = frozenset({EngineCapability.REFERENCE_VOICE})
    with pytest.raises(VoiceCompatibilityError, match="every declared"):
        VoiceStudioService(
            profiles=[profile()],
            projects=[project()],
            packs=[pack()],
            adapters={"qwen3-tts": engine},
            resource_leases=FakeLeaseProvider(),
        )


def test_running_render_can_be_cancelled_and_releases_lease() -> None:
    engine = FakeEngine()
    engine.block = True
    service, _engine, leases = make_service(engine=engine)

    async def exercise() -> None:
        job = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        worker = asyncio.create_task(service.run_job(job.id))
        await asyncio.wait_for(engine.started.wait(), timeout=1)
        cancelled, ack = await service.cancel_job(job.id)
        assert ack is not None and ack.confirmed
        assert cancelled.status == RenderJobStatus.CANCELLED
        result = await asyncio.wait_for(worker, timeout=1)
        assert result.status == RenderJobStatus.CANCELLED

    asyncio.run(exercise())
    assert len(leases.released) == 1


def test_recovery_marks_inflight_job_interrupted_and_resets_chunk() -> None:
    service, engine, _leases = make_service()

    async def exercise() -> None:
        job = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            now=NOW,
        )
        waiting = job.transition(RenderJobStatus.WAITING_RESOURCE, now=NOW)
        job = service.jobs.save(waiting, expected_version=job.version)
        running = job.transition(RenderJobStatus.RUNNING, now=NOW)
        job = service.jobs.save(running, expected_version=job.version)
        active = job.replace_chunk(job.chunks[0].running(now=NOW))
        service.jobs.save(active, expected_version=job.version)

        engine.recovery = EngineRecovery(
            status="not_found", detail="No active engine process."
        )
        recovered = await service.recover_jobs()
        assert recovered[0].status == RenderJobStatus.INTERRUPTED
        assert recovered[0].chunks[0].status == "pending"
        assert recovered[0].error == "No active engine process."

    asyncio.run(exercise())


def test_output_contract_mismatch_fails_honestly() -> None:
    engine = FakeEngine()
    engine.output_sample_rate = 48_000
    service, _engine, leases = make_service(engine=engine)

    async def exercise() -> None:
        job = service.create_render_job(
            project_id="project-1",
            engine_pack_id=pack().id,
            purpose=RenderPurpose.PRIVATE,
            settings=RenderSettings(sample_rate_hz=24_000),
            now=NOW,
        )
        failed = await service.run_job(job.id)
        assert failed.status == RenderJobStatus.FAILED
        assert "VoiceStudioError" in (failed.error or "")

    asyncio.run(exercise())
    assert len(leases.released) == 1


def test_chatterbox_template_cannot_claim_voice_design_capability() -> None:
    chatterbox_pack = pack(CHATTERBOX_PACK_TEMPLATE)
    assert EngineCapability.VOICE_DESIGN not in chatterbox_pack.capabilities
