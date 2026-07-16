from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from project_master.memory.store import SQLiteStore

_PROFANITY = re.compile(r"\b(fuck|fucking|shit|damn|ass|bullshit)\b", re.IGNORECASE)
_HUMOR = re.compile(r"(?:\blol\b|\blmao\b|😂|🤣|haha|joking)", re.IGNORECASE)
_TECHNICAL = re.compile(r"\b(model|tool|agent|architecture|code|evidence)\b", re.IGNORECASE)
_SITUATIONAL = re.compile(r"\b(?:this\s+time|for\s+this|right\s+now|in\s+this\s+response)\b", re.I)

_CORRECTION_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "preserve_semantic_fidelity",
        re.compile(
            r"\b(?:i\s+(?:didn't|did\s+not)\s+say|that's\s+not\s+what\s+i\s+said|"
            r"changed\s+my\s+meaning|put\s+words\s+in\s+my\s+mouth|don't\s+reframe)\b",
            re.I,
        ),
        "Do not replace the user's meaning with a stronger, weaker, cleaner, or commoner claim.",
    ),
    (
        "avoid_unjustified_assumptions",
        re.compile(r"\b(?:don't|do\s+not|stop)\s+assum(?:e|ing)\b", re.I),
        "Do not assume motives, goals, beliefs, or meanings beyond available evidence.",
    ),
    (
        "avoid_unsolicited_advice",
        re.compile(
            r"\b(?:didn't\s+ask\s+for|don't\s+give|stop\s+giving)\s+(?:me\s+)?advice\b", re.I
        ),
        "Default to analysis; give advice only when the user explicitly requests it.",
    ),
    (
        "avoid_unnecessary_repetition",
        re.compile(r"\b(?:stop\s+repeating|you\s+(?:keep\s+)?repeated|don't\s+repeat)\b", re.I),
        "Do not repeat or quote back the user's statement unless doing so adds needed clarity.",
    ),
    (
        "use_context_before_interpreting",
        re.compile(r"\b(?:use|check|remember)\s+(?:the\s+)?(?:context|history)\b", re.I),
        "Use established conversation context before resolving an ambiguous statement.",
    ),
)

_INITIAL_PREFERENCES: tuple[tuple[str, str], ...] = (
    ("default_response_mode", "Default to analysis rather than unsolicited advice."),
    (
        "semantic_fidelity",
        "Answer the actual question without strengthening, sanitizing, or reframing it.",
    ),
    ("epistemic_labels", "Separate facts, inferences, speculation, and uncertainty when relevant."),
    (
        "assumption_boundary",
        "Do not assume motives, goals, or meanings beyond the available evidence.",
    ),
    (
        "correction_behavior",
        "Revise an interpretation when corrected; do not defend the earlier reading.",
    ),
    ("avoid_repetition", "Do not unnecessarily repeat or quote back the user's statement."),
    ("technical_language", "Use technical terminology when it genuinely improves clarity."),
    (
        "natural_language",
        "Prefer natural language over repetitive AI-internal jargon in ordinary conversation.",
    ),
    (
        "informal_language",
        "Treat profanity, fragments, speech-to-text errors, and mid-sentence corrections as "
        "valid communication.",
    ),
    ("context_continuity", "Preserve the established timeline and facts of the conversation."),
    (
        "claim_verification",
        "Before objecting to a statement, verify that the user actually made it.",
    ),
)

FEEDBACK_PREFERENCES: dict[str, str] = {
    "preserve_semantic_fidelity": (
        "Do not replace the user's meaning with a stronger, weaker, cleaner, or commoner claim."
    ),
    "avoid_unjustified_assumptions": (
        "Do not assume motives, goals, beliefs, or meanings beyond available evidence."
    ),
    "avoid_unsolicited_advice": (
        "Default to analysis; give advice only when the user explicitly requests it."
    ),
    "avoid_unnecessary_repetition": (
        "Do not repeat or quote back the user's statement unless doing so adds needed clarity."
    ),
    "use_context_before_interpreting": (
        "Use established conversation context before resolving an ambiguous statement."
    ),
}


@dataclass(slots=True)
class CommunicationPreference:
    """An auditable communication rule, separate from a durable user memory."""

    key: str
    value: str
    source: str
    confidence: float
    scope: str = "global"
    supporting_examples: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    status: str = "active"
    superseded_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "scope": self.scope,
            "supporting_examples": list(self.supporting_examples),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "superseded_at": self.superseded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommunicationPreference:
        return cls(
            key=str(data.get("key", "")),
            value=str(data.get("value", "")),
            source=str(data.get("source", "unknown")),
            confidence=_clamp(float(data.get("confidence", 0.0))),
            scope=str(data.get("scope", "global")),
            supporting_examples=[str(item) for item in data.get("supporting_examples", [])][-5:],
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            status=str(data.get("status", "active")),
            superseded_at=(
                str(data["superseded_at"]) if data.get("superseded_at") is not None else None
            ),
        )


