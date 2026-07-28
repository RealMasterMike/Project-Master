from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EngineCapability(StrEnum):
    REFERENCE_VOICE = "reference_voice"
    VOICE_DESIGN = "voice_design"
    MULTILINGUAL = "multilingual"
    EXPRESSIVE_CONTROL = "expressive_control"
    PRONUNCIATION = "pronunciation"
    STREAMING = "streaming"
    TIMESTAMPS = "timestamps"


class ModelAssetFormat(StrEnum):
    SAFETENSORS = "safetensors"
    PYTORCH_CHECKPOINT = "pytorch_checkpoint"
    ONNX = "onnx"
    GGUF = "gguf"
    JSON = "json"
    YAML = "yaml"
    TEXT = "text"
    TOKENIZER_JSON = "tokenizer_json"
    SENTENCEPIECE = "sentencepiece"


_FORMAT_EXTENSIONS: dict[ModelAssetFormat, frozenset[str]] = {
    ModelAssetFormat.SAFETENSORS: frozenset({".safetensors"}),
    ModelAssetFormat.PYTORCH_CHECKPOINT: frozenset({".pt", ".pth"}),
    ModelAssetFormat.ONNX: frozenset({".onnx"}),
    ModelAssetFormat.GGUF: frozenset({".gguf"}),
    ModelAssetFormat.JSON: frozenset({".json"}),
    ModelAssetFormat.YAML: frozenset({".yaml", ".yml"}),
    ModelAssetFormat.TEXT: frozenset({".txt"}),
    ModelAssetFormat.TOKENIZER_JSON: frozenset({".json"}),
    ModelAssetFormat.SENTENCEPIECE: frozenset({".model", ".spm"}),
}


class AssetRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_name: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$"
    )
    description: str = Field(min_length=1, max_length=300)
    allowed_formats: tuple[ModelAssetFormat, ...]
    required: bool = True

    @field_validator("allowed_formats")
    @classmethod
    def require_formats(
        cls, value: tuple[ModelAssetFormat, ...]
    ) -> tuple[ModelAssetFormat, ...]:
        if not value:
            raise ValueError("Voice engine asset requirements need an allowed format.")
        return tuple(dict.fromkeys(value))


class ModelAsset(BaseModel):
    """Verified metadata for a user-installed, non-executable model asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_name: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$"
    )
    relative_path: str = Field(min_length=1, max_length=500)
    format: ModelAssetFormat
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=500_000_000_000)

    @model_validator(mode="after")
    def validate_safe_path_and_extension(self) -> ModelAsset:
        if "\\" in self.relative_path or "\x00" in self.relative_path:
            raise ValueError("Voice model assets require normalized relative POSIX paths.")
        if any(part in {"", ".", ".."} for part in self.relative_path.split("/")):
            raise ValueError("Voice model asset path must remain inside its pack.")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute():
            raise ValueError("Voice model asset path must remain inside its pack.")
        if path.suffix.lower() not in _FORMAT_EXTENSIONS[self.format]:
            raise ValueError(
                f"Voice model asset extension does not match safe format {self.format}."
            )
        return self


class EnginePackTemplate(BaseModel):
    """Declarative metadata only; templates cannot download or execute installers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    engine_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    upstream_homepage: str
    version_guidance: str = Field(min_length=1, max_length=200)
    capabilities: tuple[EngineCapability, ...]
    asset_requirements: tuple[AssetRequirement, ...]
    license_notice: str = Field(min_length=1, max_length=500)
    install_notes: str = Field(min_length=1, max_length=1000)
    install_mode: Literal["user_managed"] = "user_managed"
    automatic_download: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> EnginePackTemplate:
        if not self.capabilities:
            raise ValueError("Voice engine pack needs at least one capability.")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Voice engine capabilities must be unique.")
        names = [requirement.logical_name for requirement in self.asset_requirements]
        if len(names) != len(set(names)):
            raise ValueError("Voice engine asset requirement names must be unique.")
        if not self.upstream_homepage.startswith("https://"):
            raise ValueError("Voice engine homepage must use HTTPS.")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.model_dump(mode="json")).encode()).hexdigest()


