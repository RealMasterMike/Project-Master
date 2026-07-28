from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from project_master.dreams.inbox import DreamInbox, DreamInboxError, DreamItemFactory
from project_master.dreams.models import (
    DreamDisposition,
    DreamItem,
    DreamRunStatus,
    EpistemicLabel,
    PromotionTarget,
)
from project_master.dreams.snapshots import (
    DreamSource,
    SnapshotBuilder,
    SnapshotPolicy,
    SourceKind,
    SourceSnapshot,
)
from project_master.team.models import (
    CouncilResult,
    CouncilStatus,
    ProviderFailure,
    TeamMember,
    TeamPlan,
    TeamRole,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _snapshot() -> SourceSnapshot:
    return SnapshotBuilder().build(
        [
            DreamSource(
                source_id="note",
                kind=SourceKind.PROJECT,
                locator="project://note",
                content="An observed project constraint.",
                captured_at_utc=NOW,
            )
        ],
        policy=SnapshotPolicy(),
        captured_at_utc=NOW,
    )


def _result(
    status: CouncilStatus,
    *,
    final: str,
    run_id: str = "council-1",
    failure: ProviderFailure | None = None,
) -> CouncilResult:
    lead = TeamMember(
        member_id="digest:lead",
        role=TeamRole.LEAD,
        model_tag="lead",
        aliases=("lead",),
        capabilities=frozenset({"completion", "tools"}),
        size_bytes=1,
    )
    return CouncilResult(
        run_id=run_id,
        status=status,
        final=final,
        final_truncated=False,
        plan=TeamPlan(lead=lead, workers=()),
        workers=(),
        failure=failure,
    )


def _item(
    status: CouncilStatus = CouncilStatus.COMPLETE,
    *,
    final: str = "A speculative proposal.",
    run_id: str = "council-1",
) -> tuple[SourceSnapshot, DreamItem]:
    snapshot = _snapshot()
    result = _result(status, final=final, run_id=run_id)
    item = DreamItemFactory().from_council(
        recipe_id="idea-garden",
        window_key="dream:manual:idea-garden:click-1",
        snapshot=snapshot,
        result=result,
        created_at_utc=NOW,
    )
    return snapshot, item


def test_factory_creates_deterministic_speculative_item_with_provenance() -> None:
    snapshot, item = _item()
    _snapshot_again, item_again = _item()

    assert item == item_again
    assert item.run_status is DreamRunStatus.COMPLETE
    assert item.epistemic_label is EpistemicLabel.SPECULATION
    assert item.source_refs == ("note",)
    assert item.snapshot_id == snapshot.snapshot_id
    assert item.disposition is DreamDisposition.PENDING


def test_inbox_add_is_idempotent_and_window_is_unique() -> None:
    snapshot, item = _item()
    inbox = DreamInbox()

    assert inbox.add(item, snapshot) == item
    assert inbox.add(item, snapshot) == item
    conflicting = replace(item, item_id="dream_conflict", council_run_id="other")
    with pytest.raises(DreamInboxError, match="window already produced"):
        inbox.add(conflicting, snapshot)


def test_explicit_promotion_returns_candidate_handoff_without_changing_label() -> None:
    snapshot, item = _item()
    inbox = DreamInbox()
    inbox.add(item, snapshot)

    promoted, handoff = inbox.promote(
        item.item_id,
        target=PromotionTarget.MEDIA_BRIEF_CANDIDATE,
        decided_by="mike",
        decided_at_utc=NOW,
        rationale="Worth developing as a draft.",
    )

    assert promoted.disposition is DreamDisposition.PROMOTED
    assert promoted.epistemic_label is EpistemicLabel.SPECULATION
    assert handoff.target is PromotionTarget.MEDIA_BRIEF_CANDIDATE
    assert handoff.epistemic_label is EpistemicLabel.SPECULATION
    assert "candidate" in handoff.target.value
    with pytest.raises(DreamInboxError, match="already received"):
        inbox.reject(
            item.item_id,
            decided_by="mike",
            decided_at_utc=NOW,
            rationale="Changed my mind.",
        )


def test_explicit_rejection_is_terminal() -> None:
    snapshot, item = _item()
    inbox = DreamInbox()
    inbox.add(item, snapshot)

    rejected = inbox.reject(
        item.item_id,
        decided_by="mike",
        decided_at_utc=NOW,
        rationale="Not useful.",
    )

    assert rejected.disposition is DreamDisposition.REJECTED
    assert inbox.list_pending() == ()


def test_partial_council_result_enters_inbox_with_visible_reason() -> None:
    snapshot = _snapshot()
    result = _result(
        CouncilStatus.PARTIAL,
        final="A bounded partial proposal.",
        failure=ProviderFailure("provider_error", "One model failed."),
    )
    item = DreamItemFactory().from_council(
        recipe_id="risk-scan",
        window_key="dream:schedule:risk:2026-07-27:020000",
        snapshot=snapshot,
        result=result,
        created_at_utc=NOW,
    )

    assert item.run_status is DreamRunStatus.PARTIAL
    assert item.partial_reason == "One model failed."
    assert DreamInbox().add(item, snapshot) == item


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CouncilStatus.CANCELLED, DreamRunStatus.CANCELLED),
        (CouncilStatus.FAILED, DreamRunStatus.FAILED),
    ],
)
def test_cancelled_and_failed_runs_are_records_but_not_inbox_proposals(
    status: CouncilStatus,
    expected: DreamRunStatus,
) -> None:
    snapshot = _snapshot()
    item = DreamItemFactory().from_council(
        recipe_id="idea-garden",
        window_key=f"dream:manual:idea-garden:{status.value}",
        snapshot=snapshot,
        result=_result(
            status,
            final="",
            failure=ProviderFailure(status.value, f"Run {status.value}."),
        ),
        created_at_utc=NOW,
    )

    assert item.run_status is expected
    with pytest.raises(DreamInboxError, match="only complete or partial"):
        DreamInbox().add(item, snapshot)


@pytest.mark.parametrize(
    "mutation",
    [
        {"epistemic_label": EpistemicLabel.VERIFIED},
        {"source_refs": ("missing-source",)},
    ],
)
def test_inbox_rejects_unlabeled_or_untraceable_proposals(
    mutation: dict[str, object],
) -> None:
    snapshot, item = _item()
    invalid = replace(item, **mutation)

    with pytest.raises(DreamInboxError, match="provenance validation failed"):
        DreamInbox().add(invalid, snapshot)
