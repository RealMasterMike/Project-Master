from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from project_master.integrations.voice.artifacts import (
    InMemoryVoiceArtifactStore,
    VoiceArtifact,
    VoiceArtifactProvenance,
    VoiceArtifactStore,
)
from project_master.integrations.voice.cache import (
    ChunkCache,
    InMemoryChunkCache,
    build_chunk_plan,
    cache_entry,
)
from project_master.integrations.voice.engine import (
    CancellationAck,
    EngineAdapter,
    EngineHealth,
    EngineRenderRequest,
    RenderedAudio,
)
from project_master.integrations.voice.jobs import (
    InMemoryRenderJobRepository,
    RenderChunkStatus,
    RenderJob,
    RenderJobRepository,
    RenderJobStatus,
)
from project_master.integrations.voice.manifests import (
    EngineCapability,
    InstalledEnginePack,
)
from project_master.integrations.voice.profiles import (
    RenderPurpose,
    VoiceProfile,
)
from project_master.integrations.voice.projects import RenderSettings, VoiceProject
from project_master.integrations.voice.resources import (
    ResourceLease,
    ResourceLeaseProvider,
    VoiceResourceRequest,
)


class VoiceStudioError(RuntimeError):
    pass


class VoiceCompatibilityError(VoiceStudioError):
    pass


