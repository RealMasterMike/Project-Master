from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from project_master.dreams.provenance import ProvenanceValidator
from project_master.dreams.snapshots import (
    DreamSource,
    SnapshotBuilder,
    SnapshotPolicy,
    SourceKind,
    SourceSensitivity,
    SourceSnapshot,
)

CAPTURED = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _source(
    source_id: str,
    content: str,
    *,
    sensitivity: SourceSensitivity = SourceSensitivity.INTERNAL,
    allow_dreaming: bool = True,
    locator: str | None = None,
) -> DreamSource:
    return DreamSource(
        source_id=source_id,
        kind=SourceKind.PROJECT,
        locator=locator or f"project://{source_id}",
        content=content,
        captured_at_utc=CAPTURED,
        sensitivity=sensitivity,
        allow_dreaming=allow_dreaming,
    )


def test_snapshot_is_order_independent_and_redacts_content_and_locator() -> None:
    sources = [
        _source(
            "allowed",
            "Contact mike@example.com with api_key=super-secret",
            locator="project://mike@example.com",
        ),
        _source("secret", "never-copy-me", sensitivity=SourceSensitivity.SECRET),
        _source("optout", "not-for-dreams", allow_dreaming=False),
        _source("private", "private-material", sensitivity=SourceSensitivity.PRIVATE),
    ]
    builder = SnapshotBuilder()
    policy = SnapshotPolicy()

    first = builder.build(sources, policy=policy, captured_at_utc=CAPTURED)
    second = builder.build(
        list(reversed(sources)),
        policy=policy,
        captured_at_utc=CAPTURED,
    )

    assert first == second
    assert first.snapshot_id == second.snapshot_id
    assert [entry.source_id for entry in first.entries] == ["allowed"]
    entry = first.entries[0]
    assert entry.content == (
        "Contact [REDACTED:email] with [REDACTED:credential]"
    )
    assert entry.locator == "project://[REDACTED:email]"
    assert entry.redactions == ("credential", "email")
    serialized = json.dumps(first.to_dict())
    assert "super-secret" not in serialized
    assert "never-copy-me" not in serialized
    assert "private-material" not in serialized
    reasons = {item.source_id: item.reason for item in first.exclusions}
    assert reasons["secret"] == "secret sensitivity is not allowed"
    assert reasons["optout"] == "source opted out of dreaming"
    assert reasons["private"] == "private sensitivity is not allowed"


def test_snapshot_enforces_per_source_total_and_count_bounds() -> None:
    snapshot = SnapshotBuilder().build(
        [
            _source("a", "A" * 100),
            _source("b", "B" * 100),
            _source("c", "C" * 100),
        ],
        policy=SnapshotPolicy(
            max_sources=2,
            max_source_chars=30,
            max_total_chars=45,
        ),
        captured_at_utc=CAPTURED,
    )

    assert len(snapshot.entries) == 2
    assert sum(len(item.content) for item in snapshot.entries) == 45
    assert all(item.truncated for item in snapshot.entries)
    assert snapshot.entries[0].content.endswith("[truncated]")
    assert any(item.reason == "snapshot source-count limit reached" for item in snapshot.exclusions)


def test_duplicate_source_ids_are_excluded_deterministically() -> None:
    snapshot = SnapshotBuilder().build(
        [
            _source("same", "second", locator="project://z"),
            _source("same", "first", locator="project://a"),
        ],
        policy=SnapshotPolicy(),
        captured_at_utc=CAPTURED,
    )

    assert snapshot.entries[0].content == "first"
    assert snapshot.exclusions[0].reason == "duplicate source identifier"


def test_provenance_validator_detects_content_and_snapshot_tampering() -> None:
    snapshot = SnapshotBuilder().build(
        [_source("source", "redacted-safe-content")],
        policy=SnapshotPolicy(),
        captured_at_utc=CAPTURED,
    )
    tampered_entry = replace(snapshot.entries[0], content="changed")
    tampered = SourceSnapshot(
        snapshot_id=snapshot.snapshot_id,
        captured_at_utc=snapshot.captured_at_utc,
        entries=(tampered_entry,),
        exclusions=snapshot.exclusions,
    )

    report = ProvenanceValidator().validate_snapshot(tampered)

    assert report.ok is False
    assert {finding.code for finding in report.findings} == {
        "snapshot_fingerprint_mismatch",
        "source_hash_mismatch",
    }


def test_empty_snapshot_is_valid_but_explicitly_warned() -> None:
    snapshot = SnapshotBuilder().build(
        [_source("secret", "hidden", sensitivity=SourceSensitivity.CREDENTIAL)],
        policy=SnapshotPolicy(),
        captured_at_utc=CAPTURED,
    )

    report = ProvenanceValidator().validate_snapshot(snapshot)

    assert report.ok is True
    assert [finding.code for finding in report.findings] == ["empty_snapshot"]
