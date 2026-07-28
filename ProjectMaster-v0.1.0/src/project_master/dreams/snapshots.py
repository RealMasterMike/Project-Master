from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from project_master.dreams.models import _require_identifier, _utc


class SourceKind(StrEnum):
    CONVERSATION = "conversation"
    PROJECT = "project"
    MEMORY = "memory"
    DECISION = "decision"
    ARTIFACT = "artifact"
    USER_NOTE = "user_note"


class SourceSensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    SECRET = "secret"
    CREDENTIAL = "credential"


@dataclass(frozen=True, slots=True)
class DreamSource:
    source_id: str
    kind: SourceKind
    locator: str
    content: str
    captured_at_utc: datetime
    sensitivity: SourceSensitivity = SourceSensitivity.INTERNAL
    allow_dreaming: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_id",
            _require_identifier(self.source_id, "source_id"),
        )
        if not self.locator.strip():
            raise ValueError("source locator must not be empty")
        object.__setattr__(
            self,
            "captured_at_utc",
            _utc(self.captured_at_utc, "captured_at_utc"),
        )


@dataclass(frozen=True, slots=True)
class RedactionRule:
    name: str
    pattern: str
    replacement: str
    priority: int = 100
    flags: int = re.IGNORECASE

    def __post_init__(self) -> None:
        _require_identifier(self.name, "redaction rule name")
        if not self.replacement:
            raise ValueError("redaction replacement must not be empty")
        re.compile(self.pattern, self.flags)


def default_redaction_rules() -> tuple[RedactionRule, ...]:
    return (
        RedactionRule(
            name="bearer_token",
            pattern=r"\bbearer\s+[A-Za-z0-9._~+/=-]+",
            replacement="[REDACTED:bearer_token]",
            priority=10,
        ),
        RedactionRule(
            name="credential",
            pattern=(
                r"\b(?:api[_-]?key|access[_-]?token|password|secret)\b"
                r"\s*[:=]\s*[\"']?[^\"'\s,;]+[\"']?"
            ),
            replacement="[REDACTED:credential]",
            priority=20,
        ),
        RedactionRule(
            name="email",
            pattern=r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            replacement="[REDACTED:email]",
            priority=30,
        ),
    )


@dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    allowed_sensitivities: frozenset[SourceSensitivity] = field(
        default_factory=lambda: frozenset(
            {SourceSensitivity.PUBLIC, SourceSensitivity.INTERNAL}
        )
    )
    excluded_source_ids: frozenset[str] = field(default_factory=frozenset)
    excluded_kinds: frozenset[SourceKind] = field(default_factory=frozenset)
    redaction_rules: tuple[RedactionRule, ...] = field(
        default_factory=default_redaction_rules
    )
    max_sources: int = 64
    max_source_chars: int = 8_000
    max_total_chars: int = 32_000

    def __post_init__(self) -> None:
        if self.max_sources < 1:
            raise ValueError("max_sources must be positive")
        if self.max_source_chars < 1:
            raise ValueError("max_source_chars must be positive")
        if self.max_total_chars < 1:
            raise ValueError("max_total_chars must be positive")
        names = [rule.name for rule in self.redaction_rules]
        if len(names) != len(set(names)):
            raise ValueError("redaction rule names must be unique")


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    source_id: str
    kind: SourceKind
    locator: str
    source_captured_at_utc: datetime
    content: str
    content_sha256: str
    redactions: tuple[str, ...] = ()
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "locator": self.locator,
            "source_captured_at_utc": self.source_captured_at_utc.isoformat(),
            "content": self.content,
            "content_sha256": self.content_sha256,
            "redactions": list(self.redactions),
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class SnapshotExclusion:
    source_id: str
    kind: SourceKind
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    snapshot_id: str
    captured_at_utc: datetime
    entries: tuple[SnapshotEntry, ...]
    exclusions: tuple[SnapshotExclusion, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "captured_at_utc": self.captured_at_utc.isoformat(),
            "entries": [item.to_dict() for item in self.entries],
            "exclusions": [item.to_dict() for item in self.exclusions],
        }


