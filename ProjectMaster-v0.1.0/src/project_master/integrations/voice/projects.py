from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PronunciationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    term: str = Field(min_length=1, max_length=200)
    pronunciation: str = Field(min_length=1, max_length=500)
    alphabet: Literal["plain", "ipa", "x-sampa"] = "plain"
    language: str = Field(min_length=2, max_length=35, pattern=r"^[A-Za-z0-9-]+$")
    case_sensitive: bool = False

    @field_validator("term", "pronunciation")
    @classmethod
    def safe_text(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if "\n" in normalized or "\t" in normalized:
            raise ValueError("Pronunciation entries must remain on one line.")
        return normalized


class ScriptBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    kind: Literal["narration", "dialogue", "direction"] = "narration"
    text: str = Field(min_length=1, max_length=100_000)
    voice_profile_id: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    language: str | None = Field(
        default=None, max_length=35, pattern=r"^[A-Za-z0-9-]+$"
    )
    performance_direction: str = Field(default="", max_length=1000)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pause_after_ms: int = Field(default=0, ge=0, le=30_000)

    @field_validator("text")
    @classmethod
    def normalize_script(cls, value: str) -> str:
        normalized = _normalize_text(value)
        if not normalized:
            raise ValueError("Script block text cannot be empty.")
        return normalized

    @field_validator("performance_direction")
    @classmethod
    def normalize_direction(cls, value: str) -> str:
        return _normalize_text(value) if value else ""

    @field_validator("speed")
    @classmethod
    def finite_speed(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Voice speed must be finite.")
        return value


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["wav", "flac", "mp3", "opus", "aac"] = "wav"
    sample_rate_hz: int = Field(default=24_000, ge=8_000, le=192_000)
    channels: Literal[1, 2] = 1
    base_seed: int = Field(default=0, ge=0, le=2**63 - 1)
    max_chunk_characters: int = Field(default=500, ge=50, le=5000)
    normalize_loudness: bool = True


class VoiceProject(BaseModel):
    """Digest-verified script revision and pronunciation dictionary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=160)
    language: str = Field(min_length=2, max_length=35, pattern=r"^[A-Za-z0-9-]+$")
    default_voice_profile_id: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    blocks: tuple[ScriptBlock, ...]
    pronunciations: tuple[PronunciationEntry, ...] = ()
    created_at: datetime
    revision: int = Field(default=1, ge=1)
    digest: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        name: str,
        language: str,
        default_voice_profile_id: str,
        blocks: tuple[ScriptBlock, ...] | list[ScriptBlock],
        pronunciations: tuple[PronunciationEntry, ...] | list[PronunciationEntry] = (),
        created_at: datetime | None = None,
        revision: int = 1,
    ) -> VoiceProject:
        timestamp = created_at or datetime.now(UTC)
        block_tuple = tuple(blocks)
        pronunciation_tuple = tuple(pronunciations)
        content = {
            "id": project_id,
            "name": name,
            "language": language,
            "default_voice_profile_id": default_voice_profile_id,
            "blocks": [block.model_dump(mode="json") for block in block_tuple],
            "pronunciations": [
                entry.model_dump(mode="json") for entry in pronunciation_tuple
            ],
            "created_at": timestamp.isoformat(),
            "revision": revision,
        }
        digest = hashlib.sha256(_canonical(content).encode()).hexdigest()
        return cls(
            id=project_id,
            name=name,
            language=language,
            default_voice_profile_id=default_voice_profile_id,
            blocks=block_tuple,
            pronunciations=pronunciation_tuple,
            created_at=timestamp,
            revision=revision,
            digest=digest,
        )

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Voice project created_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_project(self) -> VoiceProject:
        if not self.blocks:
            raise ValueError("Voice project requires at least one script block.")
        if len(self.blocks) > 10_000:
            raise ValueError("Voice project exceeds the script block limit.")
        if sum(len(block.text) for block in self.blocks) > 1_000_000:
            raise ValueError("Voice project exceeds the total script size limit.")
        if len(self.pronunciations) > 10_000:
            raise ValueError("Voice project exceeds the pronunciation entry limit.")
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("Voice project script block IDs must be unique.")
        pronunciation_ids = [entry.id for entry in self.pronunciations]
        if len(pronunciation_ids) != len(set(pronunciation_ids)):
            raise ValueError("Voice project pronunciation IDs must be unique.")
        pronunciation_keys = [
            (
                entry.term if entry.case_sensitive else entry.term.casefold(),
                entry.language.casefold(),
            )
            for entry in self.pronunciations
        ]
        if len(pronunciation_keys) != len(set(pronunciation_keys)):
            raise ValueError("Voice project has ambiguous pronunciation entries.")
        if self.digest != self._content_digest():
            raise ValueError("Voice project digest does not match its content.")
        return self

    @property
    def voice_profile_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                block.voice_profile_id or self.default_voice_profile_id
                for block in self.blocks
                if block.kind != "direction"
            )
        )

    def applicable_pronunciations(
        self, text: str, language: str
    ) -> tuple[PronunciationEntry, ...]:
        applicable: list[PronunciationEntry] = []
        for entry in self.pronunciations:
            if entry.language.casefold() != language.casefold():
                continue
            haystack = text if entry.case_sensitive else text.casefold()
            needle = entry.term if entry.case_sensitive else entry.term.casefold()
            if needle in haystack:
                applicable.append(entry)
        return tuple(applicable)

    def _content_digest(self) -> str:
        content = {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "default_voice_profile_id": self.default_voice_profile_id,
            "blocks": [block.model_dump(mode="json") for block in self.blocks],
            "pronunciations": [
                entry.model_dump(mode="json") for entry in self.pronunciations
            ],
            "created_at": self.created_at.isoformat(),
            "revision": self.revision,
        }
        return hashlib.sha256(_canonical(content).encode()).hexdigest()


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in normalized:
        raise ValueError("Voice text cannot contain NUL.")
    if any(ord(character) < 32 and character not in {"\n", "\t"} for character in normalized):
        raise ValueError("Voice text contains unsupported control characters.")
    return normalized.strip()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
