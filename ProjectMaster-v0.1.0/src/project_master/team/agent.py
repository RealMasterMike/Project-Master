from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from project_master.agent import ProjectMasterAgent
from project_master.core.cancellation import CancellationToken
from project_master.core.models import ToolExecution
from project_master.orchestration.models import ProjectSpec, RoleSpec, RunSpec
from project_master.orchestration.store import OrchestrationStore
from project_master.team.catalog import OllamaModelCatalog
from project_master.team.council import SequentialCouncil
from project_master.team.models import (
    CouncilRequest,
    CouncilResult,
    CouncilStatus,
    TeamActivityEvent,
    TeamPlan,
)

AgentFactory = Callable[[str | None], ProjectMasterAgent]


@dataclass(slots=True)
class TeamResponse:
    answer: str
    tools: list[ToolExecution]
    run_id: str
    council: CouncilResult
    activities: list[dict[str, Any]]


class ProjectMasterTeam:
    """Coordinate advisory models once, then delegate all tools to one lead agent."""

    def __init__(
        self,
        catalog: OllamaModelCatalog,
        council: SequentialCouncil,
        agent_factory: AgentFactory,
        orchestration: OrchestrationStore,
        *,
        workspace_root: Path,
        configured_model: str,
    ) -> None:
        self.catalog = catalog
        self.council = council
        self.agent_factory = agent_factory
        self.orchestration = orchestration
        self.workspace_root = workspace_root
        self.configured_model = configured_model

    def respond(
        self,
        session_id: str,
        user_text: str,
        *,
        preferred_lead: str | None = None,
        supplemental_context: str = "",
        project_id: str | None = None,
        allow_mutations: bool = False,
        allow_web_search: bool = False,
    ) -> TeamResponse:
        models = self.catalog.load(refresh=True)
        _project_id, run_id = self._start_run(
            user_text,
            project_id=project_id,
            allow_mutations=allow_mutations,
            allow_web_search=allow_web_search,
        )
        plan = self.council.role_assigner.assign(
            models,
            preferred_lead or self.configured_model,
            required_purpose="team",
        )
        self._persist_roles(
            run_id,
            plan,
            allow_mutations=allow_mutations,
            allow_web_search=allow_web_search,
        )
        activities: list[dict[str, Any]] = []
        council_run = self.council.run(
            CouncilRequest(
                prompt=user_text,
                context=self._run_context(session_id, supplemental_context),
                run_id=run_id,
            ),
            models,
            preferred_lead=preferred_lead or self.configured_model,
        )
        for event in council_run.events:
            activities.append(event.to_dict())
            self._persist_activity(event)
        result = council_run.result
        if result.status is CouncilStatus.CANCELLED:
            self.orchestration.set_run_status(run_id, "cancelled", "Council cancelled")
            return TeamResponse("", [], run_id, result, activities)
        if result.status is CouncilStatus.FAILED:
            self.orchestration.set_run_status(run_id, "failed", "All team models failed")
            return TeamResponse("", [], run_id, result, activities)

        lead_model = result.plan.lead.model_tag if result.plan.lead else preferred_lead
        try:
            answer, tools = self.agent_factory(lead_model).respond(
                session_id,
                user_text,
                supplemental_context=_combine_context(
                    supplemental_context,
                    _advisory_context(result),
                ),
                allow_mutations=allow_mutations,
                allow_web_search=allow_web_search,
            )
        except Exception as exc:
            self.orchestration.append_event(
                run_id,
                "lead_failed",
                "MASTER lead failed before delivery",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            self.orchestration.set_run_status(
                run_id,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
            raise
        for execution in tools:
            self.orchestration.append_event(
                run_id,
                "tool",
                execution.name,
                {"tool": asdict(execution)},
            )
        self.orchestration.append_event(
            run_id,
            "delivery",
            "MASTER delivered the final response",
            {"council_status": result.status.value},
        )
        self.orchestration.set_run_status(run_id, "complete")
        return TeamResponse(answer, tools, run_id, result, activities)

    def respond_stream(
        self,
        session_id: str,
        user_text: str,
        *,
        preferred_lead: str | None = None,
        cancellation: CancellationToken | None = None,
        supplemental_context: str = "",
        project_id: str | None = None,
        allow_mutations: bool = False,
        allow_web_search: bool = False,
    ) -> Iterator[dict[str, Any]]:
        models = self.catalog.load(refresh=True)
        _project_id, run_id = self._start_run(
            user_text,
            project_id=project_id,
            allow_mutations=allow_mutations,
            allow_web_search=allow_web_search,
        )
        plan = self.council.role_assigner.assign(
            models,
            preferred_lead or self.configured_model,
            required_purpose="team",
        )
        self._persist_roles(
            run_id,
            plan,
            allow_mutations=allow_mutations,
            allow_web_search=allow_web_search,
        )
        terminal: CouncilResult | None = None

        for activity in self.council.run_stream(
            CouncilRequest(
                prompt=user_text,
                context=self._run_context(session_id, supplemental_context),
                run_id=run_id,
            ),
            models,
            preferred_lead=preferred_lead or self.configured_model,
            cancellation=cancellation,
        ):
            self._persist_activity(activity)
            payload = activity.to_dict()
            yield {"type": "team", "run_id": run_id, "activity": payload}
            if activity.result is not None:
                terminal = activity.result

        if terminal is None:
            self.orchestration.set_run_status(
                run_id,
                "failed",
                "Council ended without a terminal result",
            )
            yield {
                "type": "error",
                "error": "The model council ended without a terminal result.",
                "retryable": True,
                "run_id": run_id,
            }
            return
        if terminal.status is CouncilStatus.CANCELLED:
            self._persist_cancelled_user_turn(session_id, user_text)
            self.orchestration.set_run_status(run_id, "cancelled", "Council cancelled")
            yield {"type": "cancelled", "run_id": run_id}
            return
        if terminal.status is CouncilStatus.FAILED:
            self._persist_cancelled_user_turn(session_id, user_text)
            self.orchestration.set_run_status(run_id, "failed", "All team models failed")
            yield {
                "type": "error",
                "error": "Every available team model failed before MASTER could synthesize.",
                "retryable": True,
                "run_id": run_id,
            }
            return

        lead_model = terminal.plan.lead.model_tag if terminal.plan.lead else preferred_lead
        self.orchestration.append_event(
            run_id,
            "lead_started",
            "MASTER lead started the authorized tool loop",
            {"model": lead_model},
        )
        yield {
            "type": "team",
            "run_id": run_id,
            "activity": {
                "run_id": run_id,
                "type": "lead_started",
                "message": "MASTER lead started the authorized tool loop",
                "member": terminal.plan.lead.to_dict() if terminal.plan.lead else None,
            },
        }
        try:
            for event in self.agent_factory(lead_model).respond_stream(
                session_id,
                user_text,
                cancellation=cancellation,
                supplemental_context=_combine_context(
                    supplemental_context,
                    _advisory_context(terminal),
                ),
                allow_mutations=allow_mutations,
                allow_web_search=allow_web_search,
            ):
                event["run_id"] = run_id
                if event["type"] == "tool":
                    tool_payload = event.get("tool")
                    self.orchestration.append_event(
                        run_id,
                        "tool",
                        str(tool_payload.get("name", "tool"))
                        if isinstance(tool_payload, dict)
                        else "tool",
                        {"tool": tool_payload},
                    )
                elif event["type"] == "done":
                    self.orchestration.append_event(
                        run_id,
                        "delivery",
                        "MASTER delivered the final response",
                        {"council_status": terminal.status.value},
                    )
                    self.orchestration.set_run_status(run_id, "complete")
                elif event["type"] == "cancelled":
                    self.orchestration.set_run_status(
                        run_id,
                        "cancelled",
                        "Lead cancelled",
                    )
                yield event
        except Exception as exc:
            self.orchestration.append_event(
                run_id,
                "lead_failed",
                "MASTER lead failed before delivery",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            self.orchestration.set_run_status(
                run_id,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
            raise

    def catalog_status(self) -> list[dict[str, Any]]:
        return [model.to_dict() for model in self.catalog.load()]

    def _start_run(
        self,
        objective: str,
        *,
        project_id: str | None = None,
        allow_mutations: bool = False,
        allow_web_search: bool = False,
    ) -> tuple[str, str]:
        selected_project_id = project_id
        if selected_project_id is None:
            selected_project_id = self.orchestration.get_or_create_project(
                ProjectSpec(
                    name="General",
                    root_path=str(self.workspace_root),
                    description="Default Project Master workspace",
                    metadata={"system_default": True},
                )
            )
        elif self.orchestration.get_project(selected_project_id) is None:
            raise KeyError(f"Unknown project: {selected_project_id}")
        run_id = self.orchestration.create_run(
            RunSpec(
                project_id=selected_project_id,
                kind="team_chat",
                objective=objective,
                mode="team",
                metadata={
                    "chat_mode": "team",
                    "allow_mutations": allow_mutations,
                    "allow_web_search": allow_web_search,
                    "tool_authorization": (
                        "explicit_mutations_allowed" if allow_mutations else "read_only"
                    ),
                    "online_search_authorization": (
                        "explicit_online_search_allowed"
                        if allow_web_search
                        else "local_only"
                    ),
                },
            )
        )
        self.orchestration.append_event(
            run_id,
            "tool_authorization",
            (
                "Explicit mutation authorization granted for this team chat"
                if allow_mutations
                else "Team chat restricted to read-only tools"
            ),
            {
                "chat_mode": "team",
                "allow_mutations": allow_mutations,
                "allow_web_search": allow_web_search,
                "tool_authorization": (
                    "explicit_mutations_allowed" if allow_mutations else "read_only"
                ),
                "online_search_authorization": (
                    "explicit_online_search_allowed"
                    if allow_web_search
                    else "local_only"
                ),
            },
        )
        self.orchestration.set_run_status(run_id, "running")
        return selected_project_id, run_id

    def _persist_roles(
        self,
        run_id: str,
        plan: TeamPlan,
        *,
        allow_mutations: bool = False,
        allow_web_search: bool = False,
    ) -> None:
        for member in plan.members:
            self.orchestration.add_role(
                RoleSpec(
                    run_id=run_id,
                    role=member.role.value,
                    model=member.model_tag,
                    model_digest=member.member_id.removeprefix("digest:"),
                    assignment=(
                        "Integrate specialist work and own authorized tools"
                        if member is plan.lead
                        else f"Contribute as the {member.role.value} specialist"
                    ),
                    permissions=(
                        [
                            "registered_read_only_tools",
                            *(
                                ["registered_mutating_tools"]
                                if allow_mutations
                                else []
                            ),
                            *(
                                ["registered_external_network_tools"]
                                if allow_web_search
                                else []
                            ),
                        ]
                        if member is plan.lead
                        else ["advisory_context_only"]
                    ),
                    budget={
                        "sequential": True,
                        "allow_mutations": allow_mutations if member is plan.lead else False,
                        "allow_web_search": (
                            allow_web_search if member is plan.lead else False
                        ),
                    },
                )
            )

    def _persist_activity(self, event: TeamActivityEvent) -> None:
        payload = event.to_dict()
        self.orchestration.append_event(
            event.run_id,
            event.kind.value,
            event.message,
            payload,
        )

    def _conversation_context(self, session_id: str) -> str:
        messages = self.agent_factory(None).store.recent_messages(session_id, limit=12)
        return "\n".join(
            f"{item['role'].upper()}: {item['content']}" for item in messages
        )[-12_000:]

    def _run_context(self, session_id: str, supplemental_context: str) -> str:
        conversation = self._conversation_context(session_id)
        return _combine_context(conversation, supplemental_context)[-24_000:]

    def _persist_cancelled_user_turn(self, session_id: str, user_text: str) -> None:
        store = self.agent_factory(None).store
        history = store.recent_messages(session_id, limit=1)
        if not history or history[-1] != {"role": "user", "content": user_text}:
            store.add_message(session_id, "user", user_text)


def _advisory_context(result: CouncilResult) -> str:
    lines = [
        f"Council status: {result.status.value}",
        "Specialist work products:",
    ]
    for worker in result.workers:
        if worker.output:
            lines.append(
                f"\n[{worker.member.role.value} | {worker.member.model_tag}]\n{worker.output}"
            )
        elif worker.failure:
            lines.append(
                f"\n[{worker.member.role.value} | {worker.member.model_tag}] "
                f"{worker.status.value}: {worker.failure.message}"
            )
    if result.final:
        lines.append(f"\n[Council synthesis]\n{result.final}")
    return "\n".join(lines)


def _combine_context(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if part.strip())
