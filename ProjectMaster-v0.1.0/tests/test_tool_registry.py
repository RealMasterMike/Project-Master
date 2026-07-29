from __future__ import annotations

from project_master.tools.base import Tool, ToolRegistry


def _schema_names(registry: ToolRegistry) -> set[str]:
    return {
        str(schema["function"]["name"])
        for schema in registry.schemas()
    }


def test_rootless_project_hides_and_blocks_workspace_tools_then_cleans_up() -> None:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="workspace_probe",
            description="Test-only workspace probe.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {"project_id": registry.project_id},
            requires_workspace=True,
        )
    )
    registry.register(
        Tool(
            name="project_probe",
            description="Test-only project probe.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda _args: {
                "project_id": registry.project_id,
                "workspace_available": registry.workspace_available,
            },
        )
    )

    assert registry.project_id is None
    assert registry.workspace_available is True
    assert "workspace_probe" in _schema_names(registry)

    with registry.project_scope("creator-project", workspace_available=False):
        assert registry.project_id == "creator-project"
        assert registry.workspace_available is False
        assert "workspace_probe" not in _schema_names(registry)
        assert "project_probe" in _schema_names(registry)

        workspace_ok, workspace_result = registry.execute("workspace_probe", {})
        project_ok, project_result = registry.execute("project_probe", {})

        assert workspace_ok is False
        assert "PermissionError" in workspace_result
        assert "has no local workspace" in workspace_result
        assert project_ok is True
        assert '"project_id": "creator-project"' in project_result
        assert '"workspace_available": false' in project_result

        inventory = {
            item["name"]: item
            for item in registry.inventory()
        }
        assert inventory["workspace_probe"]["requires_workspace"] is True
        assert inventory["workspace_probe"]["available_in_current_scope"] is False
        assert inventory["project_probe"]["available_in_current_scope"] is True

    assert registry.project_id is None
    assert registry.workspace_available is True
    assert "workspace_probe" in _schema_names(registry)
    workspace_ok, _workspace_result = registry.execute("workspace_probe", {})
    assert workspace_ok is True
