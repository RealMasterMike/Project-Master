from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from typing import Protocol
from uuid import uuid4

from project_master.core.cancellation import CancellationToken
from project_master.core.models import Message
from project_master.team.models import (
    ActivityKind,
    CatalogModel,
    CouncilLimits,
    CouncilRequest,
    CouncilResult,
    CouncilRun,
    CouncilStatus,
    ProviderFailure,
    TeamActivityEvent,
    TeamMember,
    TeamPlan,
    TeamRole,
    WorkerResult,
    WorkerStatus,
    _bounded_text,
)
from project_master.team.roles import CapabilityAwareRoleAssigner


class CouncilProvider(Protocol):
    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, object]] | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[Message]:
        """Yield a model response. Council calls always pass tools=None."""


ProviderFactory = Callable[[str], CouncilProvider]

_PRIVATE_REASONING_BLOCK = re.compile(
    r"<(?:think|analysis)\b[^>]*>.*?</(?:think|analysis)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_PRIVATE_REASONING = re.compile(
    r"<(?:think|analysis)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_REASONING_TAG = re.compile(
    r"</?(?:think|analysis)\b[^>]*>",
    re.IGNORECASE,
)

_ROLE_BRIEFS: dict[TeamRole, str] = {
    TeamRole.RESEARCHER: "Identify relevant facts, unknowns, dependencies, and useful checks.",
    TeamRole.BUILDER: "Propose a concrete implementation approach, constraints, and validation.",
    TeamRole.CREATOR: (
        "Offer useful alternatives and improve the clarity or originality of the result."
    ),
    TeamRole.CRITIC: "Find weaknesses, hidden assumptions, failure modes, and safer corrections.",
    TeamRole.VERIFIER: (
        "Check internal consistency and state what evidence would verify completion."
    ),
    TeamRole.VISUAL_ANALYST: "Evaluate visual or multimodal considerations when relevant.",
    TeamRole.LEAD: "Synthesize the specialists into one accurate, actionable response.",
}