class SnapshotBuilder:
    def build(
        self,
        sources: list[DreamSource] | tuple[DreamSource, ...],
        *,
        policy: SnapshotPolicy,
        captured_at_utc: datetime,
    ) -> SourceSnapshot:
        captured_at = _utc(captured_at_utc, "captured_at_utc")
        entries: list[SnapshotEntry] = []
        exclusions: list[SnapshotExclusion] = []
        seen_ids: set[str] = set()
        total_chars = 0
        ordered = sorted(sources, key=lambda item: (item.source_id.casefold(), item.locator))

        for source in ordered:
            reason = self._exclusion_reason(source, policy, seen_ids, len(entries))
            seen_ids.add(source.source_id)
            if reason is not None:
                exclusions.append(
                    SnapshotExclusion(
                        source_id=source.source_id,
                        kind=source.kind,
                        reason=reason,
                    )
                )
                continue

            content, content_redactions = _redact(
                source.content,
                policy.redaction_rules,
            )
            locator, locator_redactions = _redact(
                source.locator,
                policy.redaction_rules,
            )
            redactions = tuple(
                sorted(set((*content_redactions, *locator_redactions)))
            )
            content, source_truncated = _bound(content, policy.max_source_chars)
            remaining = policy.max_total_chars - total_chars
            if remaining <= 0:
                exclusions.append(
                    SnapshotExclusion(
                        source_id=source.source_id,
                        kind=source.kind,
                        reason="snapshot character budget exhausted",
                    )
                )
                continue
            content, total_truncated = _bound(content, remaining)
            total_chars += len(content)
            entries.append(
                SnapshotEntry(
                    source_id=source.source_id,
                    kind=source.kind,
                    locator=locator,
                    source_captured_at_utc=source.captured_at_utc,
                    content=content,
                    content_sha256=_sha256(content),
                    redactions=redactions,
                    truncated=source_truncated or total_truncated,
                )
            )

        snapshot = SourceSnapshot(
            snapshot_id="pending",
            captured_at_utc=captured_at,
            entries=tuple(entries),
            exclusions=tuple(exclusions),
        )
        return SourceSnapshot(
            snapshot_id=snapshot_fingerprint(snapshot),
            captured_at_utc=snapshot.captured_at_utc,
            entries=snapshot.entries,
            exclusions=snapshot.exclusions,
        )

    @staticmethod
    def _exclusion_reason(
        source: DreamSource,
        policy: SnapshotPolicy,
        seen_ids: set[str],
        included_count: int,
    ) -> str | None:
        if source.source_id in seen_ids:
            return "duplicate source identifier"
        if not source.allow_dreaming:
            return "source opted out of dreaming"
        if source.source_id in policy.excluded_source_ids:
            return "source explicitly excluded by policy"
        if source.kind in policy.excluded_kinds:
            return "source kind excluded by policy"
        if source.sensitivity not in policy.allowed_sensitivities:
            return f"{source.sensitivity.value} sensitivity is not allowed"
        if included_count >= policy.max_sources:
            return "snapshot source-count limit reached"
        return None


def snapshot_fingerprint(snapshot: SourceSnapshot) -> str:
    payload = {
        "captured_at_utc": snapshot.captured_at_utc.isoformat(),
        "entries": [item.to_dict() for item in snapshot.entries],
        "exclusions": [item.to_dict() for item in snapshot.exclusions],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"snap_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def _redact(
    content: str,
    rules: tuple[RedactionRule, ...],
) -> tuple[str, tuple[str, ...]]:
    redacted = content.replace("\r\n", "\n").replace("\r", "\n")
    applied: list[str] = []
    for rule in sorted(rules, key=lambda item: (item.priority, item.name)):
        pattern = re.compile(rule.pattern, rule.flags)
        updated, count = pattern.subn(rule.replacement, redacted)
        if count:
            applied.append(rule.name)
        redacted = updated
    return redacted, tuple(applied)


def _bound(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    marker = "\n[truncated]"
    if limit <= len(marker):
        return marker[:limit], True
    return f"{content[: limit - len(marker)]}{marker}", True


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
