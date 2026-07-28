from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, replace
from datetime import datetime

from project_master.dreams.models import (
    DecisionKind,
    DreamDecision,
    DreamDisposition,
    DreamItem,
    DreamRunStatus,
    EpistemicLabel,
    PromotionTarget,
)
from project_master.dreams.provenance import ProvenanceValidator
from project_master.dreams.snapshots import SourceSnapshot
from project_master.team.models import CouncilResult, CouncilStatus


class DreamInboxError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionHandoff:
    item_id: str
    target: PromotionTarget
    proposal_text: str
    epistemic_label: EpistemicLabel
    source_refs: tuple[str, ...]
    snapshot_id: str
    approved_by: str
    approved_at_utc: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "target": self.target.value,
            "proposal_text": self.proposal_text,
            "epistemic_label": self.epistemic_label.value,
            "source_refs": list(self.source_refs),
            "snapshot_id": self.snapshot_id,
            "approved_by": self.approved_by,
            "approved_at_utc": self.approved_at_utc.isoformat(),
        }


class DreamItemFactory:
    def __init__(self, validator: ProvenanceValidator | None = None) -> None:
        self.validator = validator or ProvenanceValidator()

    def from_council(
        self,
        *,
        recipe_id: str,
        window_key: str,
        snapshot: SourceSnapshot,
        result: CouncilResult,
        created_at_utc: datetime,
    ) -> DreamItem:
        report = self.validator.validate_snapshot(snapshot)
        if not report.ok:
            codes = ", ".join(item.code for item in report.findings)
            raise DreamInboxError(f"invalid dream provenance: {codes}")
        status = {
            CouncilStatus.COMPLETE: DreamRunStatus.COMPLETE,
            CouncilStatus.PARTIAL: DreamRunStatus.PARTIAL,
            CouncilStatus.CANCELLED: DreamRunStatus.CANCELLED,
            CouncilStatus.FAILED: DreamRunStatus.FAILED,
        }[result.status]
        partial_reason: str | None = None
        if status is DreamRunStatus.PARTIAL:
            partial_reason = (
                result.failure.message
                if result.failure is not None
                else "One or more council members failed or were skipped."
            )
        item_id = _item_id(recipe_id, window_key, snapshot.snapshot_id, result.run_id)
        item = DreamItem(
            item_id=item_id,
            recipe_id=recipe_id,
            window_key=window_key,
            council_run_id=result.run_id,
            snapshot_id=snapshot.snapshot_id,
            proposal_text=result.final.strip(),
            run_status=status,
            epistemic_label=EpistemicLabel.SPECULATION,
            source_refs=tuple(entry.source_id for entry in snapshot.entries),
            created_at_utc=created_at_utc,
            partial_reason=partial_reason,
        )
        item_report = self.validator.validate_item(item, snapshot)
        if not item_report.ok:
            codes = ", ".join(finding.code for finding in item_report.findings)
            raise DreamInboxError(f"invalid dream item provenance: {codes}")
        return item


class DreamInbox:
    """Proposal lifecycle only; promotion returns data and performs no external mutation."""

    def __init__(self, validator: ProvenanceValidator | None = None) -> None:
        self.validator = validator or ProvenanceValidator()
        self._items: dict[str, DreamItem] = {}
        self._window_items: dict[str, str] = {}
        self._lock = threading.RLock()

    def add(self, item: DreamItem, snapshot: SourceSnapshot) -> DreamItem:
        if item.run_status not in {DreamRunStatus.COMPLETE, DreamRunStatus.PARTIAL}:
            raise DreamInboxError("only complete or partial proposals can enter the Dream Inbox")
        if item.disposition is not DreamDisposition.PENDING:
            raise DreamInboxError("new Dream Inbox items must be pending review")
        report = self.validator.validate_item(item, snapshot)
        if not report.ok:
            codes = ", ".join(finding.code for finding in report.findings)
            raise DreamInboxError(f"dream provenance validation failed: {codes}")
        with self._lock:
            existing = self._items.get(item.item_id)
            if existing is not None:
                if existing == item:
                    return existing
                raise DreamInboxError("dream item ID already exists with different contents")
            window_item = self._window_items.get(item.window_key)
            if window_item is not None:
                raise DreamInboxError(
                    f"dream window already produced Inbox item {window_item}"
                )
            self._items[item.item_id] = item
            self._window_items[item.window_key] = item.item_id
            return item

    def get(self, item_id: str) -> DreamItem:
        with self._lock:
            try:
                return self._items[item_id]
            except KeyError as exc:
                raise DreamInboxError(f"unknown dream item: {item_id}") from exc

    def list_all(self) -> tuple[DreamItem, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._items.values(),
                    key=lambda item: (item.created_at_utc, item.item_id),
                )
            )

    def list_pending(self) -> tuple[DreamItem, ...]:
        return tuple(
            item
            for item in self.list_all()
            if item.disposition is DreamDisposition.PENDING
        )

    def promote(
        self,
        item_id: str,
        *,
        target: PromotionTarget,
        decided_by: str,
        decided_at_utc: datetime,
        rationale: str,
    ) -> tuple[DreamItem, PromotionHandoff]:
        decision = DreamDecision(
            kind=DecisionKind.PROMOTE,
            target=target,
            decided_by=decided_by,
            decided_at_utc=decided_at_utc,
            rationale=rationale,
        )
        with self._lock:
            current = self.get(item_id)
            self._require_pending(current)
            updated = replace(
                current,
                disposition=DreamDisposition.PROMOTED,
                decision=decision,
            )
            self._items[item_id] = updated
        handoff = PromotionHandoff(
            item_id=updated.item_id,
            target=target,
            proposal_text=updated.proposal_text,
            epistemic_label=updated.epistemic_label,
            source_refs=updated.source_refs,
            snapshot_id=updated.snapshot_id,
            approved_by=decision.decided_by,
            approved_at_utc=decision.decided_at_utc,
        )
        return updated, handoff

    def reject(
        self,
        item_id: str,
        *,
        decided_by: str,
        decided_at_utc: datetime,
        rationale: str,
    ) -> DreamItem:
        decision = DreamDecision(
            kind=DecisionKind.REJECT,
            decided_by=decided_by,
            decided_at_utc=decided_at_utc,
            rationale=rationale,
        )
        with self._lock:
            current = self.get(item_id)
            self._require_pending(current)
            updated = replace(
                current,
                disposition=DreamDisposition.REJECTED,
                decision=decision,
            )
            self._items[item_id] = updated
            return updated

    @staticmethod
    def _require_pending(item: DreamItem) -> None:
        if item.disposition is not DreamDisposition.PENDING:
            raise DreamInboxError("dream item has already received a final review decision")


def _item_id(
    recipe_id: str,
    window_key: str,
    snapshot_id: str,
    council_run_id: str,
) -> str:
    raw = "\x1f".join((recipe_id, window_key, snapshot_id, council_run_id))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"dream_{digest}"
