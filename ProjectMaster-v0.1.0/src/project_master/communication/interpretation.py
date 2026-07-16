from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from project_master.core.models import Message

MessageIntent = Literal[
    "advice_request", "analysis_request", "correction", "instruction", "question", "statement"
]

_ADVICE_REQUEST = re.compile(
    r"\b(?:what\s+should\s+i\s+do|what\s+do\s+you\s+recommend|give\s+me\s+advice|"
    r"should\s+i|recommend\s+(?:a|an|the)?\s*\w*)\b",
    re.IGNORECASE,
)
_ANALYSIS_REQUEST = re.compile(
    r"\b(?:analy[sz]e|evaluate|assess|break\s+(?:this|it)\s+down|what\s+do\s+you\s+think|"
    r"is\s+this\s+(?:a\s+)?(?:good|bad)\s+idea|tell\s+me\s+whether)\b",
    re.IGNORECASE,
)
_CORRECTION = re.compile(
    r"\b(?:i\s+(?:didn't|did\s+not)\s+say|that's\s+not\s+what\s+i\s+said|"
    r"you\s+(?:misunderstood|misread|changed\s+my\s+meaning)|don't\s+(?:assume|reframe|"
    r"put\s+words\s+in\s+my\s+mouth)|stop\s+(?:assuming|repeating))\b",
    re.IGNORECASE,
)
_INSTRUCTION = re.compile(
    r"^(?:please\s+)?(?:build|create|change|update|remove|add|write|read|inspect|test|run|"
    r"implement|explain|summari[sz]e|compare|research|look\s+into|show)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"^(?:what|why|how|when|where|who|can|could|would|do|does|is|are|should)\b", re.I
)
_REJECTED_PHRASE = re.compile(
    r"\b(?:i\s+(?:didn't|did\s+not)\s+say|not\s+that|rather\s+than|"
    r"(?:does|do)\s+not\s+mean)\s+(?P<phrase>[^.?!\n]+)",
    re.IGNORECASE,
)
_CORRECTION_CONTEXT = re.compile(
    r"\b(?:i\s+(?:didn't|did\s+not)\s+say|that's\s+not\s+what\s+i\s+said|"
    r"don't\s+(?:assume|reframe)|stop\s+(?:assuming|repeating))\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ResponsePlan:
    """Operational response constraints, never a claim about the user's beliefs."""

    mode: Literal["advice", "analysis", "answer", "action", "correction", "acknowledge"]
    advice_requested: bool
    clarification_preferred: bool

    def prompt_summary(self) -> str:
        return (
            "Response plan (operational, not a user belief):\n"
            f"- Mode: {self.mode}\n"
            f"- Advice explicitly requested: {'yes' if self.advice_requested else 'no'}\n"
            f"- Clarification preferred if ambiguity remains material: "
            f"{'yes' if self.clarification_preferred else 'no'}"
        )


@dataclass(slots=True)
class ConversationInterpretation:
    """A transparent, conservative reading of one turn and its immediate context."""

    literal_user_text: str
    message_intent: MessageIntent
    likely_intended_meaning: str
    confidence: float
    ambiguities: list[str] = field(default_factory=list)
    inferred_information: list[str] = field(default_factory=list)
    relevant_prior_context: list[str] = field(default_factory=list)
    rejected_interpretations: list[str] = field(default_factory=list)
    response_plan: ResponsePlan = field(
        default_factory=lambda: ResponsePlan(
            mode="acknowledge", advice_requested=False, clarification_preferred=False
        )
    )

    def prompt_summary(self) -> str:
        lines = [
            "Communication fidelity record (generated from the current turn and recent history):",
            "- Literal current user text (do not clean up, strengthen, or paraphrase it):",
            f'  "{self.literal_user_text}"',
            f"- Message intent (operational classification, not a fact): {self.message_intent}",
            f"- Likely requested handling (inference, not a quoted user claim): "
            f"{self.likely_intended_meaning} (confidence {self.confidence:.2f})",
        ]
        if self.ambiguities:
            lines.append("- Ambiguities that remain unresolved: " + "; ".join(self.ambiguities))
        if self.inferred_information:
            lines.append(
                "- Inferred information (never present this as an explicit user statement): "
                + "; ".join(self.inferred_information)
            )
        if self.relevant_prior_context:
            lines.append("- Relevant prior context (verbatim excerpts):")
            lines.extend(f"  - {item}" for item in self.relevant_prior_context)
        if self.rejected_interpretations:
            lines.append(
                "- Interpretations the user rejected; do not reintroduce them as the user's view: "
                + "; ".join(self.rejected_interpretations)
            )
        lines.extend(
            [
                self.response_plan.prompt_summary(),
                "Fidelity requirements:",
                "- Answer the literal message in light of established context, not a statistically "
                "commoner or cleaner version of it.",
                "- Before challenging a user claim, verify that the user actually made that claim.",
                "- Label any interpretation you introduce as an inference; never attribute it to "
                "the user.",
                "- Do not give advice unless it was explicitly requested. Analysis is not a "
                "request for a plan.",
                "- If a material ambiguity cannot be resolved from the supplied context, state the "
                "uncertainty or ask one focused clarification instead of guessing.",
            ]
        )
        return "\n".join(lines)


def interpret_conversation(
    history: list[dict[str, str]] | list[Message], user_text: str
) -> ConversationInterpretation:
    """Classify a turn conservatively without converting inference into user fact."""

    text = user_text.strip()
    intent = _message_intent(text)
    prior_context = _relevant_context(history)
    rejected = _rejected_interpretations(history, text)
    ambiguities = _ambiguities(text, history)
    plan = _response_plan(intent, ambiguities)
    likely, confidence = _likely_meaning(intent, text)
    inferred = [] if confidence >= 0.9 else [likely]
    return ConversationInterpretation(
        literal_user_text=text,
        message_intent=intent,
        likely_intended_meaning=likely,
        confidence=confidence,
        ambiguities=ambiguities,
        inferred_information=inferred,
        relevant_prior_context=prior_context,
        rejected_interpretations=rejected,
        response_plan=plan,
    )


def _message_intent(text: str) -> MessageIntent:
    if _CORRECTION.search(text):
        return "correction"
    if _ADVICE_REQUEST.search(text):
        return "advice_request"
    if _ANALYSIS_REQUEST.search(text):
        return "analysis_request"
    if _INSTRUCTION.search(text):
        return "instruction"
    if "?" in text or _QUESTION.search(text):
        return "question"
    return "statement"


def _likely_meaning(intent: MessageIntent, text: str) -> tuple[str, float]:
    if intent == "correction":
        return "The user is correcting a prior interpretation or response behavior.", 0.94
    if intent == "advice_request":
        return "The user explicitly asks for a recommendation or next action.", 0.94
    if intent == "analysis_request":
        return "The user asks for analysis or evaluation, not automatically a recommendation.", 0.90
    if intent == "instruction":
        return "The user asks the assistant to perform the stated action.", 0.92
    if intent == "question":
        return "The user asks the question as written.", 0.88
    if text:
        return (
            "The user contributes information or thought without a clearly explicit request.",
            0.62,
        )
    return "No interpretable user content is present.", 0.0


def _response_plan(intent: MessageIntent, ambiguities: list[str]) -> ResponsePlan:
    if intent == "advice_request":
        mode: Literal["advice", "analysis", "answer", "action", "correction", "acknowledge"] = (
            "advice"
        )
    elif intent == "analysis_request":
        mode = "analysis"
    elif intent == "instruction":
        mode = "action"
    elif intent == "correction":
        mode = "correction"
    elif intent == "question":
        mode = "answer"
    else:
        mode = "acknowledge"
    return ResponsePlan(
        mode=mode,
        advice_requested=intent == "advice_request",
        clarification_preferred=bool(ambiguities),
    )


def _relevant_context(history: list[dict[str, str]] | list[Message]) -> list[str]:
    normalized = [_history_item(item) for item in history]
    selected = [
        item for item in normalized if item[0] == "user" and _CORRECTION_CONTEXT.search(item[1])
    ]
    selected.extend(normalized[-2:])
    result: list[str] = []
    for role, content in selected[-4:]:
        excerpt = " ".join(content.split())
        if not excerpt:
            continue
        item = f"{role}: {excerpt[:360]}"
        if item not in result:
            result.append(item)
    return result


def _rejected_interpretations(
    history: list[dict[str, str]] | list[Message], current_text: str
) -> list[str]:
    rejected: list[str] = []
    for role, content in [_history_item(item) for item in history] + [("user", current_text)]:
        if role != "user":
            continue
        for match in _REJECTED_PHRASE.finditer(content):
            phrase = match.group("phrase").strip(" ,;:-")
            if len(phrase) >= 4 and phrase not in rejected:
                rejected.append(phrase[:180])
    return rejected[-4:]


def _ambiguities(text: str, history: list[dict[str, str]] | list[Message]) -> list[str]:
    words = re.findall(r"\b\w+\b", text)
    if len(words) <= 4 and re.search(r"\b(?:it|this|that|they|there)\b", text, re.I):
        if not history:
            return ["A short reference may lack an established referent."]
    return []


def _history_item(item: dict[str, str] | Message) -> tuple[str, str]:
    if isinstance(item, Message):
        return item.role, item.content
    return str(item.get("role", "")), str(item.get("content", ""))
