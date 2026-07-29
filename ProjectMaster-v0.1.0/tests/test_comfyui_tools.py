from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from project_master.integrations.comfyui.defaults import load_bundled_workflows
from project_master.integrations.comfyui.workflow import WorkflowRevision
from project_master.tools.base import ToolRegistry
from project_master.tools.comfyui import register_comfyui_tools


class _Result:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


class _Service:
    def __init__(self) -> None:
        self.submissions: list[dict[str, Any]] = []

    async def submit_workflow(
        self,
        profile_id: str,
        revision_id: str,
        values: dict[str, Any],
        *,
        project_id: str | None = None,
    ) -> _Result:
        self.submissions.append(
            {
                "profile_id": profile_id,
                "revision_id": revision_id,
                "values": values,
                "project_id": project_id,
            }
        )
        return _Result({"job_id": "comfy-job-one", "project_id": project_id})


class _Persistence:
    def __init__(self, revision: WorkflowRevision) -> None:
        self.stored = SimpleNamespace(
            revision=revision,
            trust_state="approved",
        )

    def list_workflows(self) -> tuple[object, ...]:
        return (self.stored,)

    def get_workflow(self, revision_id: str) -> object:
        assert revision_id == self.stored.revision.id
        return self.stored


def test_comfy_tools_expose_purpose_and_scope_runs_to_the_selected_project() -> None:
    revision = WorkflowRevision.import_json(
        "Image to video",
        {"1": {"class_type": "SaveVideo", "inputs": {}}},
        purpose="video",
    )
    service = _Service()
    registry = ToolRegistry()
    register_comfyui_tools(
        registry,
        service,  # type: ignore[arg-type]
        _Persistence(revision),  # type: ignore[arg-type]
    )

    listed_ok, listed_payload = registry.execute("comfy_workflows_list", {})
    with (
        registry.project_scope("creator-project", workspace_available=False),
        registry.mutation_scope(True),
    ):
        run_ok, run_payload = registry.execute(
            "comfy_workflow_run",
            {
                "profile_id": "local",
                "workflow_revision_id": revision.id,
                "values": {},
            },
        )

    assert listed_ok
    listed_workflow = json.loads(listed_payload)["workflows"][0]
    assert listed_workflow["purpose"] == "video"
    assert listed_workflow["curated_default"] is False
    assert run_ok
    assert json.loads(run_payload)["project_id"] == "creator-project"
    assert service.submissions == [
        {
            "profile_id": "local",
            "revision_id": revision.id,
            "values": {},
            "project_id": "creator-project",
        }
    ]
    assert registry.project_id is None


def test_comfy_tools_label_a_deterministic_bundled_revision_curated() -> None:
    revision = load_bundled_workflows()[0]
    registry = ToolRegistry()
    register_comfyui_tools(
        registry,
        _Service(),  # type: ignore[arg-type]
        _Persistence(revision),  # type: ignore[arg-type]
    )

    ok, payload = registry.execute("comfy_workflows_list", {})

    assert ok
    workflow = json.loads(payload)["workflows"][0]
    assert workflow["id"] == revision.id
    assert workflow["curated_default"] is True
