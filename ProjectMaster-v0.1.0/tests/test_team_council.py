from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.team.council import SequentialCouncil
from project_master.team.models import (
    ActivityKind,
    CatalogModel,
    CouncilLimits,
    CouncilRequest,
    CouncilStatus,
    ModelDetails,
    TeamActivityEvent,
    WorkerStatus,
)


@dataclass
class ProviderBehavior:
    fragments: tuple[Message, ...] = ()
    error: Exception | None = None
    cancel_before_yield: bool = False


class FakeProvider:
    def __init__(
        self,
        model: str,
        behavior: ProviderBehavior,
        calls: list[dict[str, Any]],
    ) -> None:
        self.model = model
        self.behavior = behavior
        self.calls = calls

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, object]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        self.calls.append(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            }
        )
        if self.behavior.error is not None:
            raise self.behavior.error
        if self.behavior.cancel_before_yield and cancellation is not None:
            cancellation.cancel()
        yield from self.behavior.fragments


def _model(
    tag: str,
    *,
    capabilities: set[str] | None = None,
    size: int = 1,
) -> CatalogModel:
    return CatalogModel(
        physical_id=f"digest:{tag}",
        tags=(tag,),
        digest=tag,
        size_bytes=size,
        capabilities=frozenset(capabilities or {"completion"}),
        details=ModelDetails(family="test"),
    )


def _council(
    behaviors: dict[str, ProviderBehavior],
    calls: list[dict[str, Any]],
    *,
    limits: CouncilLimits | None = None,
) -> SequentialCouncil:
    return SequentialCouncil(
        lambda model: FakeProvider(model, behaviors[model], calls),
        limits=limits,
    )


def test_council_runs_each_physical_model_sequentially_without_tools() -> None:
    calls: list[dict[str, Any]] = []
    behaviors = {
        "alpha": ProviderBehavior((Message(role="assistant", content="alpha output"),)),
        "beta": ProviderBehavior((Message(role="assistant", content="beta output"),)),
        "lead": ProviderBehavior((Message(role="assistant", content="final synthesis"),)),
    }
    council = _council(behaviors, calls)
    models = [
        _model("lead", capabilities={"completion", "tools"}, size=12),
        _model("beta", capabilities={"completion", "thinking"}, size=7),
        _model("alpha", size=5),
    ]

    run = council.run(
        CouncilRequest(prompt="Build the feature", context="Relevant project context", run_id="r1"),
        models,
        preferred_lead="lead",
    )

    assert run.result.status is CouncilStatus.COMPLETE
    assert run.result.final == "final synthesis"
    assert [call["model"] for call in calls] == ["alpha", "beta", "lead"]
    assert all(call["tools"] is None for call in calls)
    assert [event.sequence for event in run.events] == list(range(1, len(run.events) + 1))
    assert all(isinstance(event, TeamActivityEvent) for event in run.events)
    assert run.events[0].kind is ActivityKind.COUNCIL_STARTED
    assert run.events[-1].kind is ActivityKind.COUNCIL_COMPLETED
    assert "alpha output" not in calls[1]["messages"][-1].content
    assert "alpha output" in calls[2]["messages"][-1].content
    assert "beta output" in calls[2]["messages"][-1].content
    assert "hidden chain-of-thought" in calls[0]["messages"][0].content


def test_council_bounds_inputs_worker_results_and_final_synthesis() -> None:
    calls: list[dict[str, Any]] = []
    limits = CouncilLimits(
        max_models=2,
        max_request_chars=30,
        max_context_chars=20,
        max_worker_output_chars=40,
        max_synthesis_context_chars=60,
        max_final_output_chars=50,
        max_error_chars=20,
    )
    behaviors = {
        "worker": ProviderBehavior((Message(role="assistant", content="W" * 200),)),
        "lead": ProviderBehavior((Message(role="assistant", content="L" * 200),)),
    }
    run = _council(behaviors, calls, limits=limits).run(
        CouncilRequest(prompt="P" * 100, context="C" * 100, run_id="bounded"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("worker"),
        ],
    )

    worker = next(item for item in run.result.workers if item.member.model_tag == "worker")
    assert len(worker.output) == limits.max_worker_output_chars
    assert worker.output.endswith("[truncated]")
    assert worker.output_truncated is True
    assert len(run.result.final) == limits.max_final_output_chars
    assert run.result.final.endswith("[truncated]")
    assert run.result.final_truncated is True
    worker_prompt = calls[0]["messages"][-1].content
    assert "P" * 31 not in worker_prompt
    assert "C" * 21 not in worker_prompt
    synthesis_prompt = calls[1]["messages"][-1].content
    assert "W" * 41 not in synthesis_prompt


def test_worker_failure_is_visible_and_does_not_stop_remaining_models() -> None:
    calls: list[dict[str, Any]] = []
    behaviors = {
        "bad": ProviderBehavior(error=RuntimeError("backend exploded with a long explanation")),
        "good": ProviderBehavior((Message(role="assistant", content="usable"),)),
        "lead": ProviderBehavior((Message(role="assistant", content="degraded synthesis"),)),
    }
    limits = CouncilLimits(max_error_chars=24)
    run = _council(behaviors, calls, limits=limits).run(
        CouncilRequest(prompt="Evaluate", run_id="failure"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("bad"),
            _model("good"),
        ],
    )

    assert [call["model"] for call in calls] == ["bad", "good", "lead"]
    assert run.result.status is CouncilStatus.PARTIAL
    failed = next(item for item in run.result.workers if item.member.model_tag == "bad")
    assert failed.status is WorkerStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "provider_error"
    assert len(failed.failure.message) <= limits.max_error_chars
    assert any(event.kind is ActivityKind.WORKER_FAILED for event in run.events)
    assert any(event.kind is ActivityKind.WORKER_COMPLETED for event in run.events)