@dataclass(slots=True)
class CommunicationCorrection:
    text: str
    preference_key: str
    scope: str
    timestamp: str = field(default_factory=lambda: _now())

    def to_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "preference_key": self.preference_key,
            "scope": self.scope,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommunicationCorrection:
        return cls(
            text=str(data.get("text", "")),
            preference_key=str(data.get("preference_key", "")),
            scope=str(data.get("scope", "global")),
            timestamp=str(data.get("timestamp", _now())),
        )


@dataclass(slots=True)
class CommunicationProfile:
    """Persistent profile of how to communicate, never what the user believes."""

    directness: float = 0.65
    verbosity: float = 0.50
    formality: float = 0.35
    humor: float = 0.20
    profanity_tolerance: float = 0.25
    technical_depth: float = 0.55
    observations: int = 0
    preferences: list[CommunicationPreference] = field(
        default_factory=lambda: _initial_preference_records()
    )
    corrections: list[CommunicationCorrection] = field(default_factory=list)
    disliked_response_patterns: list[str] = field(
        default_factory=lambda: [
            "unjustified assumptions",
            "unsolicited advice",
            "meaning-changing reframes",
            "unnecessary repetition",
        ]
    )

    def clamp(self) -> None:
        for field_name in (
            "directness",
            "verbosity",
            "formality",
            "humor",
            "profanity_tolerance",
            "technical_depth",
        ):
            setattr(self, field_name, _clamp(float(getattr(self, field_name))))
        self._ensure_initial_preferences()

    def to_dict(self) -> dict[str, Any]:
        return {
            "directness": self.directness,
            "verbosity": self.verbosity,
            "formality": self.formality,
            "humor": self.humor,
            "profanity_tolerance": self.profanity_tolerance,
            "technical_depth": self.technical_depth,
            "observations": self.observations,
            "preferences": [item.to_dict() for item in self.preferences],
            "corrections": [item.to_dict() for item in self.corrections[-50:]],
            "disliked_response_patterns": list(self.disliked_response_patterns),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommunicationProfile:
        scalar_fields = {
            key: data[key]
            for key in (
                "directness",
                "verbosity",
                "formality",
                "humor",
                "profanity_tolerance",
                "technical_depth",
                "observations",
            )
            if key in data
        }
        raw_preferences = data.get("preferences", [])
        raw_corrections = data.get("corrections", [])
        preferences = (
            [
                CommunicationPreference.from_dict(item)
                for item in raw_preferences
                if isinstance(item, dict)
            ]
            if isinstance(raw_preferences, list)
            else []
        )
        corrections = (
            [
                CommunicationCorrection.from_dict(item)
                for item in raw_corrections
                if isinstance(item, dict)
            ]
            if isinstance(raw_corrections, list)
            else []
        )
        profile = cls(
            **scalar_fields,
            preferences=preferences or _initial_preference_records(),
            corrections=corrections[-50:],
            disliked_response_patterns=[
                str(item) for item in data.get("disliked_response_patterns", [])
            ]
            or [
                "unjustified assumptions",
                "unsolicited advice",
                "meaning-changing reframes",
                "unnecessary repetition",
            ],
        )
        profile.clamp()
        return profile

    def active_preferences(self, scope: str = "global") -> list[CommunicationPreference]:
        return [
            item
            for item in self.preferences
            if item.status == "active" and (item.scope == scope or item.scope == "global")
        ]

    def record_preference(
        self,
        key: str,
        value: str,
        *,
        source: str,
        confidence: float,
        example: str | None = None,
        scope: str = "global",
    ) -> CommunicationPreference:
        now = _now()
        active = next(
            (
                item
                for item in self.preferences
                if item.key == key and item.scope == scope and item.status == "active"
            ),
            None,
        )
        if active and active.value == value:
            active.source = source
            active.confidence = max(active.confidence, _clamp(confidence))
            active.updated_at = now
            if example and example not in active.supporting_examples:
                active.supporting_examples = (active.supporting_examples + [example])[-5:]
            return active
        if active:
            active.status = "superseded"
            active.superseded_at = now
            active.updated_at = now
        preference = CommunicationPreference(
            key=key,
            value=value,
            source=source,
            confidence=_clamp(confidence),
            scope=scope,
            supporting_examples=[example] if example else [],
            created_at=now,
            updated_at=now,
        )
        self.preferences.append(preference)
        return preference

    def record_corrections(self, text: str) -> list[str]:
        updated: list[str] = []
        scope = "situational" if _SITUATIONAL.search(text) else "global"
        for key, pattern, value in _CORRECTION_RULES:
            if not pattern.search(text):
                continue
            self.record_preference(
                key,
                value,
                source="explicit_user_correction",
                confidence=1.0,
                example=text,
                scope=scope,
            )
            self.corrections.append(
                CommunicationCorrection(text=text, preference_key=key, scope=scope)
            )
            updated.append(key)
            if key == "avoid_unjustified_assumptions":
                self._add_disliked_pattern("unjustified assumptions")
            elif key == "avoid_unsolicited_advice":
                self._add_disliked_pattern("unsolicited advice")
            elif key == "avoid_unnecessary_repetition":
                self._add_disliked_pattern("unnecessary repetition")
            elif key == "preserve_semantic_fidelity":
                self._add_disliked_pattern("meaning-changing reframes")
        self.corrections = self.corrections[-50:]
        return updated

    def prompt_summary(self) -> str:
        preferences = self.active_preferences()
        lines = [
            "Adaptive communication profile (presentation and interpretation only; never mirror "
            "beliefs):",
            f"- Directness: {_label(self.directness)}",
            f"- Detail level: {_label(self.verbosity)}",
            f"- Formality: {_label(self.formality)}",
            f"- Humor frequency: {_label(self.humor)}",
            f"- Profanity tolerance: {_label(self.profanity_tolerance)}",
            f"- Technical depth: {_label(self.technical_depth)}",
            "Established communication preferences (behavior constraints, not claims about "
            "beliefs):",
        ]
        lines.extend(f"- {item.value}" for item in preferences[:16])
        lines.append("Keep humor natural and occasional. Do not overperform the user's style.")
        return "\n".join(lines)

    def _ensure_initial_preferences(self) -> None:
        existing = {item.key for item in self.preferences if item.status == "active"}
        for key, value in _INITIAL_PREFERENCES:
            if key not in existing:
                self.preferences.append(
                    CommunicationPreference(
                        key=key,
                        value=value,
                        source="explicit_user_instruction",
                        confidence=1.0,
                    )
                )

    def _add_disliked_pattern(self, value: str) -> None:
        if value not in self.disliked_response_patterns:
            self.disliked_response_patterns.append(value)


class StyleProfiler:
    """Records clear style signals and corrections without inferring preferences from silence."""

    def __init__(self, store: SQLiteStore, profile_id: str = "default") -> None:
        self.store = store
        self.profile_id = profile_id
        loaded = store.load_profile(profile_id)
        self.profile = CommunicationProfile.from_dict(loaded) if loaded else CommunicationProfile()
        if loaded is None:
            self.store.save_profile(self.profile_id, self.profile.to_dict())

    def observe(self, text: str) -> CommunicationProfile:
        # Only positive, low-risk presentation signals adjust these values. Absence of a signal is
        # deliberately not treated as a preference, disagreement, or approval.
        signals: dict[str, float] = {}
        if len(re.findall(r"\b\w+\b", text)) >= 120:
            signals["verbosity"] = 0.65
        if _HUMOR.search(text):
            signals["humor"] = 0.55
        if _PROFANITY.search(text):
            signals["profanity_tolerance"] = 0.75
        if _TECHNICAL.search(text):
            signals["technical_depth"] = 0.70

        alpha = 0.18 if self.profile.observations >= 3 else 0.35
        for name, observed in signals.items():
            current = float(getattr(self.profile, name))
            setattr(self.profile, name, (1 - alpha) * current + alpha * observed)

        self.profile.record_corrections(text)
        self.profile.observations += 1
        self.profile.clamp()
        self.store.save_profile(self.profile_id, self.profile.to_dict())
        return self.profile

    def record_feedback(
        self, preference_key: str, note: str, scope: str = "global"
    ) -> CommunicationPreference:
        """Persist a deliberate user correction without treating it as a factual memory."""

        try:
            value = FEEDBACK_PREFERENCES[preference_key]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported communication feedback category: {preference_key}"
            ) from exc
        if scope not in {"global", "situational"}:
            raise ValueError(f"Unsupported feedback scope: {scope}")
        preference = self.profile.record_preference(
            preference_key,
            value,
            source="explicit_user_feedback",
            confidence=1.0,
            example=note.strip(),
            scope=scope,
        )
        self.profile.corrections.append(
            CommunicationCorrection(
                text=note.strip(), preference_key=preference_key, scope=scope
            )
        )
        self.profile.corrections = self.profile.corrections[-50:]
        self.store.save_profile(self.profile_id, self.profile.to_dict())
        return preference


def _initial_preference_records() -> list[CommunicationPreference]:
    return [
        CommunicationPreference(
            key=key,
            value=value,
            source="explicit_user_instruction",
            confidence=1.0,
        )
        for key, value in _INITIAL_PREFERENCES
    ]


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _label(value: float) -> str:
    if value < 0.25:
        return "low"
    if value < 0.50:
        return "moderately low"
    if value < 0.75:
        return "moderate"
    return "high"


def _now() -> str:
    return datetime.now(UTC).isoformat()