class SequentialCouncil:
    """Run one bounded, tool-free specialist at a time, then ask a lead to synthesize."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        *,
        role_assigner: CapabilityAwareRoleAssigner | None = None,
        limits: CouncilLimits | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.role_assigner = role_assigner or CapabilityAwareRoleAssigner()
        self.limits = limits or CouncilLimits()

    def run(
        self,
        request: CouncilRequest,
        models: Sequence[CatalogModel],
        *,
        preferred_lead: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CouncilRun:
        events = tuple(
            self.run_stream(
                request,
                models,
                preferred_lead=preferred_lead,
                cancellation=cancellation,
            )
        )
        for event in reversed(events):
            if event.result is not None:
                return CouncilRun(events=events, result=event.result)
        raise RuntimeError("Council stream ended without a terminal result")

    def run_stream(
        self,
        request: CouncilRequest,
        models: Sequence[CatalogModel],
        *,
        preferred_lead: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[TeamActivityEvent]:
        run_id = request.run_id or uuid4().hex
        prompt, context, input_truncated = request.bounded(self.limits)
        plan = self.role_assigner.assign(
            models,
            preferred_lead,
            required_purpose=request.automatic_purpose,
        )
        sequence = 0

        def event(
            kind: ActivityKind,
            message: str,
            *,
            member: TeamMember | None = None,
            worker: WorkerResult | None = None,
            result: CouncilResult | None = None,
        ) -> TeamActivityEvent:
            nonlocal sequence
            sequence += 1
            return TeamActivityEvent(
                run_id=run_id,
                sequence=sequence,
                kind=kind,
                message=message,
                member=member,
                worker=worker,
                result=result,
            )

        yield event(
            ActivityKind.COUNCIL_STARTED,
            "Sequential council started"
            + (" with bounded input" if input_truncated else ""),
        )

        worker_results: list[WorkerResult] = []
        for excluded in plan.excluded:
            skipped_member = TeamMember(
                member_id=excluded.model.physical_id,
                role=TeamRole.RESEARCHER,
                model_tag=excluded.model.primary_tag,
                aliases=excluded.model.tags,
                capabilities=excluded.model.capabilities,
                size_bytes=excluded.model.size_bytes,
            )
            skipped = WorkerResult(
                member=skipped_member,
                status=WorkerStatus.SKIPPED,
                failure=ProviderFailure(code="ineligible_model", message=excluded.reason),
            )
            worker_results.append(skipped)
            yield event(
                ActivityKind.MODEL_SKIPPED,
                excluded.reason,
                member=skipped_member,
                worker=skipped,
            )

        if plan.lead is None:
            failure = ProviderFailure(
                code="no_conversational_model",
                message="No installed physical model can provide conversational completions.",
            )
            result = CouncilResult(
                run_id=run_id,
                status=CouncilStatus.FAILED,
                final="",
                final_truncated=False,
                plan=plan,
                workers=tuple(worker_results),
                failure=failure,
            )
            yield event(
                ActivityKind.COUNCIL_COMPLETED,
                failure.message,
                result=result,
            )
            return

        available_worker_slots = max(self.limits.max_models - 1, 0)
        active_workers = plan.workers[:available_worker_slots]
        overflow_workers = plan.workers[available_worker_slots:]
        for member in overflow_workers:
            skipped = WorkerResult(
                member=member,
                status=WorkerStatus.SKIPPED,
                failure=ProviderFailure(
                    code="model_limit",
                    message=f"Council model limit is {self.limits.max_models}.",
                ),
            )
            worker_results.append(skipped)
            yield event(
                ActivityKind.MODEL_SKIPPED,
                skipped.failure.message,
                member=member,
                worker=skipped,
            )

        if active_workers and request.triage:
            if _cancelled(cancellation):
                result = self._cancelled_result(run_id, plan, worker_results)
                yield event(
                    ActivityKind.COUNCIL_CANCELLED,
                    "Council cancelled before triage",
                    result=result,
                )
                return
            yield event(
                ActivityKind.TRIAGE_STARTED,
                "Lead is selecting which specialists this message needs",
                member=plan.lead,
            )
            selected_roles = self._triage(plan.lead, prompt, context, active_workers, cancellation)
            if _cancelled(cancellation):
                result = self._cancelled_result(run_id, plan, worker_results)
                yield event(
                    ActivityKind.COUNCIL_CANCELLED,
                    "Council cancelled during triage",
                    result=result,
                )
                return
            if selected_roles is None:
                yield event(
                    ActivityKind.TRIAGE_COMPLETED,
                    "Triage was inconclusive — running the full specialist council",
                    member=plan.lead,
                )
            else:
                kept = [m for m in active_workers if m.role in selected_roles]
                dropped = [m for m in active_workers if m.role not in selected_roles]
                for member in dropped:
                    worker_results.append(
                        WorkerResult(
                            member=member,
                            status=WorkerStatus.SKIPPED,
                            failure=ProviderFailure(
                                code="triage_skipped",
                                message="Lead triage: not needed for this message.",
                            ),
                        )
                    )
                if kept:
                    summary = ", ".join(m.role.value for m in kept)
                    yield event(
                        ActivityKind.TRIAGE_COMPLETED,
                        f"Lead selected: {summary}"
                        + (f" · skipped {len(dropped)}" if dropped else ""),
                        member=plan.lead,
                    )
                else:
                    yield event(
                        ActivityKind.TRIAGE_COMPLETED,
                        "No specialists needed — MASTER will respond directly",
                        member=plan.lead,
                    )
                    result = CouncilResult(
                        run_id=run_id,
                        status=CouncilStatus.COMPLETE,
                        final="",
                        final_truncated=False,
                        plan=plan,
                        workers=tuple(worker_results),
                    )
                    yield event(
                        ActivityKind.COUNCIL_COMPLETED,
                        "Council complete — no specialists were needed",
                        result=result,
                    )
                    return
                active_workers = kept

        for member in active_workers:
            if _cancelled(cancellation):
                result = self._cancelled_result(run_id, plan, worker_results)
                yield event(
                    ActivityKind.COUNCIL_CANCELLED,
                    "Council cancelled before the next specialist started",
                    result=result,
                )
                return

            yield event(
                ActivityKind.WORKER_STARTED,
                f"{member.role.value} specialist started",
                member=member,
            )
            messages = self._worker_messages(member, prompt, context)
            worker = self._invoke_member(member, messages, cancellation)
            worker_results.append(worker)
            if worker.status is WorkerStatus.CANCELLED:
                result = self._cancelled_result(run_id, plan, worker_results)
                yield event(
                    ActivityKind.WORKER_CANCELLED,
                    "Specialist call was cancelled",
                    member=member,
                    worker=worker,
                )
                yield event(
                    ActivityKind.COUNCIL_CANCELLED,
                    "Council cancelled",
                    result=result,
                )
                return
            if worker.status is WorkerStatus.FAILED:
                yield event(
                    ActivityKind.WORKER_FAILED,
                    worker.failure.message if worker.failure else "Specialist failed",
                    member=member,
                    worker=worker,
                )
                continue
            yield event(
                ActivityKind.WORKER_COMPLETED,
                f"{member.role.value} specialist completed",
                member=member,
                worker=worker,
            )

        if _cancelled(cancellation):
            result = self._cancelled_result(run_id, plan, worker_results)
            yield event(
                ActivityKind.COUNCIL_CANCELLED,
                "Council cancelled before synthesis",
                result=result,
            )
            return

        yield event(
            ActivityKind.SYNTHESIS_STARTED,
            "Lead synthesis started",
            member=plan.lead,
        )
        synthesis_messages = self._synthesis_messages(
            plan.lead,
            prompt,
            context,
            worker_results,
        )
        synthesis = self._invoke_member(plan.lead, synthesis_messages, cancellation)
        if synthesis.status is WorkerStatus.CANCELLED:
            result = self._cancelled_result(run_id, plan, [*worker_results, synthesis])
            yield event(
                ActivityKind.COUNCIL_CANCELLED,
                "Council cancelled during synthesis",
                member=plan.lead,
                worker=synthesis,
                result=result,
            )
            return

        failed_workers = any(
            item.status is WorkerStatus.FAILED
            or (
                item.status is WorkerStatus.SKIPPED
                and (item.failure is None or item.failure.code != "triage_skipped")
            )
            for item in worker_results
        )
        if synthesis.status is WorkerStatus.FAILED:
            final, final_truncated = self._fallback_final(worker_results)
            status = CouncilStatus.PARTIAL if final else CouncilStatus.FAILED
            result = CouncilResult(
                run_id=run_id,
                status=status,
                final=final,
                final_truncated=final_truncated,
                plan=plan,
                workers=tuple([*worker_results, synthesis]),
                failure=synthesis.failure,
            )
            yield event(
                ActivityKind.SYNTHESIS_FAILED,
                synthesis.failure.message if synthesis.failure else "Lead synthesis failed",
                member=plan.lead,
                worker=synthesis,
            )
            yield event(
                ActivityKind.COUNCIL_COMPLETED,
                "Council completed without a verified synthesis",
                result=result,
            )
            return

        status = CouncilStatus.PARTIAL if failed_workers else CouncilStatus.COMPLETE
        result = CouncilResult(
            run_id=run_id,
            status=status,
            final=synthesis.output,
            final_truncated=synthesis.output_truncated,
            plan=plan,
            workers=tuple([*worker_results, synthesis]),
        )
        yield event(
            ActivityKind.SYNTHESIS_COMPLETED,
            "Lead synthesis completed",
            member=plan.lead,
            worker=synthesis,
        )
        yield event(
            ActivityKind.COUNCIL_COMPLETED,
            f"Council completed with status {status.value}",
            result=result,
        )

    def _invoke_member(
        self,
        member: TeamMember,
        messages: list[Message],
        cancellation: CancellationToken | None,
    ) -> WorkerResult:
        parts: list[str] = []
        attempted_tool_call = False
        output_limit = (
            self.limits.max_final_output_chars
            if member.role is TeamRole.LEAD
            else self.limits.max_worker_output_chars
        )
        # Keep enough headroom to remove common private-reasoning blocks before applying the
        # public result limit, while bounding memory even if a provider ignores output guidance.
        raw_capture_limit = max(output_limit * 4, output_limit + 4_096)
        captured_chars = 0
        raw_truncated = False
        try:
            provider = self.provider_factory(member.model_tag)
            # Specialists and synthesis are advisory. Passing no schemas guarantees this layer
            # can never execute or authorize a tool.
            for fragment in provider.chat_stream(
                messages,
                tools=None,
                cancellation=cancellation,
            ):
                if _cancelled(cancellation):
                    return WorkerResult(member=member, status=WorkerStatus.CANCELLED)
                if fragment.tool_calls:
                    attempted_tool_call = True
                if fragment.content:
                    remaining = raw_capture_limit - captured_chars
                    if remaining > 0:
                        captured = fragment.content[:remaining]
                        parts.append(captured)
                        captured_chars += len(captured)
                    if len(fragment.content) > remaining:
                        raw_truncated = True
        except Exception as exc:
            if _cancelled(cancellation):
                return WorkerResult(member=member, status=WorkerStatus.CANCELLED)
            message, _truncated = _bounded_text(
                f"{type(exc).__name__}: {exc}",
                self.limits.max_error_chars,
            )
            return WorkerResult(
                member=member,
                status=WorkerStatus.FAILED,
                failure=ProviderFailure(code="provider_error", message=message),
            )

        if _cancelled(cancellation):
            return WorkerResult(member=member, status=WorkerStatus.CANCELLED)
        if attempted_tool_call:
            return WorkerResult(
                member=member,
                status=WorkerStatus.FAILED,
                failure=ProviderFailure(
                    code="unexpected_tool_call",
                    message="A tool-free council member attempted to emit a tool call.",
                ),
            )
        public_output = _strip_private_reasoning("".join(parts)).strip()
        output, bounded = _bounded_text(public_output, output_limit)
        truncated = raw_truncated or bounded
        if not output:
            return WorkerResult(
                member=member,
                status=WorkerStatus.FAILED,
                failure=ProviderFailure(
                    code="empty_response",
                    message="The model returned no usable work product.",
                ),
            )
        return WorkerResult(
            member=member,
            status=WorkerStatus.SUCCEEDED,
            output=output,
            output_truncated=truncated,
        )

    def _triage(
        self,
        lead: TeamMember,
        prompt: str,
        context: str,
        candidates: Sequence[TeamMember],
        cancellation: CancellationToken | None,
    ) -> set[TeamRole] | None:
        """Ask the lead which specialist roles this message needs.

        Returns the selected roles (possibly empty for a direct response), or
        None when the triage call failed or was unparseable — the caller then
        falls back to running the full council, so triage can never lose work.
        """
        roles = sorted({member.role for member in candidates}, key=lambda role: role.value)
        role_lines = "\n".join(f"- {role.value}: {_ROLE_BRIEFS[role]}" for role in roles)
        system = (
            "You route work inside a local multi-model council. Decide which "
            "specialist roles would materially improve the answer to the user's "
            "current message. Simple follow-ups, opinions about prior output, "
            "clarifications, and conversational replies need no specialists. "
            "Reserve specialists for substantial new work. Reply with only a "
            "comma-separated subset of these role names, or the single word NONE:\n"
            f"{role_lines}"
        )
        user = f"User message:\n{prompt}"
        if context:
            user += f"\n\nBounded context:\n{context}"
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
        result = self._invoke_member(lead, messages, cancellation)
        if result.status is not WorkerStatus.SUCCEEDED:
            return None
        text = result.output.lower()
        selected = {
            role
            for role in roles
            if re.search(rf"\b{re.escape(role.value)}\b", text)
        }
        if selected:
            return selected
        if re.search(r"\bnone\b", text):
            return set()
        return None

    @staticmethod
    def _worker_messages(
        member: TeamMember,
        prompt: str,
        context: str,
    ) -> list[Message]:
        system = (
            f"You are the {member.role.value} specialist in a local multi-model council. "
            f"{_ROLE_BRIEFS[member.role]} "
            "Return only a concise work product: conclusions, supporting observations, "
            "uncertainties, and recommended next steps. Do not reveal hidden chain-of-thought "
            "or private reasoning. Do not call, request, or claim to have used tools; tools are "
            "not available in this advisory phase."
        )
        user = f"Task:\n{prompt}"
        if context:
            user += f"\n\nBounded context:\n{context}"
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]

    def _synthesis_messages(
        self,
        lead: TeamMember,
        prompt: str,
        context: str,
        workers: Sequence[WorkerResult],
    ) -> list[Message]:
        summaries: list[str] = []
        for worker in workers:
            if worker.status is WorkerStatus.SUCCEEDED:
                summaries.append(
                    f"[{worker.member.role.value} | {worker.member.model_tag}]\n{worker.output}"
                )
            elif worker.failure is not None:
                summaries.append(
                    f"[{worker.member.role.value} | {worker.member.model_tag} | "
                    f"{worker.status.value}]\n{worker.failure.message}"
                )
        raw_summaries = "\n\n".join(summaries) or "(No additional specialist output.)"
        bounded_summaries, _truncated = _bounded_text(
            raw_summaries,
            self.limits.max_synthesis_context_chars,
        )
        system = (
            f"You are the {lead.role.value} for a local multi-model council. "
            f"{_ROLE_BRIEFS[lead.role]} Return only the final response suitable for the user. "
            "Use the work products as advisory evidence, correct conflicts, and distinguish "
            "observations from inferences and unresolved uncertainty. Do not reveal hidden "
            "chain-of-thought or private reasoning. Tools are unavailable in this council phase, "
            "so do not claim that any action or tool call was completed."
        )
        user = f"Original task:\n{prompt}"
        if context:
            user += f"\n\nBounded context:\n{context}"
        user += f"\n\nSpecialist work products:\n{bounded_summaries}"
        return [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]

    def _fallback_final(
        self,
        workers: Sequence[WorkerResult],
    ) -> tuple[str, bool]:
        available = [
            f"[{item.member.role.value} | {item.member.model_tag}]\n{item.output}"
            for item in workers
            if item.status is WorkerStatus.SUCCEEDED and item.output
        ]
        if not available:
            return "", False
        raw = (
            "Lead synthesis failed, so completion is not verified. "
            "The following bounded specialist work products remain available:\n\n"
            + "\n\n".join(available)
        )
        return _bounded_text(raw, self.limits.max_final_output_chars)

    @staticmethod
    def _cancelled_result(
        run_id: str,
        plan: TeamPlan,
        workers: Sequence[WorkerResult],
    ) -> CouncilResult:
        return CouncilResult(
            run_id=run_id,
            status=CouncilStatus.CANCELLED,
            final="",
            final_truncated=False,
            plan=plan,
            workers=tuple(workers),
            failure=ProviderFailure(code="cancelled", message="Council run was cancelled."),
        )


def _cancelled(cancellation: CancellationToken | None) -> bool:
    return cancellation is not None and cancellation.cancelled


def _strip_private_reasoning(content: str) -> str:
    """Remove common provider-emitted private-reasoning envelopes before publication."""
    without_blocks = _PRIVATE_REASONING_BLOCK.sub("", content)
    without_unclosed = _UNCLOSED_PRIVATE_REASONING.sub("", without_blocks)
    return _PRIVATE_REASONING_TAG.sub("", without_unclosed)