class InstalledEnginePack(BaseModel):
    """A verified inventory; loading and inference remain the adapter's responsibility."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.-]*$")
    template_id: str
    engine_id: str
    installed_version: str = Field(min_length=1, max_length=100)
    template_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: tuple[EngineCapability, ...]
    assets: tuple[ModelAsset, ...]

    @model_validator(mode="after")
    def validate_inventory(self) -> InstalledEnginePack:
        if not self.capabilities:
            raise ValueError("Installed voice engine pack needs a capability.")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Installed voice engine capabilities must be unique.")
        names = [asset.logical_name for asset in self.assets]
        if len(names) != len(set(names)):
            raise ValueError("Installed voice model asset names must be unique.")
        return self

    @classmethod
    def from_template(
        cls,
        template: EnginePackTemplate,
        *,
        installed_version: str,
        assets: tuple[ModelAsset, ...] | list[ModelAsset],
        pack_id: str | None = None,
    ) -> InstalledEnginePack:
        inventory = tuple(assets)
        requirements = {item.logical_name: item for item in template.asset_requirements}
        provided: set[str] = set()
        issues: list[str] = []
        for asset in inventory:
            requirement = requirements.get(asset.logical_name)
            if requirement is None:
                issues.append(f"Unexpected model asset {asset.logical_name!r}.")
                continue
            if asset.logical_name in provided:
                issues.append(f"Duplicate model asset {asset.logical_name!r}.")
            provided.add(asset.logical_name)
            if asset.format not in requirement.allowed_formats:
                issues.append(
                    f"Asset {asset.logical_name!r} uses unsupported format {asset.format}."
                )
        missing = sorted(
            name
            for name, requirement in requirements.items()
            if requirement.required and name not in provided
        )
        if missing:
            issues.append(f"Missing required model assets: {', '.join(missing)}.")
        if issues:
            raise ValueError("; ".join(issues))
        return cls(
            id=pack_id or f"{template.id}-installed",
            template_id=template.id,
            engine_id=template.engine_id,
            installed_version=installed_version,
            template_digest=template.digest,
            capabilities=template.capabilities,
            assets=inventory,
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.model_dump(mode="json")).encode()).hexdigest()

    @property
    def asset_digests(self) -> tuple[str, ...]:
        return tuple(sorted(asset.sha256 for asset in self.assets))


QWEN3_TTS_PACK_TEMPLATE = EnginePackTemplate(
    id="qwen3-tts-local",
    engine_id="qwen3-tts",
    display_name="Qwen3-TTS Local",
    upstream_homepage="https://github.com/QwenLM/Qwen3-TTS",
    version_guidance="Choose and pin a compatible upstream release locally.",
    capabilities=(
        EngineCapability.REFERENCE_VOICE,
        EngineCapability.VOICE_DESIGN,
        EngineCapability.MULTILINGUAL,
        EngineCapability.EXPRESSIVE_CONTROL,
        EngineCapability.PRONUNCIATION,
    ),
    asset_requirements=(
        AssetRequirement(
            logical_name="model_weights",
            description="Primary model weights.",
            allowed_formats=(ModelAssetFormat.SAFETENSORS,),
        ),
        AssetRequirement(
            logical_name="model_config",
            description="Pinned model configuration.",
            allowed_formats=(ModelAssetFormat.JSON,),
        ),
        AssetRequirement(
            logical_name="tokenizer",
            description="Tokenizer vocabulary and configuration.",
            allowed_formats=(
                ModelAssetFormat.TOKENIZER_JSON,
                ModelAssetFormat.SENTENCEPIECE,
            ),
        ),
    ),
    license_notice=(
        "Review and accept the upstream code, model, and voice-use licenses before installation."
    ),
    install_notes=(
        "Install and verify the engine outside Project Master, then register only the required "
        "safe-format assets and their SHA-256 digests. This template runs no installer."
    ),
)


CHATTERBOX_PACK_TEMPLATE = EnginePackTemplate(
    id="chatterbox-local",
    engine_id="chatterbox",
    display_name="Chatterbox Local",
    upstream_homepage="https://github.com/resemble-ai/chatterbox",
    version_guidance=(
        "Use the Project Master-pinned upstream source and model revisions."
    ),
    capabilities=(
        EngineCapability.REFERENCE_VOICE,
        EngineCapability.MULTILINGUAL,
        EngineCapability.EXPRESSIVE_CONTROL,
    ),
    asset_requirements=(
        AssetRequirement(
            logical_name="t3_weights",
            description="Pinned multilingual V3 T3 weights.",
            allowed_formats=(ModelAssetFormat.SAFETENSORS,),
        ),
        AssetRequirement(
            logical_name="voice_encoder_checkpoint",
            description=(
                "Pinned upstream voice-encoder checkpoint, loaded with "
                "PyTorch weights_only mode."
            ),
            allowed_formats=(ModelAssetFormat.PYTORCH_CHECKPOINT,),
        ),
        AssetRequirement(
            logical_name="generator_checkpoint",
            description=(
                "Pinned upstream waveform-generator checkpoint, loaded with "
                "PyTorch weights_only mode."
            ),
            allowed_formats=(ModelAssetFormat.PYTORCH_CHECKPOINT,),
        ),
        AssetRequirement(
            logical_name="conditioning_checkpoint",
            description=(
                "Pinned upstream built-in conditioning checkpoint, loaded with "
                "PyTorch weights_only mode."
            ),
            allowed_formats=(ModelAssetFormat.PYTORCH_CHECKPOINT,),
        ),
        AssetRequirement(
            logical_name="tokenizer",
            description="Tokenizer vocabulary and configuration.",
            allowed_formats=(
                ModelAssetFormat.TOKENIZER_JSON,
                ModelAssetFormat.SENTENCEPIECE,
            ),
        ),
        AssetRequirement(
            logical_name="cangjie_map",
            description="Pinned Chinese Cangjie tokenizer mapping.",
            allowed_formats=(ModelAssetFormat.JSON,),
        ),
    ),
    license_notice=(
        "Review and accept the upstream code, model, and voice-use licenses before installation."
    ),
    install_notes=(
        "Project Master's explicit setup step pins both upstream revisions and records a "
        "SHA-256 inventory. Chatterbox's official .pt checkpoints are accepted only for "
        "this pack and are loaded with PyTorch weights_only mode. Normal health and render "
        "operations are offline and never run an installer."
    ),
)


ESPEAK_NG_PACK_TEMPLATE = EnginePackTemplate(
    id="espeak-ng-system",
    engine_id="espeak-ng",
    display_name="eSpeak NG System Voice",
    upstream_homepage="https://github.com/espeak-ng/espeak-ng",
    version_guidance="Use the distribution-maintained eSpeak NG executable.",
    capabilities=(
        EngineCapability.VOICE_DESIGN,
        EngineCapability.MULTILINGUAL,
        EngineCapability.EXPRESSIVE_CONTROL,
        EngineCapability.PRONUNCIATION,
    ),
    asset_requirements=(),
    license_notice=(
        "eSpeak NG is distributed under GPLv3 or later. Review the installed "
        "distribution package and its license before redistribution."
    ),
    install_notes=(
        "Project Master discovers the fixed system executable and never downloads or "
        "executes an installer. Neural voice cloning is not provided by this fallback."
    ),
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def ensure_finite(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Value must be finite.")
    return value
