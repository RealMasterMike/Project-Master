from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RightsBasis(StrEnum):
    SELF_VOICE = "self_voice"
    EXPLICIT_CONSENT = "explicit_consent"
    LICENSED_VOICE = "licensed_voice"
    SYNTHETIC_REFERENCE = "synthetic_reference"
    SYNTHETIC_DESIGN = "synthetic_design"


class ConsentScope(StrEnum):
    VOICE_GENERATION = "voice_generation"
    PUBLICATION = "publication"
    COMMERCIAL_USE = "commercial_use"


class RenderPurpose(StrEnum):
    PRIVATE = "private"
    PUBLICATION = "publication"
    COMMERCIAL = "commercial"


class VoiceRightsError(PermissionError):
    pass


class ConsentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    basis: RightsBasis
    scopes: tuple[ConsentScope, ...]
    subject_label: str = Field(min_length=1, max_length=120)
    attested_by_user: bool
    granted_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    evidence_artifact_ids: tuple[str, ...] = ()
    notes: str = Field(default="", max_length=500)

    @field_validator("evidence_artifact_ids")
    @classmethod
    def validate_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Voice consent evidence artifacts must be unique.")
        for artifact_id in value:
            if (
                not artifact_id
                or len(artifact_id) > 160
                or not all(character.isalnum() or character in "._:-" for character in artifact_id)
            ):
                raise ValueError("Voice consent evidence artifact ID is invalid.")
        return value

    @field_validator("granted_at", "expires_at", "revoked_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Voice consent timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> ConsentRecord:
        if ConsentScope.VOICE_GENERATION not in self.scopes:
            raise ValueError("Voice consent must explicitly include voice_generation.")
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("Voice consent scopes must be unique.")
        if self.expires_at is not None and self.expires_at <= self.granted_at:
            raise ValueError("Voice consent expiry must follow its grant time.")
        if self.revoked_at is not None and self.revoked_at < self.granted_at:
            raise ValueError("Voice consent revocation cannot precede its grant.")
        if self.basis in {RightsBasis.EXPLICIT_CONSENT, RightsBasis.LICENSED_VOICE}:
            if not self.evidence_artifact_ids:
                raise ValueError(
                    "Explicit consent and licensed voices require an evidence artifact."
                )
        return self

    def assert_authorized(
        self,
        purpose: RenderPurpose,
        *,
        at: datetime | None = None,
    ) -> None:
        timestamp = at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("Authorization checks require a timezone-aware timestamp.")
        if not self.attested_by_user:
            raise VoiceRightsError("Voice rights have not been attested by the user.")
        if self.revoked_at is not None and self.revoked_at <= timestamp:
            raise VoiceRightsError("Voice authorization has been revoked.")
        if self.expires_at is not None and self.expires_at <= timestamp:
            raise VoiceRightsError("Voice authorization has expired.")
        required = {
            RenderPurpose.PRIVATE: ConsentScope.VOICE_GENERATION,
            RenderPurpose.PUBLICATION: ConsentScope.PUBLICATION,
            RenderPurpose.COMMERCIAL: ConsentScope.COMMERCIAL_USE,
        }[purpose]
        if required not in self.scopes:
            raise VoiceRightsError(
                f"Voice authorization does not include {required.value}."
            )


class VoiceReference(BaseModel):
    """Metadata for an already imported audio artifact, never a filesystem path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal[
        "audio/wav",
        "audio/flac",
        "audio/mpeg",
        "audio/ogg",
        "audio/opus",
        "audio/mp4",
    ]
    duration_seconds: float = Field(gt=0, le=600)
    sample_rate_hz: int = Field(ge=8_000, le=384_000)
    channels: int = Field(ge=1, le=2)
    transcript: str | None = Field(default=None, max_length=10_000)


class VoiceProfile(BaseModel):
    """Digest-verified voice identity and authorization contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["reference", "designed"]
    language: str = Field(min_length=2, max_length=35, pattern=r"^[A-Za-z0-9-]+$")
    description: str = Field(default="", max_length=1000)
    references: tuple[VoiceReference, ...]
    consent: ConsentRecord
    created_at: datetime
    revision: int = Field(default=1, ge=1)
    digest: str
    enabled: bool = True

    @classmethod
    def create(
        cls,
        *,
        profile_id: str,
        name: str,
        mode: Literal["reference", "designed"],
        language: str,
        consent: ConsentRecord,
        references: tuple[VoiceReference, ...] | list[VoiceReference] = (),
        description: str = "",
        created_at: datetime | None = None,
        revision: int = 1,
        enabled: bool = True,
    ) -> VoiceProfile:
        timestamp = created_at or datetime.now(UTC)
        content = {
            "id": profile_id,
            "name": name,
            "mode": mode,
            "language": language,
            "description": description,
            "references": [
                reference.model_dump(mode="json") for reference in tuple(references)
            ],
            "consent": consent.model_dump(mode="json"),
            "created_at": timestamp.isoformat(),
            "revision": revision,
            "enabled": enabled,
        }
        digest = hashlib.sha256(_canonical(content).encode()).hexdigest()
        return cls(
            id=profile_id,
            name=name,
            mode=mode,
            language=language,
            description=description,
            created_at=timestamp,
            consent=consent,
            references=tuple(references),
            revision=revision,
            enabled=enabled,
            digest=digest,
        )

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Voice profile created_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> VoiceProfile:
        if self.mode == "reference":
            if not self.references:
                raise ValueError("Reference voice profiles require at least one audio reference.")
            if self.consent.basis == RightsBasis.SYNTHETIC_DESIGN:
                raise ValueError(
                    "Reference voices cannot use synthetic_design; "
                    "they require an audio-reference rights basis."
                )
        else:
            if self.references:
                raise ValueError("Designed voices cannot include an audio reference.")
            if self.consent.basis != RightsBasis.SYNTHETIC_DESIGN:
                raise ValueError("Designed voices require the synthetic_design rights basis.")
            if not self.description.strip():
                raise ValueError("Designed voices require a non-empty voice description.")
        if len({reference.sha256 for reference in self.references}) != len(self.references):
            raise ValueError("Voice profile references must be unique.")
        if self.digest != self._content_digest():
            raise ValueError("Voice profile digest does not match its content.")
        return self

    def assert_authorized(
        self,
        purpose: RenderPurpose,
        *,
        at: datetime | None = None,
    ) -> None:
        if not self.enabled:
            raise VoiceRightsError("Voice profile is disabled.")
        if self.digest != self._content_digest():
            raise VoiceRightsError("Voice profile integrity verification failed.")
        self.consent.assert_authorized(purpose, at=at)

    def _content_digest(self) -> str:
        content = {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "language": self.language,
            "description": self.description,
            "references": [
                reference.model_dump(mode="json") for reference in self.references
            ],
            "consent": self.consent.model_dump(mode="json"),
            "created_at": self.created_at.isoformat(),
            "revision": self.revision,
            "enabled": self.enabled,
        }
        return hashlib.sha256(_canonical(content).encode()).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
