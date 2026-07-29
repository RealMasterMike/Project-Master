from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from project_master.integrations.comfyui.persistence import (
    SQLiteComfyStore,
    StoredWorkflow,
)
from project_master.integrations.comfyui.workflow import (
    WorkflowBinding,
    WorkflowRevision,
)

_BUNDLED_CREATED_AT = datetime(2026, 7, 28, tzinfo=UTC)
_BUNDLED_APPROVAL_NOTE = (
    "Approved Project Master bundled default. The workflow uses only the audited "
    "local ComfyUI boundary and a publisher-documented SFW/NSFW or uncensored model."
)
_BUNDLED_FILENAMES = (
    "chroma1-flash-uncensored-text-to-image-project-master-import.json",
    "chroma1-flash-uncensored-image-to-image-project-master-import.json",
    "realvisxl-v5-nsfw-capable-text-to-image-project-master-import.json",
    "realvisxl-v5-nsfw-capable-image-to-image-project-master-import.json",
    "wan2.2-lightx2v-4step-uncensored-project-master-import.json",
    "wan2.2-lightx2v-4step-uncensored-image-to-video-project-master-import.json",
)
_BUNDLED_WORKFLOW_IDS = frozenset(
    {
        "comfy-wf-2c06b241ca8c0d574ac91cd5",
        "comfy-wf-d3aae436038bfaf695f88835",
        "comfy-wf-87170667b571ca8cc2c9e202",
        "comfy-wf-4ad28d9409072570197a7461",
        "comfy-wf-561795a382ee4f0e81ddd80b",
        "comfy-wf-657defdefbfe75d7e24d1899",
    }
)


class WorkflowService(Protocol):
    def list_workflows(self) -> tuple[WorkflowRevision, ...]: ...

    def add_workflow(self, revision: WorkflowRevision) -> None: ...


def load_bundled_workflows(
    directory: str | Path | None = None,
) -> tuple[WorkflowRevision, ...]:
    workflow_directory = (
        Path(directory).resolve()
        if directory is not None
        else _bundled_workflow_directory()
    )
    revisions: list[WorkflowRevision] = []
    for filename in _BUNDLED_FILENAMES:
        path = workflow_directory / filename
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Bundled ComfyUI workflow is unavailable: {filename}") from exc
        if not isinstance(document, dict):
            raise RuntimeError(f"Bundled ComfyUI workflow is invalid: {filename}")
        try:
            name = str(document["name"])
            purpose = document["purpose"]
            workflow = document["workflow"]
            raw_bindings = document["bindings"]
            if not isinstance(raw_bindings, list):
                raise TypeError("bindings must be a list")
            bindings = tuple(
                WorkflowBinding.model_validate(item) for item in raw_bindings
            )
            revision = WorkflowRevision.import_json(
                name,
                workflow,
                bindings,
                created_at=_BUNDLED_CREATED_AT,
                purpose=purpose,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Bundled ComfyUI workflow is invalid: {filename}") from exc
        revisions.append(revision)
    return tuple(revisions)


def seed_bundled_workflows(
    service: WorkflowService,
    persistence: SQLiteComfyStore,
    *,
    directory: str | Path | None = None,
) -> tuple[StoredWorkflow, ...]:
    """Install audited app-owned defaults without overriding a user's later decision."""
    loaded_ids = {revision.id for revision in service.list_workflows()}
    stored_by_id = {item.revision.id: item for item in persistence.list_workflows()}
    seeded: list[StoredWorkflow] = []

    for revision in load_bundled_workflows(directory):
        stored = stored_by_id.get(revision.id)
        if stored is None:
            stored = persistence.save_workflow(revision)
            stored = persistence.decide_workflow(
                revision.id,
                "approved",
                _BUNDLED_APPROVAL_NOTE,
            )
            stored_by_id[revision.id] = stored
        if revision.id not in loaded_ids:
            service.add_workflow(stored.revision)
            loaded_ids.add(revision.id)
        seeded.append(stored)

    return tuple(seeded)


def _bundled_workflow_directory() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str) and frozen_root:
        packaged = Path(frozen_root) / "project_master_workflow_data"
        if packaged.is_dir():
            return packaged
    return Path(__file__).resolve().parents[4] / "examples" / "comfyui"


def bundled_workflow_filenames() -> tuple[str, ...]:
    return _BUNDLED_FILENAMES


def bundled_workflow_ids() -> frozenset[str]:
    """Return the immutable revision IDs that identify curated app defaults."""
    return _BUNDLED_WORKFLOW_IDS


__all__ = [
    "bundled_workflow_filenames",
    "bundled_workflow_ids",
    "load_bundled_workflows",
    "seed_bundled_workflows",
]
