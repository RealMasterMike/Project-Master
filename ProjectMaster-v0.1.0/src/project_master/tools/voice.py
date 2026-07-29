from __future__ import annotations

import asyncio
from typing import Any

from project_master.integrations.voice import (
    CHATTERBOX_PACK_TEMPLATE,
    QWEN3_TTS_PACK_TEMPLATE,
    RenderPurpose,
    SQLiteVoiceStore,
    VoiceStudioService,
)
from project_master.tools.base import Tool, ToolRegistry


def register_voice_tools(
    registry: ToolRegistry,
    service: VoiceStudioService,
    persistence: SQLiteVoiceStore,
) -> None:
    """Expose bounded rendering operations; identity and rights setup remain user-owned."""

    registry.register(
        Tool(
            name="voice_studio_status",
            mutating=False,
            description=(
                "List Voice Studio engines, profiles, projects, render jobs, and optional "
                "neural engine templates without downloading anything."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "packs": [
                    pack.model_dump(mode="json") for pack in service.list_packs()
                ],
                "optional_pack_templates": [
                    QWEN3_TTS_PACK_TEMPLATE.model_dump(mode="json"),
                    CHATTERBOX_PACK_TEMPLATE.model_dump(mode="json"),
                ],
                "profiles": [
                    profile.model_dump(mode="json")
                    for profile in service.list_profiles()
                ],
                "projects": [
                    project.model_dump(mode="json")
                    for project in service.list_projects()
                ],
                "jobs": [
                    job.model_dump(mode="json")
                    for job in persistence.list_jobs(include_internal=False)
                ],
            },
        )
    )
    registry.register(
        Tool(
            name="voice_engine_health",
            mutating=False,
            description="Check one installed Voice Studio engine pack.",
            parameters={
                "type": "object",
                "properties": {"engine_pack_id": {"type": "string"}},
                "required": ["engine_pack_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.engine_health(str(args["engine_pack_id"]))
            ).model_dump(mode="json"),
        )
    )

    def create_job(args: dict[str, Any]) -> dict[str, Any]:
        job = service.create_render_job(
            project_id=str(args["project_id"]),
            engine_pack_id=str(args["engine_pack_id"]),
            purpose=RenderPurpose(str(args.get("purpose", "private"))),
        )
        return job.model_dump(mode="json")

    registry.register(
        Tool(
            name="voice_render_create",
            mutating=True,
            description=(
                "Create a Voice Studio render job from an existing user-authored project, "
                "voice profile, and rights record. This does not start rendering."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "engine_pack_id": {"type": "string"},
                    "purpose": {
                        "type": "string",
                        "enum": [item.value for item in RenderPurpose],
                        "default": "private",
                    },
                },
                "required": ["project_id", "engine_pack_id"],
                "additionalProperties": False,
            },
            handler=create_job,
        )
    )
    registry.register(
        Tool(
            name="voice_render_run",
            mutating=True,
            description=(
                "Run or explicitly resume an existing Voice Studio job. Rights and engine "
                "compatibility are rechecked before synthesis."
            ),
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.run_job(str(args["job_id"]))
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="voice_render_status",
            mutating=False,
            description="Read a durable Voice Studio render job.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: service.job_status(
                str(args["job_id"])
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="voice_render_cancel",
            mutating=True,
            description="Cancel a Project Master-owned Voice Studio render job.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _cancel_payload(
                _run(service.cancel_job(str(args["job_id"])))
            ),
        )
    )
    registry.register(
        Tool(
            name="voice_artifacts_list",
            mutating=False,
            description="List checksum-verified Voice Studio audio artifacts and provenance.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "artifacts": [
                    artifact.model_dump(mode="json")
                    for artifact in persistence.list_artifacts()
                ]
            },
        )
    )


def _cancel_payload(result: tuple[Any, Any]) -> dict[str, Any]:
    job, acknowledgement = result
    return {
        "job": job.model_dump(mode="json"),
        "acknowledgement": (
            acknowledgement.model_dump(mode="json")
            if acknowledgement is not None
            else None
        ),
    }


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)
