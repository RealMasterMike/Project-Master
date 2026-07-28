import pytest
from pydantic import ValidationError

from project_master.integrations.voice.manifests import (
    CHATTERBOX_PACK_TEMPLATE,
    QWEN3_TTS_PACK_TEMPLATE,
    InstalledEnginePack,
    ModelAsset,
    ModelAssetFormat,
)


def assets() -> tuple[ModelAsset, ...]:
    return (
        ModelAsset(
            logical_name="model_weights",
            relative_path="weights/model.safetensors",
            format=ModelAssetFormat.SAFETENSORS,
            sha256="1" * 64,
            size_bytes=1024,
        ),
        ModelAsset(
            logical_name="model_config",
            relative_path="config.json",
            format=ModelAssetFormat.JSON,
            sha256="2" * 64,
            size_bytes=512,
        ),
        ModelAsset(
            logical_name="tokenizer",
            relative_path="tokenizer.json",
            format=ModelAssetFormat.TOKENIZER_JSON,
            sha256="3" * 64,
            size_bytes=2048,
        ),
    )


def test_bundled_pack_templates_are_declarative_and_never_download() -> None:
    for template in (QWEN3_TTS_PACK_TEMPLATE, CHATTERBOX_PACK_TEMPLATE):
        payload = template.model_dump(mode="json")
        assert payload["install_mode"] == "user_managed"
        assert payload["automatic_download"] is False
        assert "download_url" not in payload
        assert "install_command" not in payload
        assert len(template.digest) == 64


def test_installed_pack_requires_complete_safe_verified_inventory() -> None:
    pack = InstalledEnginePack.from_template(
        QWEN3_TTS_PACK_TEMPLATE,
        installed_version="test-version",
        assets=assets(),
    )

    assert pack.engine_id == "qwen3-tts"
    assert pack.asset_digests == ("1" * 64, "2" * 64, "3" * 64)
    assert len(pack.digest) == 64
    with pytest.raises(ValueError, match="Missing required"):
        InstalledEnginePack.from_template(
            QWEN3_TTS_PACK_TEMPLATE,
            installed_version="test-version",
            assets=assets()[:1],
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "../weights/model.safetensors",
        "/absolute/model.safetensors",
        r"weights\model.safetensors",
        "weights/model.pt",
    ],
)
def test_model_assets_reject_escape_and_executable_serialization(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError):
        ModelAsset(
            logical_name="model_weights",
            relative_path=relative_path,
            format=ModelAssetFormat.SAFETENSORS,
            sha256="1" * 64,
            size_bytes=1024,
        )


def test_chatterbox_declares_pinned_weights_only_checkpoints_truthfully() -> None:
    checkpoint = ModelAsset(
        logical_name="voice_encoder_checkpoint",
        relative_path="models/ve.pt",
        format=ModelAssetFormat.PYTORCH_CHECKPOINT,
        sha256="4" * 64,
        size_bytes=1024,
    )

    requirement = next(
        item
        for item in CHATTERBOX_PACK_TEMPLATE.asset_requirements
        if item.logical_name == checkpoint.logical_name
    )
    assert checkpoint.format in requirement.allowed_formats
    assert "weights_only" in CHATTERBOX_PACK_TEMPLATE.install_notes
