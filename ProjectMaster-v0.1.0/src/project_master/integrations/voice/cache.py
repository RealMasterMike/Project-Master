from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_master.integrations.voice.manifests import InstalledEnginePack
from project_master.integrations.voice.profiles import VoiceProfile
from project_master.integrations.voice.projects import (
    PronunciationEntry,
    RenderSettings,
    VoiceProject,
)


class VoiceChunkPlan(BaseModel):
    """Deterministic unit of synthesis and cache identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str
    cache_key: str
    ordinal: int = Field(ge=0)
    block_id: str
    block_chunk_index: int = Field(ge=0)
    text: str
    language: str
    voice_profile_id: str
    voice_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_pack_id: str
    engine_pack_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    performance_direction: str = ""
    speed: float
    pause_after_ms: int
    pronunciations: tuple[PronunciationEntry, ...] = ()
    output_format: Literal["wav", "flac", "mp3", "opus", "aac"]
    sample_rate_hz: int
    channels: Literal[1, 2]
    seed: int
    normalize_loudness: bool

    @model_validator(mode="after")
    def validate_identity(self) -> VoiceChunkPlan:
        expected_id = f"voice-chunk-{self._instance_digest()[:32]}"
        expected_cache_key = f"voice-cache-{self._cache_digest()[:32]}"
        if self.id != expected_id:
            raise ValueError("Voice chunk ID does not match its deterministic content.")
        if self.cache_key != expected_cache_key:
            raise ValueError("Voice chunk cache key does not match its synthesis inputs.")
        return self

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def _cache_digest(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={
                "id",
                "cache_key",
                "ordinal",
                "block_id",
                "block_chunk_index",
                "project_digest",
                "engine_pack_id",
                "voice_profile_id",
            },
        )
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    def _instance_digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"id", "cache_key"})
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()


class ChunkCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_key: str
    artifact_id: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_timestamp(self) -> ChunkCacheEntry:
        if self.created_at.tzinfo is None:
            raise ValueError("Voice cache timestamps must include a timezone.")
        return self


class ChunkCache(Protocol):
    def get(self, cache_key: str) -> ChunkCacheEntry | None: ...

    def put(self, entry: ChunkCacheEntry) -> None: ...

    def remove(self, cache_key: str) -> None: ...


class InMemoryChunkCache:
    def __init__(self) -> None:
        self._entries: dict[str, ChunkCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, cache_key: str) -> ChunkCacheEntry | None:
        with self._lock:
            entry = self._entries.get(cache_key)
            return entry.model_copy(deep=True) if entry is not None else None

    def put(self, entry: ChunkCacheEntry) -> None:
        with self._lock:
            existing = self._entries.get(entry.cache_key)
            if existing is not None and existing.artifact_id != entry.artifact_id:
                raise ValueError("A deterministic voice chunk cannot map to two artifacts.")
            self._entries[entry.cache_key] = entry.model_copy(deep=True)

    def remove(self, cache_key: str) -> None:
        with self._lock:
            self._entries.pop(cache_key, None)


def build_chunk_plan(
    project: VoiceProject,
    profiles: Mapping[str, VoiceProfile],
    engine_pack: InstalledEnginePack,
    settings: RenderSettings,
    *,
    engine_max_characters: int,
) -> tuple[VoiceChunkPlan, ...]:
    limit = min(settings.max_chunk_characters, engine_max_characters)
    if limit < 50:
        raise ValueError("Voice engine chunk limit must be at least 50 characters.")
    planned: list[VoiceChunkPlan] = []
    ordinal = 0
    for block in project.blocks:
        if block.kind == "direction":
            continue
        profile_id = block.voice_profile_id or project.default_voice_profile_id
        try:
            profile = profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"Voice profile {profile_id!r} is not registered.") from exc
        language = block.language or project.language
        chunks = split_script_text(block.text, limit)
        for block_chunk_index, text in enumerate(chunks):
            pronunciations = project.applicable_pronunciations(text, language)
            seed_material = f"{settings.base_seed}:{block.id}:{block_chunk_index}".encode()
            seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
            seed &= 2**63 - 1
            raw = {
                "schema_version": 1,
                "ordinal": ordinal,
                "block_id": block.id,
                "block_chunk_index": block_chunk_index,
                "text": text,
                "language": language,
                "voice_profile_id": profile.id,
                "voice_profile_digest": profile.digest,
                "project_digest": project.digest,
                "engine_pack_id": engine_pack.id,
                "engine_pack_digest": engine_pack.digest,
                "performance_direction": block.performance_direction,
                "speed": block.speed,
                "pause_after_ms": (
                    block.pause_after_ms
                    if block_chunk_index == len(chunks) - 1
                    else 0
                ),
                "pronunciations": [
                    entry.model_dump(mode="json") for entry in pronunciations
                ],
                "output_format": settings.format,
                "sample_rate_hz": settings.sample_rate_hz,
                "channels": settings.channels,
                "seed": seed,
                "normalize_loudness": settings.normalize_loudness,
            }
            model_payload = {**raw, "pronunciations": pronunciations}
            provisional = VoiceChunkPlan.model_construct(id="", cache_key="", **model_payload)
            cache_digest = provisional._cache_digest()
            instance_digest = provisional._instance_digest()
            planned.append(
                VoiceChunkPlan(
                    id=f"voice-chunk-{instance_digest[:32]}",
                    cache_key=f"voice-cache-{cache_digest[:32]}",
                    **model_payload,
                )
            )
            ordinal += 1
    if not planned:
        raise ValueError("Voice project contains no renderable script blocks.")
    return tuple(planned)


def split_script_text(text: str, max_characters: int) -> tuple[str, ...]:
    """Deterministically split plain text without engine-specific markup."""

    if max_characters < 1:
        raise ValueError("Voice chunk length must be positive.")
    collapsed = " ".join(text.split())
    if not collapsed:
        return ()
    units = [
        unit.strip()
        for unit in re.split(r"(?<=[.!?])\s+|\n+", collapsed)
        if unit.strip()
    ]
    pieces: list[str] = []
    for unit in units:
        pieces.extend(_split_long_unit(unit, max_characters))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = piece if not current else f"{current} {piece}"
        if len(candidate) <= max_characters:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return tuple(chunks)


def _split_long_unit(unit: str, limit: int) -> list[str]:
    if len(unit) <= limit:
        return [unit]
    words = unit.split()
    if not words:
        return [unit[index : index + limit] for index in range(0, len(unit), limit)]
    pieces: list[str] = []
    current = ""
    for word in words:
        if len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(word[index : index + limit] for index in range(0, len(word), limit))
            continue
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
        else:
            pieces.append(current)
            current = word
    if current:
        pieces.append(current)
    return pieces


def cache_entry(cache_key: str, artifact_id: str) -> ChunkCacheEntry:
    return ChunkCacheEntry(
        cache_key=cache_key,
        artifact_id=artifact_id,
        created_at=datetime.now(UTC),
    )


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
