from __future__ import annotations

from pathlib import Path

from project_master.integrations.comfyui.defaults import (
    bundled_workflow_filenames,
    bundled_workflow_ids,
    load_bundled_workflows,
    seed_bundled_workflows,
)
from project_master.integrations.comfyui.persistence import SQLiteComfyStore
from project_master.integrations.comfyui.workflow import WorkflowRevision
from project_master.memory.store import SQLiteStore


class WorkflowService:
    def __init__(self) -> None:
        self.revisions: dict[str, WorkflowRevision] = {}

    def list_workflows(self) -> tuple[WorkflowRevision, ...]:
        return tuple(self.revisions.values())

    def add_workflow(self, revision: WorkflowRevision) -> None:
        if revision.id in self.revisions:
            raise AssertionError("duplicate workflow")
        self.revisions[revision.id] = revision


def example_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "examples" / "comfyui"


def test_bundled_defaults_cover_all_four_creator_generation_modes() -> None:
    revisions = load_bundled_workflows(example_directory())

    assert len(revisions) == len(bundled_workflow_filenames()) == 6
    assert bundled_workflow_ids() == frozenset(
        revision.id for revision in revisions
    )
    assert [revision.purpose for revision in revisions] == [
        "image",
        "image",
        "image",
        "image",
        "video",
        "video",
    ]
    assert [
        any(binding.value_type == "image_asset" for binding in revision.bindings)
        for revision in revisions
    ] == [False, True, False, True, False, True]
    assert all(
        "NSFW" in revision.name or "Uncensored" in revision.name
        for revision in revisions
    )


def test_chroma_sorts_ahead_of_realvisxl_so_it_wins_automatic_selection() -> None:
    # Two curated defaults now exist per image operation. The Creator sorts curated
    # entries by name and auto-selects the first, so Chroma only stays the automatic
    # default while it sorts ahead of RealVisXL. Renaming either one would silently
    # flip the default, which is exactly what this test is here to catch.
    revisions = load_bundled_workflows(example_directory())
    image_names = sorted(
        revision.name for revision in revisions if revision.purpose == "image"
    )

    assert image_names[0].startswith("Chroma1-Flash")
    assert image_names[1].startswith("Chroma1-Flash")


def test_bundled_defaults_seed_once_without_overriding_user_rejection(
    tmp_path: Path,
) -> None:
    persistence = SQLiteComfyStore(SQLiteStore(tmp_path / "master.db"))
    service = WorkflowService()

    first = seed_bundled_workflows(
        service,
        persistence,
        directory=example_directory(),
    )
    rejected_id = first[0].revision.id
    persistence.decide_workflow(rejected_id, "rejected", "User disabled this default.")
    second = seed_bundled_workflows(
        service,
        persistence,
        directory=example_directory(),
    )

    assert len(service.revisions) == 6
    assert len(persistence.list_workflows()) == 6
    assert all(item.trust_state == "approved" for item in first)
    assert next(item for item in second if item.revision.id == rejected_id).trust_state == (
        "rejected"
    )
