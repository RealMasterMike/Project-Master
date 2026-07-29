from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from project_master.dreams import (
    DreamDisposition,
    DreamService,
    DreamSource,
    SourceKind,
    SourceSensitivity,
)
from project_master.team.catalog import OllamaModelCatalog
from project_master.tools.base import Tool, ToolRegistry


def register_dream_tools(
    registry: ToolRegistry,
    service: DreamService,
    catalog: OllamaModelCatalog,
    *,
    configured_model: str,
) -> None:
    """Expose proposal-only Dream Lab operations to the authorized lead agent."""

    registry.register(
        Tool(
            name="dream_recipes_list",
            mutating=False,
            description="List available proposal-only Dream Lab recipes.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "recipes": [item.to_dict() for item in service.list_recipes()]
            },
        )
    )

    def run_manual(args: dict[str, Any]) -> dict[str, Any]:
        raw_sources = args.get("sources", [])
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("A manual dream requires at least one attributed source.")
        if len(raw_sources) > 64:
            raise ValueError("A manual dream accepts at most 64 sources.")
        captured_at = datetime.now(UTC)
        sources: list[DreamSource] = []
        for index, raw in enumerate(raw_sources):
            if not isinstance(raw, dict):
                raise ValueError("Each dream source must be an object.")
            sources.append(
                DreamSource(
                    source_id=str(raw.get("source_id") or f"source-{index + 1}"),
                    kind=SourceKind(str(raw.get("kind", "user_note"))),
                    locator=str(raw.get("locator") or f"tool://source/{index + 1}"),
                    content=str(raw.get("content", "")),
                    captured_at_utc=captured_at,
                    sensitivity=SourceSensitivity(
                        str(raw.get("sensitivity", "internal"))
                    ),
                    allow_dreaming=bool(raw.get("allow_dreaming", True)),
                )
            )
        request_id = str(args.get("request_id") or uuid4().hex)
        execution = service.execute_manual(
            recipe_id=str(args["recipe_id"]),
            request_id=request_id,
            sources=sources,
            models=catalog.load(refresh=True),
            preferred_lead=str(args.get("preferred_lead") or configured_model),
        )
        return execution.to_dict()

    registry.register(
        Tool(
            name="dream_run_manual",
            mutating=True,
            description=(
                "Run every eligible local model through a proposal-only Dream Lab recipe "
                "using explicitly attributed source material. This can be slow. It never "
                "changes memory, performs actions, or queues media."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "recipe_id": {"type": "string"},
                    "request_id": {"type": "string"},
                    "preferred_lead": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_id": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": [item.value for item in SourceKind],
                                },
                                "locator": {"type": "string"},
                                "content": {"type": "string"},
                                "sensitivity": {
                                    "type": "string",
                                    "enum": [item.value for item in SourceSensitivity],
                                },
                                "allow_dreaming": {"type": "boolean"},
                            },
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["recipe_id", "sources"],
                "additionalProperties": False,
            },
            handler=run_manual,
        )
    )
    registry.register(
        Tool(
            name="dream_runs_list",
            mutating=False,
            description="List durable Dream Lab runs and their truthful terminal state.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    }
                },
                "additionalProperties": False,
            },
            handler=lambda args: {
                "runs": [
                    run.to_dict()
                    for run in service.list_runs(
                        min(max(int(args.get("limit", 50)), 1), 200)
                    )
                ]
            },
        )
    )
    registry.register(
        Tool(
            name="dream_inbox_list",
            mutating=False,
            description=(
                "List Dream Lab proposals. Every item is speculation pending explicit "
                "local-user review unless its disposition says otherwise."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "disposition": {
                        "type": ["string", "null"],
                        "enum": [
                            DreamDisposition.PENDING.value,
                            DreamDisposition.PROMOTED.value,
                            DreamDisposition.REJECTED.value,
                            None,
                        ],
                        "default": None,
                    }
                },
                "additionalProperties": False,
            },
            handler=lambda args: {
                "items": [
                    item.to_dict()
                    for item in service.list_inbox(
                        DreamDisposition(str(args["disposition"]))
                        if args.get("disposition")
                        else None
                    )
                ]
            },
        )
    )
