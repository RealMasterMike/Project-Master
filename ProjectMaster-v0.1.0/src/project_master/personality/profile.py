from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from project_master.memory.store import SQLiteStore

_PROFANITY = re.compile(r"\b(fuck|fucking|shit|damn|ass|bullshit)\b", re.IGNORECASE)
_HUMOR = re.compile(r"(?:\blol\b|\blmao\b|😂|🤣|haha|joking)", re.IGNORECASE)
_HEDGES = re.compile(r"\b(maybe|probably|i think|i guess|not sure|could be)\b", re.IGNORECASE)


@dataclass(slots=True)
class CommunicationProfile:
    directness: float = 0.65
    verbosity: float = 0.50
    formality: float = 0.35
    humor: float = 0.20
    profanity_tolerance: float = 0.25
    technical_depth: float = 0.55
    observations: int = 0

    def clamp(self) -> None:
        for field_name in (
            "directness",
            "verbosity",
            "formality",
            "humor",
            "profanity_tolerance",
            "technical_depth",
        ):
            value = float(getattr(self, field_name))
            setattr(self, field_name, min(1.0, max(0.0, value)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommunicationProfile:
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        profile = cls(**{key: value for key, value in data.items() if key in allowed})
        profile.clamp()
        return profile

    def prompt_summary(self) -> str:
        return (
            "Adaptive communication profile (presentation only; never mirror beliefs):\n"
            f"- Directness: {_label(self.directness)}\n"
            f"- Detail level: {_label(self.verbosity)}\n"
            f"- Formality: {_label(self.formality)}\n"
            f"- Humor frequency: {_label(self.humor)}\n"
            f"- Profanity tolerance: {_label(self.profanity_tolerance)}\n"
            f"- Technical depth: {_label(self.technical_depth)}\n"
            "Keep humor natural and occasional. Do not overperform the user's style."
        )


class StyleProfiler:
    def __init__(self, store: SQLiteStore, profile_id: str = "default") -> None:
        self.store = store
        self.profile_id = profile_id
        loaded = store.load_profile(profile_id)
        self.profile = CommunicationProfile.from_dict(loaded) if loaded else CommunicationProfile()

    def observe(self, text: str) -> CommunicationProfile:
        words = re.findall(r"\b\w+\b", text)
        word_count = len(words)
        sentence_count = max(1, len(re.findall(r"[.!?]+", text)))
        avg_sentence = word_count / sentence_count

        signals = {
            "verbosity": min(1.0, word_count / 180.0),
            "directness": 0.75 if avg_sentence < 22 else 0.55,
            "formality": 0.25 if _PROFANITY.search(text) else 0.50,
            "humor": 0.75 if _HUMOR.search(text) else 0.15,
            "profanity_tolerance": 0.90 if _PROFANITY.search(text) else 0.20,
            "technical_depth": 0.70
            if re.search(r"\b(model|tool|agent|architecture|code|evidence)\b", text, re.I)
            else 0.45,
        }
        if _HEDGES.search(text):
            signals["directness"] *= 0.9

        alpha = 0.18 if self.profile.observations >= 3 else 0.35
        for name, observed in signals.items():
            current = float(getattr(self.profile, name))
            setattr(self.profile, name, (1 - alpha) * current + alpha * observed)

        self.profile.observations += 1
        self.profile.clamp()
        self.store.save_profile(self.profile_id, self.profile.to_dict())
        return self.profile


def _label(value: float) -> str:
    if value < 0.25:
        return "low"
    if value < 0.50:
        return "moderately low"
    if value < 0.75:
        return "moderate"
    return "high"
