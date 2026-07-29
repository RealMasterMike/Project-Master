from __future__ import annotations

from importlib.resources import files

from project_master.personality.profile import CommunicationProfile


class PromptBuilder:
    def __init__(self) -> None:
        self.base_prompt = (
            files("project_master.resources")
            .joinpath("system_prompt.md")
            .read_text(encoding="utf-8")
        )

    def build(
        self,
        profile: CommunicationProfile,
        memory_context: str = "",
        interpretation_context: str = "",
    ) -> str:
        # The adaptive communication profile is deliberately NOT injected. It rewrote the
        # model's own instructions from observed user style (directness, humor, profanity
        # tolerance, and up to 16 learned preferences), which made behavior drift in ways
        # the user could not see or control. StyleProfiler still records observations, so
        # re-enabling this is a one-line change: append profile.prompt_summary() here.
        del profile
        sections = [self.base_prompt.strip()]
        if memory_context.strip():
            sections.append(
                "Relevant stored context follows. Treat it as context with provenance, "
                "not automatic truth:\n" + memory_context.strip()
            )
        if interpretation_context.strip():
            sections.append(interpretation_context.strip())
        return "\n\n".join(sections)
