from collections.abc import Iterator
from pathlib import Path
from typing import Any

from project_master.agent import ProjectMasterAgent
from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.memory.store import SQLiteStore
from project_master.orchestration.models import ProjectSpec
from project_master.orchestration.store import OrchestrationStore
from project_master.personality.profile import StyleProfiler
from project_master.team.agent import ProjectMasterTeam
from project_master.team.catalog import OllamaModelCatalog
from project_master.team.council import SequentialCouncil
from project_master.tools.builtin import build_registry


class CatalogProvider:
    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "lead",
                "digest": "lead-digest",
                "size": 8_000,
                "details": {"family": "gemma"},
            },
            {
                "name": "worker",
                "digest": "worker-digest",
                "size": 4_000,
                "details": {"family": "llama"},
            },
        ]

    def show_model(self, model: str) -> dict[str, Any]:
        capabilities = ["completion", "tools"] if model == "lead" else ["completion"]
        return {"capabilities": capabilities, "details": {"family": "test"}}


class AdvisoryProvider:
    def __init__(self, model: str, calls: list[tuple[str, Any]]) -> None:
        self.model = model
        self.calls = calls

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, object]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        self.calls.append((self.model, tools))
        yield Message(role="assistant", content=f"{self.model} advisory output")


class ToolLeadProvider:
    model = "lead"

    def __init__(self) -> None:
        self.round = 0
        self.seen_supplemental = False
        self.seen_tool_names: list[set[str]] = []

    def health(self) -> dict[str, Any]:
        return {"ok": True, "models": ["lead", "worker"], "configured_model": "lead"}

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        self.seen_tool_names.append(
            {str(schema["function"]["name"]) for schema in tools or []}
        )
        return Message(role="assistant", content="The result is 4.")

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        self.seen_tool_names.append(
            {str(schema["function"]["name"]) for schema in tools or []}
        )
        self.seen_supplemental = any(
            "advisory output from other local models" in message.content for message in messages
        )
        self.round += 1
        if self.round == 1:
            yield Message(
                role="assistant",
                content="Planning text that must stay out of the final bubble.",
                tool_calls=[
                    {
                        "function": {
                            "name": "calculator",
                            "arguments": {"expression": "2+2"},
                        }
                    }
                ],
            )
            return
        yield Message(role="assistant", content="The result is 4.")


def test_team_stream_is_durable_tool_safe_and_final_only(tmp_path: Path) -> None:
    sqlite = SQLiteStore(tmp_path / "master.db")
    orchestration = OrchestrationStore(sqlite)
    profiler = StyleProfiler(sqlite)
    lead_provider = ToolLeadProvider()
    agent = ProjectMasterAgent(
        provider=lead_provider,
        tools=build_registry(sqlite, tmp_path / "workspace"),
        store=sqlite,
        profiler=profiler,
        prompt_builder=PromptBuilder(),
    )
    advisory_calls: list[tuple[str, Any]] = []
    team = ProjectMasterTeam(
        catalog=OllamaModelCatalog(CatalogProvider()),
        council=SequentialCouncil(
            lambda model: AdvisoryProvider(model, advisory_calls),
        ),
        agent_factory=lambda _model: agent,
        orchestration=orchestration,
        workspace_root=tmp_path / "workspace",
        configured_model="lead",
    )
    session_id = sqlite.create_session("Team test")
    selected_project_id = orchestration.create_project(
        ProjectSpec(name="Selected", root_path=str(tmp_path / "workspace"))
    )

    events = list(
        team.respond_stream(
            session_id,
            "What is 2 + 2?",
            preferred_lead="lead",
            project_id=selected_project_id,
        )
    )

    assert advisory_calls
    assert all(tools is None for _model, tools in advisory_calls)
    assert any(event["type"] == "team" for event in events)
    assert any(event["type"] == "tool" for event in events)
    token_text = "".join(
        str(event.get("content", "")) for event in events if event["type"] == "token"
    )
    assert token_text == "The result is 4."
    assert "Planning text" not in token_text
    assert lead_provider.seen_supplemental
    assert all(
        "workspace_write" not in tool_names
        for tool_names in lead_provider.seen_tool_names
    )
    assert sqlite.recent_messages(session_id) == [
        {"role": "user", "content": "What is 2 + 2?"},
        {"role": "assistant", "content": "The result is 4."},
    ]

    runs = orchestration.list_runs(selected_project_id)
    assert runs[0]["status"] == "complete"
    assert runs[0]["metadata"]["chat_mode"] == "team"
    assert runs[0]["metadata"]["allow_mutations"] is False
    run_events = orchestration.list_events(runs[0]["id"])
    assert any(event["event_type"] == "tool" for event in run_events)
    assert any(event["event_type"] == "delivery" for event in run_events)
    assert any(event["event_type"] == "tool_authorization" for event in run_events)

    lead_provider.round = 0
    prior_schema_count = len(lead_provider.seen_tool_names)
    authorized_session = sqlite.create_session("Authorized team test")
    authorized_events = list(
        team.respond_stream(
            authorized_session,
            "You may update the selected project.",
            preferred_lead="lead",
            project_id=selected_project_id,
            allow_mutations=True,
        )
    )

    assert authorized_events[-1]["type"] == "done"
    authorized_schemas = lead_provider.seen_tool_names[prior_schema_count:]
    assert authorized_schemas
    assert all("workspace_write" in tool_names for tool_names in authorized_schemas)
    authorized_run = orchestration.list_runs(selected_project_id)[0]
    assert authorized_run["metadata"]["chat_mode"] == "team"
    assert authorized_run["metadata"]["allow_mutations"] is True
    lead_role = next(
        role
        for role in orchestration.list_roles(authorized_run["id"])
        if role["role"] == "lead"
    )
    assert "registered_mutating_tools" in lead_role["permissions"]
