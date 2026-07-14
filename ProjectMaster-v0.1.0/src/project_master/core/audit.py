from __future__ import annotations

import re

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


def audit_response(text: str) -> list[AuditFinding]:
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
    return findings
