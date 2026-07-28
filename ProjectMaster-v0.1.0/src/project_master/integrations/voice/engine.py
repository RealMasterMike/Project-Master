from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.voice.cache import VoiceChunkPlan
from project_master.integrations.voice.manifests import EngineCapability, InstalledEnginePack


class EngineHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    status: Literal["ready", "busy", "offline", "incompatible", "error"]
    detail: str = Field(default="", max_length=500)


class EngineRenderRequest(BaseModel):
    """Minimal synthesis inputs; consent records and subject labels are not sent to engines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    chunk: VoiceChunkPlan
    engine_pack: InstalledEnginePack
    reference_artifact_ids: tuple[str, ...] = ()
    reference_sha256: tuple[str, ...] = ()
    designed_voice_description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_voice_source(self) -> EngineRenderRequest:
        if bool(self.reference_artifact_ids) == bool(self.designed_voice_description):
            raise ValueError(
                "Engine request requires exactly one reference or designed voice source."
            )
        if len(self.reference_artifact_ids) != len(self.reference_sha256):
            raise ValueError("Voice reference IDs and digests must align.")
        return self


class RenderedAudio(BaseModel):
    """Bounded audio returned by an adapter before artifact persistence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes = Field(min_length=1, max_length=256 * 1024 * 1024)
    format: Literal["wav", "flac", "mp3", "opus", "aac"]
    media_type: Literal[
        "audio/wav",
        "audio/flac",
        "audio/mpeg",
        "audio/ogg",
        "audio/opus",
        "audio/aac",
        "audio/mp4",
    ]
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channels: Literal[1, 2]
    duration_seconds: float = Field(gt=0, le=86_400)
    engine_run_id: str | None = Field(default=None, max_length=160)

    @field_validator("duration_seconds")
    @classmethod
    def finite_duration(cls, value: float) -> float:
        import math

        if not math.isfinite(value):
            raise ValueError("Rendered audio duration must be finite.")
        return value


class CancellationAck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    confirmed: bool
    detail: str = Field(default="", max_length=500)


class EngineRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["not_found", "running", "cancelled", "failed", "unknown"]
    detail: str = Field(default="", max_length=500)


class EngineAdapter(Protocol):
    """Provider-agnostic boundary implemented by optional local engine packs."""

    engine_id: str
    capabilities: frozenset[EngineCapability]
    max_chunk_characters: int

    async def health(self, pack: InstalledEnginePack) -> EngineHealth: ...

    async def render_chunk(self, request: EngineRenderRequest) -> RenderedAudio: ...

    async def cancel(self, job_id: str) -> CancellationAck: ...

    async def recover(self, job_id: str) -> EngineRecovery: ...
