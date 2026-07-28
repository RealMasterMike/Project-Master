from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_master.integrations.voice.engine import RenderedAudio
from project_master.integrations.voice.profiles import RightsBasis


class VoiceArtifactProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    synthesis_cache_key: str
    voice_profile_id: str
    voice_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_record_id: str
    rights_basis: RightsBasis
    engine_id: str
    engine_version: str
    engine_pack_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_asset_digests: tuple[str, ...]
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int

    @model_validator(mode="after")
    def validate_asset_digests(self) -> VoiceArtifactProvenance:
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.model_asset_digests
        ):
            raise ValueError("Voice provenance contains an invalid model asset digest.")
        if len(self.model_asset_digests) != len(set(self.model_asset_digests)):
            raise ValueError("Voice provenance model asset digests must be unique.")
        return self


class VoiceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    format: Literal["wav", "flac", "mp3", "opus", "aac"]
    media_type: str
    sample_rate_hz: int
    channels: Literal[1, 2]
    duration_seconds: float
    created_at: datetime
    verified: Literal[True] = True
    provenance: VoiceArtifactProvenance

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Voice artifact timestamps must include a timezone.")
        return value


class VoiceArtifactStore(Protocol):
    def store(
        self, audio: RenderedAudio, provenance: VoiceArtifactProvenance
    ) -> VoiceArtifact: ...

    def get(self, artifact_id: str) -> VoiceArtifact | None: ...


class InMemoryVoiceArtifactStore:
    """Test/reference store. Production adapters should stream and atomically promote files."""

    def __init__(self) -> None:
        self._artifacts: dict[str, VoiceArtifact] = {}
        self._content: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def store(
        self, audio: RenderedAudio, provenance: VoiceArtifactProvenance
    ) -> VoiceArtifact:
        digest = hashlib.sha256(audio.content).hexdigest()
        cache_suffix = provenance.synthesis_cache_key.removeprefix("voice-cache-")
        artifact_id = f"voice-artifact-{cache_suffix}-{digest[:16]}"
        artifact = VoiceArtifact(
            id=artifact_id,
            sha256=digest,
            size_bytes=len(audio.content),
            format=audio.format,
            media_type=audio.media_type,
            sample_rate_hz=audio.sample_rate_hz,
            channels=audio.channels,
            duration_seconds=audio.duration_seconds,
            created_at=datetime.now(UTC),
            provenance=provenance,
        )
        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                if existing.sha256 != artifact.sha256:
                    raise ValueError("Voice artifact ID collision.")
                return existing.model_copy(deep=True)
            self._artifacts[artifact_id] = artifact.model_copy(deep=True)
            self._content[artifact_id] = bytes(audio.content)
            return artifact.model_copy(deep=True)

    def get(self, artifact_id: str) -> VoiceArtifact | None:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            return artifact.model_copy(deep=True) if artifact is not None else None

    def read(self, artifact_id: str) -> bytes:
        with self._lock:
            try:
                return bytes(self._content[artifact_id])
            except KeyError as exc:
                raise KeyError(f"Voice artifact {artifact_id!r} does not exist.") from exc
