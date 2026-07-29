from __future__ import annotations

import asyncio
from typing import Any

from project_master.integrations.comfyui.defaults import bundled_workflow_ids
from project_master.integrations.comfyui.persistence import SQLiteComfyStore
from project_master.integrations.comfyui.service import ComfyUIService
from project_master.tools.base import Tool, ToolRegistry


def register_comfyui_tools(
    registry: ToolRegistry,
    service: ComfyUIService,
    persistence: SQLiteComfyStore,
) -> None:
    """Expose bounded ComfyUI operations without arbitrary REST or graph submission."""

    curated_workflow_ids = bundled_workflow_ids()

    registry.register(
        Tool(
            name="comfy_connections_list",
            mutating=False,
            description="List configured ComfyUI connection profiles without resolving secrets.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "connections": [
                    profile.model_dump(mode="json") for profile in service.list_profiles()
                ]
            },
        )
    )
    registry.register(
        Tool(
            name="comfy_connection_status",
            mutating=False,
            description="Probe one configured ComfyUI server and report its basic capabilities.",
            parameters={
                "type": "object",
                "properties": {"profile_id": {"type": "string"}},
                "required": ["profile_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.connection_status(str(args["profile_id"]))
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="comfy_workflows_list",
            mutating=False,
            description="List imported ComfyUI workflow revisions and their approval state.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "workflows": [
                    {
                        "id": item.revision.id,
                        "name": item.revision.name,
                        "purpose": item.revision.purpose,
                        "digest": item.revision.digest,
                        "curated_default": (
                            item.revision.id in curated_workflow_ids
                        ),
                        "bindings": [
                            binding.model_dump(mode="json")
                            for binding in item.revision.bindings
                        ],
                        "trust_state": item.trust_state,
                    }
                    for item in persistence.list_workflows()
                ]
            },
        )
    )
    registry.register(
        Tool(
            name="comfy_workflow_validate",
            mutating=False,
            description=(
                "Check whether an imported workflow uses node types available on one "
                "configured ComfyUI server. This does not queue work."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string"},
                    "workflow_revision_id": {"type": "string"},
                },
                "required": ["profile_id", "workflow_revision_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.validate_compatibility(
                    str(args["profile_id"]),
                    str(args["workflow_revision_id"]),
                )
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="comfy_queue_status",
            mutating=False,
            description="Read Project Master's configured ComfyUI queue state.",
            parameters={
                "type": "object",
                "properties": {"profile_id": {"type": "string"}},
                "required": ["profile_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.queue_status(str(args["profile_id"]))
            ).model_dump(mode="json"),
        )
    )

    def scoped_job_id(args: dict[str, Any]) -> str:
        """Refuse to touch a job that belongs to a different conversation scope.

        Job IDs are guessable and the service accessors are not project-aware, so without
        this check a chat scoped to one Creator project could read, poll, or cancel another
        project's job. A rootless chat owns only rootless jobs.
        """

        job_id = str(args["job_id"])
        owner = service.job_status(job_id).project_id
        if owner != registry.project_id:
            raise PermissionError(
                f"ComfyUI job {job_id!r} belongs to a different project and is not "
                "accessible from this conversation."
            )
        return job_id

    def run_workflow(args: dict[str, Any]) -> dict[str, Any]:
        revision_id = str(args["workflow_revision_id"])
        stored = persistence.get_workflow(revision_id)
        if stored.trust_state != "approved":
            raise PermissionError(
                "ComfyUI workflows require explicit approval before an agent can run them."
            )
        values = args.get("values", {})
        if not isinstance(values, dict):
            raise ValueError("ComfyUI workflow values must be an object.")
        return _run(
            service.submit_workflow(
                str(args["profile_id"]),
                revision_id,
                values,
                project_id=registry.project_id,
            )
        ).model_dump(mode="json")

    registry.register(
        Tool(
            name="comfy_workflow_run",
            mutating=True,
            description=(
                "Queue an explicitly approved, immutable ComfyUI workflow revision with "
                "schema-validated values."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string"},
                    "workflow_revision_id": {"type": "string"},
                    "values": {"type": "object", "default": {}},
                },
                "required": ["profile_id", "workflow_revision_id"],
                "additionalProperties": False,
            },
            handler=run_workflow,
        )
    )
    registry.register(
        Tool(
            name="comfy_run_status",
            mutating=True,
            description="Refresh and report a Project Master-owned ComfyUI job.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.refresh_job(scoped_job_id(args))
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="comfy_run_cancel",
            mutating=True,
            description=(
                "Cancel a Project Master-owned ComfyUI job. A global interrupt is used only "
                "when the queue proves Project Master owns the sole running prompt."
            ),
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _run(
                service.cancel_job(scoped_job_id(args))
            ).model_dump(mode="json"),
        )
    )
    registry.register(
        Tool(
            name="comfy_run_artifacts",
            mutating=False,
            description="List output metadata for a Project Master-owned ComfyUI job.",
            parameters={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=lambda args: {
                "artifacts": [
                    artifact.model_dump(mode="json")
                    for artifact in service.artifacts(scoped_job_id(args))
                ]
            },
        )
    )


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)
