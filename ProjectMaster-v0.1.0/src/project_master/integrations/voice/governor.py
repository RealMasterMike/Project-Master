from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from project_master.integrations.voice.resources import (
    ResourceLease,
    VoiceResourceRequest,
)
from project_master.orchestration.resource import (
    LOCAL_GPU_INFERENCE_RESOURCE,
    ResourceGovernor,
)


class GovernorVoiceLeaseProvider:
    """Bridge Voice Studio resource requests to the shared durable governor."""

    def __init__(
        self,
        governor: ResourceGovernor,
        *,
        before_gpu_acquire: Callable[[], None] | None = None,
        wait_seconds: float = 15.0,
    ) -> None:
        self.governor = governor
        self.before_gpu_acquire = before_gpu_acquire
        self.wait_seconds = wait_seconds

    async def acquire(
        self,
        request: VoiceResourceRequest,
        *,
        owner_id: str,
    ) -> ResourceLease:
        resource_id = (
            LOCAL_GPU_INFERENCE_RESOURCE
            if request.kind == "gpu"
            else "local-cpu-voice"
        )
        metadata = {
            "subsystem": "voice",
            "minimum_memory_mb": request.minimum_memory_mb,
            "minimum_vram_mb": request.minimum_vram_mb,
            "exclusive": request.exclusive,
            "priority": request.priority,
            "preemptible": request.preemptible,
        }
        # Interactive chat holds this same GPU lease for the length of a turn
        # and waits up to 15s for it. Failing a render instantly meant that
        # rendering right after sending a message always errored, so wait the
        # same way instead of giving up on a lease that is about to free up.
        deadline = asyncio.get_running_loop().time() + max(0.0, self.wait_seconds)
        while not self.governor.acquire(
            resource_id, owner_id, ttl_seconds=3_600, metadata=metadata
        ):
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(
                    f"Resource {resource_id} is busy with another local "
                    "inference job (chat or a Dream run). It did not free up "
                    f"within {self.wait_seconds:.0f}s — retry in a moment."
                )
            await asyncio.sleep(0.05)
        if request.kind == "gpu" and self.before_gpu_acquire is not None:
            try:
                await asyncio.to_thread(self.before_gpu_acquire)
            except Exception:
                self.governor.release(resource_id, owner_id)
                raise
        return ResourceLease(
            id=f"{resource_id}:{owner_id}",
            owner_id=owner_id,
            resource_id=resource_id,
            acquired_at=datetime.now(UTC),
        )

    async def release(self, lease: ResourceLease) -> None:
        self.governor.release(lease.resource_id, lease.owner_id)
