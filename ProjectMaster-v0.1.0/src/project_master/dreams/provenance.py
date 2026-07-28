from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum
from typing import Any

from project_master.dreams.models import DreamItem, EpistemicLabel
from project_master.dreams.snapshots import SourceSnapshot, snapshot_fingerprint


class ProvenanceSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProvenanceFinding:
    severity: ProvenanceSeverity
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    findings: tuple[ProvenanceFinding, ...]

    @property
    def ok(self) -> bool:
        return not any(
            finding.severity is ProvenanceSeverity.ERROR for finding in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class ProvenanceValidator:
    def validate_snapshot(self, snapshot: SourceSnapshot) -> ProvenanceReport:
        findings: list[ProvenanceFinding] = []
        if (
            snapshot.captured_at_utc.tzinfo is None
            or snapshot.captured_at_utc.utcoffset() is None
            or snapshot.captured_at_utc.utcoffset() != UTC.utcoffset(None)
        ):
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "snapshot_timestamp_not_utc",
                    "Snapshot capture timestamp must be timezone-aware UTC.",
                )
            )
        expected_snapshot_id = snapshot_fingerprint(snapshot)
        if snapshot.snapshot_id != expected_snapshot_id:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "snapshot_fingerprint_mismatch",
                    "Snapshot fingerprint does not match its redacted contents.",
                )
            )
        seen_ids: set[str] = set()
        for entry in snapshot.entries:
            if (
                entry.source_captured_at_utc.tzinfo is None
                or entry.source_captured_at_utc.utcoffset() is None
                or entry.source_captured_at_utc.utcoffset() != UTC.utcoffset(None)
            ):
                findings.append(
                    ProvenanceFinding(
                        ProvenanceSeverity.ERROR,
                        "source_timestamp_not_utc",
                        f"Snapshot source {entry.source_id} timestamp is not UTC.",
                    )
                )
            if entry.source_id in seen_ids:
                findings.append(
                    ProvenanceFinding(
                        ProvenanceSeverity.ERROR,
                        "duplicate_source_ref",
                        f"Snapshot contains duplicate source ID {entry.source_id}.",
                    )
                )
            seen_ids.add(entry.source_id)
            expected_hash = hashlib.sha256(entry.content.encode("utf-8")).hexdigest()
            if entry.content_sha256 != expected_hash:
                findings.append(
                    ProvenanceFinding(
                        ProvenanceSeverity.ERROR,
                        "source_hash_mismatch",
                        f"Snapshot source {entry.source_id} failed its content hash check.",
                    )
                )
        if not snapshot.entries:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.WARNING,
                    "empty_snapshot",
                    "Dream snapshot contains no eligible source material.",
                )
            )
        return ProvenanceReport(tuple(findings))

    def validate_item(
        self,
        item: DreamItem,
        snapshot: SourceSnapshot,
    ) -> ProvenanceReport:
        findings = list(self.validate_snapshot(snapshot).findings)
        if item.snapshot_id != snapshot.snapshot_id:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "item_snapshot_mismatch",
                    "Dream item does not reference the supplied source snapshot.",
                )
            )
        if item.epistemic_label is not EpistemicLabel.SPECULATION:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "dream_not_speculative",
                    "Dream proposals must remain explicitly labeled as speculation.",
                )
            )
        available = {entry.source_id for entry in snapshot.entries}
        if len(item.source_refs) != len(set(item.source_refs)):
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "duplicate_item_source_ref",
                    "Dream item contains duplicate source references.",
                )
            )
        missing = sorted(set(item.source_refs) - available)
        if missing:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "unknown_source_ref",
                    f"Dream item references unknown source IDs: {', '.join(missing)}.",
                )
            )
        if available and not item.source_refs:
            findings.append(
                ProvenanceFinding(
                    ProvenanceSeverity.ERROR,
                    "missing_source_refs",
                    "Dream item omitted provenance links for a non-empty snapshot.",
                )
            )
        return ProvenanceReport(tuple(findings))
