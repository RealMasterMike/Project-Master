from project_master.core.prompting import PromptBuilder
from project_master.personality.profile import CommunicationProfile


def test_system_prompt_keeps_the_instructions_tool_calling_depends_on() -> None:
    prompt = PromptBuilder().build(CommunicationProfile())

    assert "Use tools when they materially improve accuracy or completion" in prompt
    assert "Never claim an action succeeded unless the tool result supports" in prompt
    assert "Write durable memory only when the user explicitly asks" in prompt
    assert "Keep file operations inside the configured workspace" in prompt
    assert "Do not claim to have a capability that no currently enabled tool provides" in prompt


def test_system_prompt_carries_no_persona_or_style_direction() -> None:
    # Mike removed all personalization on 2026-07-28 and authors this file himself. These
    # assertions exist so a future edit cannot quietly reintroduce generated persona.
    prompt = PromptBuilder().build(CommunicationProfile())

    for removed in (
        "What are you working through today",  # scripted first-session intake
        "glitter",  # tone and voice direction
        "Thinking level is not a simple quality scale",  # thinking-mode policy
        "Adapt tone, directness, detail, and humor",  # style adaptation
        "Claim being evaluated",  # imposed nine-point epistemics template
    ):
        assert removed not in prompt


def test_adaptive_communication_profile_is_not_injected_into_the_prompt() -> None:
    # The profile still observes style, but it must not rewrite the model's instructions.
    profile = CommunicationProfile()
    profile.humor = 0.95
    profile.profanity_tolerance = 0.95
    profile.directness = 0.95

    prompt = PromptBuilder().build(profile)

    assert "Adaptive communication profile" not in prompt
    assert "Humor frequency" not in prompt
    assert "Profanity tolerance" not in prompt
    assert profile.prompt_summary() not in prompt


def test_memory_and_interpretation_context_still_reach_the_prompt() -> None:
    prompt = PromptBuilder().build(
        CommunicationProfile(),
        memory_context="stored fact about the workspace",
        interpretation_context="the user is continuing a prior request",
    )

    assert "stored fact about the workspace" in prompt
    assert "the user is continuing a prior request" in prompt
