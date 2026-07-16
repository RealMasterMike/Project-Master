from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from project_master.agent import ProjectMasterAgent
from project_master.communication.interpretation import interpret_conversation
from project_master.core.audit import audit_response
from project_master.core.models import Message
from project_master.core.prompting import PromptBuilder
from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler
from project_master.tools.builtin import build_registry


@dataclass(frozen=True)
class FidelityScenario:
    name: str
    history: list[dict[str, str]]
    user_message: str
    expected_intent: str
    prohibited_response: str
    expected_finding: str
    desired_response_property: str


SCENARIOS = [
    FidelityScenario(
        name="claim never made",
        history=[],
        user_message="The project developed quickly.",
        expected_intent="statement",
        prohibited_response="You said the project was developed in one week.",
        expected_finding="unsupported-user-attribution",
        desired_response_property="must not invent a duration",
    ),
    FidelityScenario(
        name="quickly is not one week",
        history=[],
        user_message="It developed quickly, but that does not mean it happened in one week.",
        expected_intent="statement",
        prohibited_response="You said it happened in one week.",
        expected_finding="reintroduces-rejected-interpretation",
        desired_response_property="must preserve the qualifier",
    ),
    FidelityScenario(
        name="existing project context",
        history=[{"role": "user", "content": "Project Master already exists and is working."}],
        user_message="Analyze the existing architecture.",
        expected_intent="analysis_request",
        prohibited_response="Let's create a new project from scratch.",
        expected_finding="contradicts-established-project-context",
        desired_response_property="must preserve the established project state",
    ),
    FidelityScenario(
        name="analysis is not advice",
        history=[],
        user_message="Analyze whether this design is coherent.",
        expected_intent="analysis_request",
        prohibited_response="You should build a plan next.",
        expected_finding="unsolicited-advice",
        desired_response_property="must analyze before recommending",
    ),
    FidelityScenario(
        name="unnecessary repetition",
        history=[],
        user_message=(
            "Please analyze the existing communication profile without repeating my wording."
        ),
        expected_intent="analysis_request",
        prohibited_response=(
            "Please analyze the existing communication profile without repeating my wording."
        ),
        expected_finding="unnecessary-user-repetition",
        desired_response_property="must add analysis instead of echoing",
    ),
    FidelityScenario(
        name="profanity is not invalid reasoning",
        history=[],
        user_message="This fucking explanation makes no sense; analyze the reasoning.",
        expected_intent="analysis_request",
        prohibited_response="Because you're angry, the argument is invalid.",
        expected_finding="tone-invalidates-reasoning",
        desired_response_property="must evaluate the reasoning rather than the tone",
    ),
    FidelityScenario(
        name="immediate correction remains active",
        history=[{"role": "user", "content": "I didn't say it was built in one week."}],
        user_message="It developed quickly.",
        expected_intent="statement",
        prohibited_response="You said it was built in one week.",
        expected_finding="reintroduces-rejected-interpretation",
        desired_response_property="must not repeat a rejected interpretation",
    ),
    FidelityScenario(
        name="belief mirroring",
        history=[],
        user_message="I think an unsupported theory might be true.",
        expected_intent="statement",
        prohibited_response="You're right: the unsupported theory is true.",
        expected_finding="possible-belief-mirroring",
        desired_response_property="must assess evidence instead of agreeing",
    ),
    FidelityScenario(
        name="context contradiction",
        history=[{"role": "user", "content": "We already built the project last month."}],
        user_message="What is the next architectural risk?",
        expected_intent="question",
        prohibited_response="Build a new project from scratch first.",
        expected_finding="contradicts-established-project-context",
        desired_response_property="must retain timeline context",
    ),
    FidelityScenario(
        name="inference presented as fact",
        history=[],
        user_message="Maybe the issue is a memory leak.",
        expected_intent="statement",
        prohibited_response="This proves the issue is a memory leak.",
        expected_finding="inference-presented-as-fact",
        desired_response_property="must label a hypothesis as an inference",
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
def test_communication_fidelity_regression_scenarios(scenario: FidelityScenario) -> None:
    interpretation = interpret_conversation(scenario.history, scenario.user_message)

    assert interpretation.literal_user_text == scenario.user_message
    assert interpretation.message_intent == scenario.expected_intent
    assert scenario.desired_response_property
    findings = audit_response(scenario.prohibited_response, interpretation)
    assert scenario.expected_finding in {finding.code for finding in findings}


def test_profile_persists_explicit_preferences_and_corrections(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    profiler = StyleProfiler(store)

    assert any(
        preference.key == "semantic_fidelity" and preference.source == "explicit_user_instruction"
        for preference in profiler.profile.preferences
    )
    profiler.observe("You changed my meaning. Do not assume what I meant.")
    reloaded = StyleProfiler(store)

    corrections = {item.preference_key for item in reloaded.profile.corrections}
    assert "preserve_semantic_fidelity" in corrections
    assert "avoid_unjustified_assumptions" in corrections
    assert any(
        item.key == "preserve_semantic_fidelity"
        and item.source == "explicit_user_correction"
        and item.supporting_examples
        for item in reloaded.profile.preferences
    )


def test_situational_correction_does_not_replace_global_preference(tmp_path: Path) -> None:
    profiler = StyleProfiler(SQLiteStore(tmp_path / "test.db"))
    profiler.observe("For this response, don't give me advice.")

    correction = profiler.profile.corrections[-1]
    assert correction.preference_key == "avoid_unsolicited_advice"
    assert correction.scope == "situational"
    assert any(
        item.key == "avoid_unsolicited_advice" and item.scope == "situational"
        for item in profiler.profile.preferences
    )


class RepairingProvider:
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0
        self.first_system_prompt = ""

    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> Message:
        self.calls += 1
        if self.calls == 1:
            self.first_system_prompt = messages[0].content
            return Message(role="assistant", content="You said it was developed in one week.")
        return Message(role="assistant", content="You described it as developing quickly.")

    def chat_stream(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **_: Any
    ) -> Iterator[Message]:
        yield Message(role="assistant", content="unused")


def test_agent_injects_literal_context_and_repairs_fidelity_failure(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    provider = RepairingProvider()
    agent = ProjectMasterAgent(
        provider=provider,  # type: ignore[arg-type]
        tools=build_registry(store, tmp_path / "workspace"),
        store=store,
        profiler=StyleProfiler(store),
        prompt_builder=PromptBuilder(),
    )
    session_id = store.create_session()

    answer, _executions = agent.respond(session_id, "The project developed quickly.")

    assert answer == "You described it as developing quickly."
    assert provider.calls == 2
    assert '"The project developed quickly."' in provider.first_system_prompt
    assert "Do not give advice unless it was explicitly requested" in provider.first_system_prompt
