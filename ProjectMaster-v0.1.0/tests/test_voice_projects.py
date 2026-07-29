from datetime import UTC, datetime

from project_master.integrations.voice.cache import build_chunk_plan
from project_master.integrations.voice.manifests import (
    QWEN3_TTS_PACK_TEMPLATE,
    InstalledEnginePack,
    ModelAsset,
    ModelAssetFormat,
)
from project_master.integrations.voice.profiles import (
    ConsentRecord,
    ConsentScope,
    RightsBasis,
    VoiceProfile,
    VoiceReference,
)
from project_master.integrations.voice.projects import (
    PronunciationEntry,
    RenderSettings,
    ScriptBlock,
    VoiceProject,
    VoiceWorkflowOrigin,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def pack() -> InstalledEnginePack:
    return InstalledEnginePack.from_template(
        QWEN3_TTS_PACK_TEMPLATE,
        installed_version="test",
        assets=(
            ModelAsset(
                logical_name="model_weights",
                relative_path="model.safetensors",
                format=ModelAssetFormat.SAFETENSORS,
                sha256="1" * 64,
                size_bytes=100,
            ),
            ModelAsset(
                logical_name="model_config",
                relative_path="config.json",
                format=ModelAssetFormat.JSON,
                sha256="2" * 64,
                size_bytes=100,
            ),
            ModelAsset(
                logical_name="tokenizer",
                relative_path="tokenizer.json",
                format=ModelAssetFormat.TOKENIZER_JSON,
                sha256="3" * 64,
                size_bytes=100,
            ),
        ),
    )


def profile() -> VoiceProfile:
    record = ConsentRecord(
        id="consent-1",
        basis=RightsBasis.SELF_VOICE,
        scopes=(ConsentScope.VOICE_GENERATION,),
        subject_label="Owner",
        attested_by_user=True,
        granted_at=NOW,
    )
    return VoiceProfile.create(
        profile_id="voice-1",
        name="Voice",
        mode="reference",
        language="en",
        consent=record,
        references=(
            VoiceReference(
                artifact_id="reference-1",
                sha256="a" * 64,
                media_type="audio/wav",
                duration_seconds=10,
                sample_rate_hz=24_000,
                channels=1,
            ),
        ),
        created_at=NOW,
    )


def project(name: str = "Project", revision: int = 1) -> VoiceProject:
    return VoiceProject.create(
        project_id="project-1",
        name=name,
        language="en",
        default_voice_profile_id="voice-1",
        blocks=(
            ScriptBlock(
                id="block-1",
                text=(
                    "Project Master speaks the first sentence. "
                    "This second sentence is deliberately long enough to produce another chunk."
                ),
            ),
            ScriptBlock(id="block-2", text="Project Master speaks again."),
        ),
        pronunciations=(
            PronunciationEntry(
                id="pm",
                term="Project Master",
                pronunciation="Project Mass-ter",
                language="en",
            ),
        ),
        created_at=NOW,
        revision=revision,
    )


def test_chunk_ids_and_cache_keys_are_deterministic_and_bounded() -> None:
    voice = profile()
    settings = RenderSettings(max_chunk_characters=60, base_seed=42)
    first = build_chunk_plan(
        project(),
        {voice.id: voice},
        pack(),
        settings,
        engine_max_characters=100,
    )
    second = build_chunk_plan(
        project(),
        {voice.id: voice},
        pack(),
        settings,
        engine_max_characters=100,
    )

    assert first == second
    assert all(len(chunk.text) <= 60 for chunk in first)
    assert len({chunk.id for chunk in first}) == len(first)
    assert first[0].pronunciations[0].term == "Project Master"


def test_non_synthesis_project_revision_changes_instance_not_cache_identity() -> None:
    voice = profile()
    settings = RenderSettings(max_chunk_characters=60)
    first = build_chunk_plan(
        project("First name", 1),
        {voice.id: voice},
        pack(),
        settings,
        engine_max_characters=100,
    )
    renamed = build_chunk_plan(
        project("Renamed", 2),
        {voice.id: voice},
        pack(),
        settings,
        engine_max_characters=100,
    )

    assert [chunk.id for chunk in first] != [chunk.id for chunk in renamed]
    assert [chunk.cache_key for chunk in first] == [
        chunk.cache_key for chunk in renamed
    ]


def test_legacy_chat_speech_project_is_classified_as_internal() -> None:
    legacy = VoiceProject.create(
        project_id="chat-speech-legacy",
        name="Chat speech",
        language="en",
        default_voice_profile_id="voice-1",
        blocks=(ScriptBlock(id="message", text="Legacy speech."),),
        created_at=NOW,
    ).model_dump()
    legacy.pop("origin")

    restored = VoiceProject.model_validate(legacy)

    assert restored.origin is VoiceWorkflowOrigin.CHAT_SPEECH
    assert restored.studio_visible is False
