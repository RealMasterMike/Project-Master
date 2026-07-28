from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VoiceResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["cpu", "gpu"] = "gpu"
    minimum_memory_mb: int = Field(default=0, ge=0, le=1_000_000)
    minimum_vram_mb: int = Field(default=0, ge=0, le=1_000_000)
    exclusive: bool = True
    priority: int = Field(default=50, ge=0, le=100)
    preemptible: bool = True


class ResourceLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner_id: str
    resource_id: str
    acquired_at: datetime

    @field_validator("acquired_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Resource lease timestamps must include a timezone.")
        return value


class ResourceLeaseProvider(Protocol):
    """Adapter to the future shared GPU/CPU governor."""

    async def acquire(
        self, request: VoiceResourceRequest, *, owner_id: str
    ) -> ResourceLease: ...

    async def release(self, lease: ResourceLease) -> None: ...
