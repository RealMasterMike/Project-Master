from project_master.core.prompting import PromptBuilder
from project_master.personality.profile import CommunicationProfile


def test_system_prompt_defines_a_grounded_first_session_and_capability_contract() -> None:
    prompt = PromptBuilder().build(CommunicationProfile())

    assert "What are you working through today" in prompt
    assert "Do not turn this into a long onboarding questionnaire" in prompt
    assert "Do not use emojis, hype, glitter, magic, space, aliens" in prompt
    assert (
        "Do not claim to have web search or browsing, background jobs, recurring reminders"
        in prompt
    )
    assert "File writing may be disabled" in prompt