class VoiceStudioService:
    """Typed orchestration boundary; no engines, tools, or downloads are registered here."""

    def __init__(
        self,
        *,
        profiles: Sequence[VoiceProfile],
        projects: Sequence[VoiceProject],
        packs: Sequence[InstalledEnginePack],
        adapters: Mapping[str, EngineAdapter],
        resource_leases: ResourceLeaseProvider,
        resource_requests: Mapping[str, VoiceResourceRequest] | None = None,
        jobs: RenderJobRepository | None = None,
        cache: ChunkCache | None = None,
        artifacts: VoiceArtifactStore | None = None,
    ) -> None:
        self._profiles = _unique_by_id(profiles, "voice profile")
        self._projects = _unique_by_id(projects, "voice project")
        self._packs = _unique_by_id(packs, "voice engine pack")
        self._adapters = dict(adapters)
        self._resource_leases = resource_leases
        self._resource_requests = dict(resource_requests or {})
        self.jobs = jobs or InMemoryRenderJobRepository()
        self.cache = cache or InMemoryChunkCache()
        self.artifacts = artifacts or InMemoryVoiceArtifactStore()
        for pack in self._packs.values():
            self._validate_adapter(pack)

    def list_profiles(self) -> tuple[VoiceProfile, ...]:
        return tuple(sorted(self._profiles.values(), key=lambda item: item.id))

    def list_projects(self) -> tuple[VoiceProject, ...]:
        return tuple(sorted(self._projects.values(), key=lambda item: item.id))

    def list_packs(self) -> tuple[InstalledEnginePack, ...]:
        return tuple(sorted(self._packs.values(), key=lambda item: item.id))

    def upsert_profile(self, profile: VoiceProfile) -> None:
        self._profiles[profile.id] = VoiceProfile.model_validate(profile.model_dump())

    def upsert_project(self, project: VoiceProject) -> None:
        self._projects[project.id] = VoiceProject.model_validate(project.model_dump())

    def upsert_pack(
        self,
        pack: InstalledEnginePack,
        *,
        adapter: EngineAdapter | None = None,
        resource_request: VoiceResourceRequest | None = None,
    ) -> None:
        if adapter is not None:
            self._adapters[pack.engine_id] = adapter
        self._packs[pack.id] = InstalledEnginePack.model_validate(pack.model_dump())
        if resource_request is not None:
            self._resource_requests[pack.id] = resource_request
        self._validate_adapter(pack)

    async def engine_health(self, pack_id: str) -> EngineHealth:
        pack = self._pack(pack_id)
        return await self._adapter(pack).health(pack)

    def create_render_job(
        self,
        *,
        project_id: str,
        engine_pack_id: str,
        purpose: RenderPurpose,
        settings: RenderSettings | None = None,
        now: datetime | None = None,
    ) -> RenderJob:
        timestamp = now or datetime.now(UTC)
        project = self._project(project_id)
        pack = self._pack(engine_pack_id)
        adapter = self._adapter(pack)
        self._assert_project_authorized(project, pack, adapter, purpose, timestamp)
        effective_settings = settings or RenderSettings()
        plans = build_chunk_plan(
            project,
            self._profiles,
            pack,
            effective_settings,
            engine_max_characters=adapter.max_chunk_characters,
        )
        job = RenderJob.new(
            job_id=f"voice-job-{uuid4().hex}",
            project_id=project.id,
            project_digest=project.digest,
            engine_pack_id=pack.id,
            engine_pack_digest=pack.digest,
            purpose=purpose,
            settings=effective_settings,
            plans=plans,
            now=timestamp,
        )
        return self.jobs.create(job)

    def job_status(self, job_id: str) -> RenderJob:
        return self.jobs.get(job_id)

    async def run_job(self, job_id: str) -> RenderJob:
        job = self.jobs.get(job_id)
        if job.status.terminal:
            return job
        if job.status == RenderJobStatus.CANCEL_REQUESTED:
            return self._save_transition(job, RenderJobStatus.CANCELLED)
        if job.status not in {
            RenderJobStatus.PLANNED,
            RenderJobStatus.INTERRUPTED,
            RenderJobStatus.WAITING_RESOURCE,
        }:
            raise VoiceStudioError(f"Voice job {job.id} is already running.")

        project = self._project(job.project_id)
        pack = self._pack(job.engine_pack_id)
        adapter = self._adapter(pack)
        self._verify_job_contract(job, project, pack)
        self._assert_project_authorized(
            project, pack, adapter, job.purpose, datetime.now(UTC)
        )
        if job.status != RenderJobStatus.WAITING_RESOURCE:
            job = self._save_transition(job, RenderJobStatus.WAITING_RESOURCE)
        job = self._apply_cache_hits(job)
        if all(chunk.status.complete for chunk in job.chunks):
            return self._save_transition(job, RenderJobStatus.SUCCEEDED)

        request = self._resource_requests.get(pack.id, VoiceResourceRequest())
        lease: ResourceLease | None = None
        try:
            try:
                lease = await self._resource_leases.acquire(request, owner_id=job.id)
            except Exception as exc:
                current = self.jobs.get(job.id)
                interrupted = current.transition(
                    RenderJobStatus.INTERRUPTED,
                    error=f"Resource lease failed ({type(exc).__name__}).",
                )
                return self.jobs.save(interrupted, expected_version=current.version)

            current = self.jobs.get(job.id)
            if current.status in {
                RenderJobStatus.CANCEL_REQUESTED,
                RenderJobStatus.CANCELLED,
            }:
                if current.status == RenderJobStatus.CANCEL_REQUESTED:
                    current = self._save_transition(current, RenderJobStatus.CANCELLED)
                return current
            job = self._save_transition(
                current, RenderJobStatus.RUNNING, lease_id=lease.id
            )

            for initial_chunk in job.chunks:
                current = self.jobs.get(job.id)
                if current.status in {
                    RenderJobStatus.CANCEL_REQUESTED,
                    RenderJobStatus.CANCELLED,
                }:
                    if current.status == RenderJobStatus.CANCEL_REQUESTED:
                        current = self._save_transition(
                            current, RenderJobStatus.CANCELLED
                        )
                    return current
                chunk = next(
                    item
                    for item in current.chunks
                    if item.plan.id == initial_chunk.plan.id
                )
                if chunk.status.complete:
                    continue
                running_chunk = chunk.running()
                updated = current.replace_chunk(running_chunk)
                job = self.jobs.save(updated, expected_version=current.version)
                profile = self._profile(running_chunk.plan.voice_profile_id)
                engine_request = self._engine_request(job, running_chunk.plan, pack, profile)
                try:
                    audio = await adapter.render_chunk(engine_request)
                    self._validate_audio(audio, running_chunk.plan)
                except Exception as exc:
                    current = self.jobs.get(job.id)
                    if current.status in {
                        RenderJobStatus.CANCEL_REQUESTED,
                        RenderJobStatus.CANCELLED,
                    }:
                        if current.status == RenderJobStatus.CANCEL_REQUESTED:
                            current = self._save_transition(
                                current, RenderJobStatus.CANCELLED
                            )
                        return current
                    failed_chunk = next(
                        item
                        for item in current.chunks
                        if item.plan.id == running_chunk.plan.id
                    ).failed(f"Voice engine failed ({type(exc).__name__}).")
                    current = self.jobs.save(
                        current.replace_chunk(failed_chunk),
                        expected_version=current.version,
                    )
                    return self._save_transition(
                        current,
                        RenderJobStatus.FAILED,
                        error=f"Voice render failed ({type(exc).__name__}).",
                    )

                current = self.jobs.get(job.id)
                if current.status in {
                    RenderJobStatus.CANCEL_REQUESTED,
                    RenderJobStatus.CANCELLED,
                }:
                    if current.status == RenderJobStatus.CANCEL_REQUESTED:
                        current = self._save_transition(
                            current, RenderJobStatus.CANCELLED
                        )
                    return current
                active_chunk = next(
                    item
                    for item in current.chunks
                    if item.plan.id == running_chunk.plan.id
                )
                try:
                    artifact = self.artifacts.store(
                        audio, self._provenance(active_chunk.plan, pack, profile)
                    )
                    self.cache.put(cache_entry(active_chunk.plan.cache_key, artifact.id))
                    completed = active_chunk.completed(
                        artifact.id,
                        cached=False,
                        engine_run_id=audio.engine_run_id,
                    )
                    job = self.jobs.save(
                        current.replace_chunk(completed),
                        expected_version=current.version,
                    )
                except Exception as exc:
                    current = self.jobs.get(job.id)
                    if current.status in {
                        RenderJobStatus.CANCEL_REQUESTED,
                        RenderJobStatus.CANCELLED,
                    }:
                        if current.status == RenderJobStatus.CANCEL_REQUESTED:
                            current = self._save_transition(
                                current, RenderJobStatus.CANCELLED
                            )
                        return current
                    failed_chunk = next(
                        item
                        for item in current.chunks
                        if item.plan.id == running_chunk.plan.id
                    ).failed(f"Voice artifact persistence failed ({type(exc).__name__}).")
                    current = self.jobs.save(
                        current.replace_chunk(failed_chunk),
                        expected_version=current.version,
                    )
                    return self._save_transition(
                        current,
                        RenderJobStatus.FAILED,
                        error=f"Voice artifact persistence failed ({type(exc).__name__}).",
                    )

            latest = self.jobs.get(job.id)
            return self._save_transition(latest, RenderJobStatus.SUCCEEDED)
        finally:
            if lease is not None:
                await self._resource_leases.release(lease)

    async def cancel_job(self, job_id: str) -> tuple[RenderJob, CancellationAck | None]:
        job = self.jobs.get(job_id)
        if job.status.terminal:
            return job, None
        if job.status in {RenderJobStatus.PLANNED, RenderJobStatus.INTERRUPTED}:
            return self._save_transition(job, RenderJobStatus.CANCELLED), None
        if job.status == RenderJobStatus.WAITING_RESOURCE:
            requested = self._save_transition(job, RenderJobStatus.CANCEL_REQUESTED)
            return self._save_transition(requested, RenderJobStatus.CANCELLED), None
        if job.status != RenderJobStatus.CANCEL_REQUESTED:
            job = self._save_transition(job, RenderJobStatus.CANCEL_REQUESTED)
        adapter = self._adapter(self._pack(job.engine_pack_id))
        ack = await adapter.cancel(job.id)
        if ack.confirmed:
            current = self.jobs.get(job.id)
            if current.status == RenderJobStatus.CANCEL_REQUESTED:
                current = self._save_transition(current, RenderJobStatus.CANCELLED)
            return current, ack
        return self.jobs.get(job.id), ack

    async def recover_jobs(self) -> tuple[RenderJob, ...]:
        recovered: list[RenderJob] = []
        for job in self.jobs.list():
            if job.status.terminal or job.status in {
                RenderJobStatus.PLANNED,
                RenderJobStatus.INTERRUPTED,
            }:
                recovered.append(job)
                continue
            adapter = self._adapter(self._pack(job.engine_pack_id))
            try:
                observation = await adapter.recover(job.id)
            except Exception as exc:
                current = self.jobs.get(job.id)
                if current.status.terminal:
                    recovered.append(current)
                    continue
                interrupted = current.transition(
                    RenderJobStatus.INTERRUPTED,
                    error=f"Engine recovery failed ({type(exc).__name__}).",
                )
                recovered.append(
                    self.jobs.save(interrupted, expected_version=current.version)
                )
                continue
            current = self.jobs.get(job.id)
            if current.status.terminal:
                recovered.append(current)
                continue
            if (
                current.status == RenderJobStatus.CANCEL_REQUESTED
                and observation.status == "cancelled"
            ):
                recovered.append(
                    self._save_transition(current, RenderJobStatus.CANCELLED)
                )
                continue
            detail = observation.detail or f"Engine recovery status: {observation.status}."
            interrupted = current.transition(
                RenderJobStatus.INTERRUPTED,
                error=detail,
            )
            recovered.append(
                self.jobs.save(interrupted, expected_version=current.version)
            )
        return tuple(recovered)

    def artifact(self, artifact_id: str) -> VoiceArtifact | None:
        return self.artifacts.get(artifact_id)

    def _apply_cache_hits(self, job: RenderJob) -> RenderJob:
        current = job
        for existing_chunk in tuple(current.chunks):
            if existing_chunk.status != RenderChunkStatus.PENDING:
                continue
            cached = self.cache.get(existing_chunk.plan.cache_key)
            if cached is None:
                continue
            artifact = self.artifacts.get(cached.artifact_id)
            if (
                artifact is None
                or artifact.provenance.synthesis_cache_key
                != existing_chunk.plan.cache_key
            ):
                self.cache.remove(existing_chunk.plan.cache_key)
                continue
            chunk = next(
                item
                for item in current.chunks
                if item.plan.id == existing_chunk.plan.id
            )
            completed = chunk.completed(artifact.id, cached=True)
            updated = current.replace_chunk(completed)
            current = self.jobs.save(updated, expected_version=current.version)
        return current

    def _assert_project_authorized(
        self,
        project: VoiceProject,
        pack: InstalledEnginePack,
        adapter: EngineAdapter,
        purpose: RenderPurpose,
        at: datetime,
    ) -> None:
        capabilities = set(pack.capabilities) & set(adapter.capabilities)
        for profile_id in project.voice_profile_ids:
            profile = self._profile(profile_id)
            profile.assert_authorized(purpose, at=at)
            required = (
                EngineCapability.REFERENCE_VOICE
                if profile.mode == "reference"
                else EngineCapability.VOICE_DESIGN
            )
            if required not in capabilities:
                raise VoiceCompatibilityError(
                    f"Voice engine does not support profile mode {profile.mode!r}."
                )
        if project.pronunciations and EngineCapability.PRONUNCIATION not in capabilities:
            raise VoiceCompatibilityError(
                "Voice engine does not support pronunciation dictionaries."
            )
        languages = {
            block.language or project.language
            for block in project.blocks
            if block.kind != "direction"
        }
        if len(languages) > 1 and EngineCapability.MULTILINGUAL not in capabilities:
            raise VoiceCompatibilityError(
                "Voice engine does not support multilingual projects."
            )

    def _engine_request(
        self,
        job: RenderJob,
        plan: object,
        pack: InstalledEnginePack,
        profile: VoiceProfile,
    ) -> EngineRenderRequest:
        from project_master.integrations.voice.cache import VoiceChunkPlan

        if not isinstance(plan, VoiceChunkPlan):
            raise TypeError("Expected a voice chunk plan.")
        return EngineRenderRequest(
            job_id=job.id,
            chunk=plan,
            engine_pack=pack,
            reference_artifact_ids=tuple(
                reference.artifact_id for reference in profile.references
            ),
            reference_sha256=tuple(reference.sha256 for reference in profile.references),
            designed_voice_description=(
                profile.description if profile.mode == "designed" else None
            ),
        )

    def _provenance(
        self,
        plan: object,
        pack: InstalledEnginePack,
        profile: VoiceProfile,
    ) -> VoiceArtifactProvenance:
        from project_master.integrations.voice.cache import VoiceChunkPlan

        if not isinstance(plan, VoiceChunkPlan):
            raise TypeError("Expected a voice chunk plan.")
        return VoiceArtifactProvenance(
            synthesis_cache_key=plan.cache_key,
            voice_profile_id=profile.id,
            voice_profile_digest=profile.digest,
            consent_record_id=profile.consent.id,
            rights_basis=profile.consent.basis,
            engine_id=pack.engine_id,
            engine_version=pack.installed_version,
            engine_pack_digest=pack.digest,
            model_asset_digests=pack.asset_digests,
            text_sha256=plan.text_sha256,
            seed=plan.seed,
        )

    def _validate_audio(self, audio: RenderedAudio, plan: object) -> None:
        from project_master.integrations.voice.cache import VoiceChunkPlan

        if not isinstance(plan, VoiceChunkPlan):
            raise TypeError("Expected a voice chunk plan.")
        if (
            audio.format != plan.output_format
            or audio.sample_rate_hz != plan.sample_rate_hz
            or audio.channels != plan.channels
        ):
            raise VoiceStudioError(
                "Voice engine output does not match the requested audio contract."
            )
        expected_media_types = {
            "wav": {"audio/wav"},
            "flac": {"audio/flac"},
            "mp3": {"audio/mpeg"},
            "opus": {"audio/opus", "audio/ogg"},
            "aac": {"audio/aac", "audio/mp4"},
        }
        if audio.media_type not in expected_media_types[plan.output_format]:
            raise VoiceStudioError(
                "Voice engine media type does not match the requested format."
            )

    def _verify_job_contract(
        self,
        job: RenderJob,
        project: VoiceProject,
        pack: InstalledEnginePack,
    ) -> None:
        if job.project_digest != project.digest:
            raise VoiceStudioError("Voice job project revision is no longer available.")
        if job.engine_pack_digest != pack.digest:
            raise VoiceStudioError("Voice job engine pack revision is no longer available.")

    def _save_transition(
        self,
        job: RenderJob,
        status: RenderJobStatus,
        *,
        error: str | None = None,
        lease_id: str | None = None,
    ) -> RenderJob:
        changed = job.transition(status, error=error, lease_id=lease_id)
        return self.jobs.save(changed, expected_version=job.version)

    def _profile(self, profile_id: str) -> VoiceProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise VoiceStudioError(f"Unknown voice profile {profile_id!r}.") from exc

    def _project(self, project_id: str) -> VoiceProject:
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise VoiceStudioError(f"Unknown voice project {project_id!r}.") from exc

    def _pack(self, pack_id: str) -> InstalledEnginePack:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            raise VoiceStudioError(f"Unknown voice engine pack {pack_id!r}.") from exc

    def _adapter(self, pack: InstalledEnginePack) -> EngineAdapter:
        try:
            return self._adapters[pack.engine_id]
        except KeyError as exc:
            raise VoiceCompatibilityError(
                f"No adapter is registered for voice engine {pack.engine_id!r}."
            ) from exc

    def _validate_adapter(self, pack: InstalledEnginePack) -> None:
        adapter = self._adapter(pack)
        if adapter.engine_id != pack.engine_id:
            raise VoiceCompatibilityError("Voice engine adapter ID does not match its pack.")
        if not set(pack.capabilities).issubset(adapter.capabilities):
            raise VoiceCompatibilityError(
                "Voice engine adapter does not implement every declared pack capability."
            )
        if adapter.max_chunk_characters < 50:
            raise VoiceCompatibilityError("Voice engine chunk limit is too small.")


def _unique_by_id(items: Sequence[object], label: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str):
            raise TypeError(f"Every {label} requires a string ID.")
        if item_id in indexed:
            raise ValueError(f"Duplicate {label} ID {item_id!r}.")
        indexed[item_id] = item
    return indexed