def test_lead_failure_returns_bounded_unverified_worker_fallback() -> None:
    calls: list[dict[str, Any]] = []
    behaviors = {
        "worker": ProviderBehavior((Message(role="assistant", content="worker evidence"),)),
        "lead": ProviderBehavior(error=RuntimeError("lead unavailable")),
    }

    run = _council(behaviors, calls).run(
        CouncilRequest(prompt="Evaluate", run_id="lead-failure"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("worker"),
        ],
    )

    assert run.result.status is CouncilStatus.PARTIAL
    assert "completion is not verified" in run.result.final
    assert "worker evidence" in run.result.final
    assert run.result.failure is not None
    assert run.result.failure.code == "provider_error"
    assert any(event.kind is ActivityKind.SYNTHESIS_FAILED for event in run.events)


def test_pre_cancelled_council_never_starts_a_provider() -> None:
    calls: list[dict[str, Any]] = []
    token = CancellationToken()
    token.cancel()
    run = _council(
        {"lead": ProviderBehavior((Message(role="assistant", content="unused"),))},
        calls,
    ).run(
        CouncilRequest(prompt="Do not start", run_id="pre-cancel"),
        [_model("lead", capabilities={"completion", "tools"})],
        cancellation=token,
    )

    assert calls == []
    assert run.result.status is CouncilStatus.CANCELLED
    assert run.events[-1].kind is ActivityKind.COUNCIL_CANCELLED


def test_cancellation_during_worker_stops_before_later_models() -> None:
    calls: list[dict[str, Any]] = []
    token = CancellationToken()
    behaviors = {
        "first": ProviderBehavior(
            (Message(role="assistant", content="ignored"),),
            cancel_before_yield=True,
        ),
        "second": ProviderBehavior((Message(role="assistant", content="unused"),)),
        "lead": ProviderBehavior((Message(role="assistant", content="unused"),)),
    }
    run = _council(behaviors, calls).run(
        CouncilRequest(prompt="Stop safely", run_id="cancel"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("first"),
            _model("second"),
        ],
        cancellation=token,
    )

    assert [call["model"] for call in calls] == ["first"]
    assert run.result.status is CouncilStatus.CANCELLED
    assert any(event.kind is ActivityKind.WORKER_CANCELLED for event in run.events)


def test_tool_call_from_advisory_worker_is_rejected_and_never_executed() -> None:
    calls: list[dict[str, Any]] = []
    behaviors = {
        "worker": ProviderBehavior(
            (
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[{"function": {"name": "workspace_write", "arguments": {}}}],
                ),
            )
        ),
        "lead": ProviderBehavior((Message(role="assistant", content="safe synthesis"),)),
    }
    run = _council(behaviors, calls).run(
        CouncilRequest(prompt="Inspect only", run_id="tools"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("worker"),
        ],
    )

    attempted = next(item for item in run.result.workers if item.member.model_tag == "worker")
    assert attempted.status is WorkerStatus.FAILED
    assert attempted.failure is not None
    assert attempted.failure.code == "unexpected_tool_call"
    assert all(call["tools"] is None for call in calls)
    assert run.result.status is CouncilStatus.PARTIAL


def test_private_reasoning_envelopes_are_not_published_or_sent_to_synthesis() -> None:
    calls: list[dict[str, Any]] = []
    behaviors = {
        "worker": ProviderBehavior(
            (
                Message(
                    role="assistant",
                    content="<think>private worker reasoning</think>public worker result",
                ),
            )
        ),
        "lead": ProviderBehavior(
            (
                Message(
                    role="assistant",
                    content="<analysis>private lead reasoning</analysis>public final",
                ),
            )
        ),
    }
    run = _council(behaviors, calls).run(
        CouncilRequest(prompt="Keep reasoning private", run_id="private-reasoning"),
        [
            _model("lead", capabilities={"completion", "tools"}, size=10),
            _model("worker"),
        ],
    )

    worker = next(item for item in run.result.workers if item.member.model_tag == "worker")
    assert worker.output == "public worker result"
    assert "private worker reasoning" not in calls[-1]["messages"][-1].content
    assert run.result.final == "public final"
    assert "private" not in run.events[-1].to_dict()["result"]["final"]


def test_no_conversational_models_produces_honest_failed_result() -> None:
    calls: list[dict[str, Any]] = []
    run = _council({}, calls).run(
        CouncilRequest(prompt="Cannot run", run_id="no-chat"),
        [_model("embed", capabilities={"embedding"})],
    )

    assert calls == []
    assert run.result.status is CouncilStatus.FAILED
    assert run.result.failure is not None
    assert run.result.failure.code == "no_conversational_model"
    assert run.result.workers[0].status is WorkerStatus.SKIPPED
