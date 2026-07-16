from __future__ import annotations

import re

from project_master.communication.interpretation import ConversationInterpretation
from project_master.core.models import AuditFinding

_ABSOLUTES = re.compile(
    r"\b(definitely|certainly|obviously|undeniably|proven|guaranteed|always|never)\b",
    re.IGNORECASE,
)
_UNIVERSAL_AUTHORITY = re.compile(
    r"\b(every scientist|all experts|everyone knows|nobody disputes)\b",
    re.IGNORECASE,
)
_HUMAN_EMOTION_CLAIMS = re.compile(
    r"\b(i feel|i am excited|i'm excited|i love you|i personally want|i get tired)\b",
    re.IGNORECASE,
)
_SOURCE_LANGUAGE = re.compile(r"\b(source|evidence|study|report|document|data|transcript)\b", re.I)
_CONFIDENCE_LANGUAGE = re.compile(
    r"\b(confidence|uncertain|likely|unlikely|appears|suggests|may|might)\b", re.I
)
_USER_ATTRIBUTION = re.compile(
    r"\byou\s+(?:said|stated|claimed|argued|believe|think|want|mean)\s+"
    r"(?:that\s+)?(?P<claim>[^.?!\n]+)",
    re.IGNORECASE,
)
_UNSOLICITED_ADVICE = re.compile(
    r"\b(?:you\s+should|you\s+need\s+to|i\s+recommend|the\s+best\s+move\s+is)\b",
    re.IGNORECASE,
)
_INVALIDATES_TONE = re.compile(
    r"\b(?:because\s+you(?:'re|\s+are)\s+(?:angry|emotional)|"
    r"your\s+(?:profanity|tone)\s+(?:means|shows|proves))\b",
    re.IGNORECASE,
)
_BELIEF_MIRRORING = re.compile(r"\b(?:you're\s+right|you\s+are\s+right|i\s+agree)\b", re.I)
_RESTART_FROM_SCRATCH = re.compile(
    r"\b(?:start\s+(?:it|the\s+project)?\s*from\s+scratch|create\s+a\s+new\s+project|"
    r"build\s+a\s+new\s+project)\b",
    re.IGNORECASE,
)
_EXISTING_PROJECT = re.compile(
    r"\b(?:project(?:\s+\w+){0,2}\s+(?:already\s+)?exists|already\s+(?:have|built|created)|"
    r"existing\s+project)\b",
    re.IGNORECASE,
)
_INFERENCE_AS_FACT = re.compile(r"\b(?:this\s+proves|therefore\s+it\s+is\s+clear)\b", re.I)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
    "you",
    "your",
}


def audit_response(
    text: str, interpretation: ConversationInterpretation | None = None
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if _ABSOLUTES.search(text) and not _SOURCE_LANGUAGE.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="possible-overconfidence",
                message="Absolute language appears without nearby evidence or source language.",
            )
        )
    if _UNIVERSAL_AUTHORITY.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="universal-authority-claim",
                message=(
                    "Universal claims about experts or consensus should be sourced or narrowed."
                ),
            )
        )
    if _HUMAN_EMOTION_CLAIMS.search(text):
        findings.append(
            AuditFinding(
                severity="info",
                code="ai-emotion-framing",
                message="Human-like emotional language may misrepresent the assistant's nature.",
            )
        )
    if len(text) > 700 and not _CONFIDENCE_LANGUAGE.search(text):
        findings.append(
            AuditFinding(
                severity="info",
                code="missing-calibration-language",
                message="A long answer contains no visible uncertainty or confidence language.",
            )
        )
    if interpretation is not None:
        findings.extend(_audit_semantic_fidelity(text, interpretation))
    return findings


def _audit_semantic_fidelity(
    text: str, interpretation: ConversationInterpretation
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if _has_unsupported_user_attribution(text, interpretation):
        findings.append(
            AuditFinding(
                severity="error",
                code="unsupported-user-attribution",
                message=(
                    "The response attributes a claim, motive, or preference to the user without "
                    "matching support in the supplied user messages."
                ),
            )
        )
    if not interpretation.response_plan.advice_requested and _UNSOLICITED_ADVICE.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="unsolicited-advice",
                message=(
                    "The response gives a recommendation even though the user did not "
                    "explicitly ask for advice."
                ),
            )
        )
    if _unnecessary_repetition(text, interpretation.literal_user_text):
        findings.append(
            AuditFinding(
                severity="info",
                code="unnecessary-user-repetition",
                message="The opening appears to repeat the user's wording without adding analysis.",
            )
        )
    if _INVALIDATES_TONE.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="tone-invalidates-reasoning",
                message=(
                    "The response treats emotional intensity or profanity as evidence that "
                    "the user's reasoning is invalid."
                ),
            )
        )
    if _BELIEF_MIRRORING.search(text) and not _SOURCE_LANGUAGE.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="possible-belief-mirroring",
                message="Agreement language appears without an evidence-based assessment.",
            )
        )
    if _INFERENCE_AS_FACT.search(text):
        findings.append(
            AuditFinding(
                severity="warning",
                code="inference-presented-as-fact",
                message=(
                    "The response uses proof-like language for a conclusion that may be an "
                    "inference."
                ),
            )
        )
    user_context = "\n".join(interpretation.relevant_prior_context)
    if _EXISTING_PROJECT.search(user_context) and _RESTART_FROM_SCRATCH.search(text):
        findings.append(
            AuditFinding(
                severity="error",
                code="contradicts-established-project-context",
                message=(
                    "The response proposes recreating a project that established context says "
                    "already exists."
                ),
            )
        )
    if _reintroduces_rejected_interpretation(text, interpretation.rejected_interpretations):
        findings.append(
            AuditFinding(
                severity="error",
                code="reintroduces-rejected-interpretation",
                message="The response repeats an interpretation the user explicitly rejected.",
            )
        )
    return findings


def _has_unsupported_user_attribution(
    text: str, interpretation: ConversationInterpretation
) -> bool:
    user_text = " ".join(
        [interpretation.literal_user_text]
        + [
            item.removeprefix("user: ")
            for item in interpretation.relevant_prior_context
            if item.startswith("user: ")
        ]
    )
    user_tokens = set(_significant_tokens(user_text))
    for match in _USER_ATTRIBUTION.finditer(text):
        attributed_tokens = set(_significant_tokens(match.group("claim")))
        if len(attributed_tokens) >= 2 and not attributed_tokens.issubset(user_tokens):
            return True
    return False


def _unnecessary_repetition(text: str, user_text: str) -> bool:
    response_opening = re.split(r"[.?!\n]", text, maxsplit=1)[0]
    response_tokens = set(_significant_tokens(response_opening))
    user_tokens = set(_significant_tokens(user_text))
    if len(user_tokens) < 5 or len(response_tokens) < 5:
        return False
    return len(response_tokens & user_tokens) / len(user_tokens) >= 0.85


def _reintroduces_rejected_interpretation(text: str, rejected: list[str]) -> bool:
    response = set(_significant_tokens(text))
    for phrase in rejected:
        tokens = set(_significant_tokens(phrase))
        if len(tokens) >= 2 and tokens.issubset(response):
            return True
    return False


def _significant_tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", text)
        if len(token) > 2 and token.lower() not in _STOP_WORDS
    ]
