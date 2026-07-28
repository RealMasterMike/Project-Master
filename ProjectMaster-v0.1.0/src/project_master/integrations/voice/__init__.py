"""Optional, engine-agnostic Voice Studio contracts."""

from project_master.integrations.voice.artifacts import (
    InMemoryVoiceArtifactStore,
    VoiceArtifact,
    VoiceArtifactProvenance,
    VoiceArtifactStore,
)
from project_master.integrations.voice.cache import (
    ChunkCache,
    InMemoryChunkCache,
    VoiceChunkPlan,
    build_chunk_plan,
)
from project_master.integrations.voice.chatterbox import (
    ChatterboxWorkerAdapter,
    chatterbox_python,
    discover_chatterbox_pack,
)
from project_master.integrations.voice.engine import (
    CancellationAck,
    EngineAdapter,
    EngineHealth,
    EngineRecovery,
    EngineRenderRequest,
    RenderedAudio,
)
from project_master.integrations.voice.espeak import (
    EspeakNgAdapter,
    discover_espeak_pack,
)
from project_master.integrations.voice.governor import GovernorVoiceLeaseProvider
from project_master.integrations.voice.jobs import (
    InMemoryRenderJobRepository,
    RenderChunkStatus,
    RenderJob,
    RenderJobRepository,
    RenderJobStatus,
)
from project_master.integrations.voice.manifests import (
    CHATTERBOX_PACK_TEMPLATE,
    ESPEAK_NG_PACK_TEMPLATE,
    QWEN3_TTS_PACK_TEMPLATE,
    EngineCapability,
    EnginePackTemplate,
    InstalledEnginePack,
    ModelAsset,
    ModelAssetFormat,
)
from project_master.integrations.voice.persistence import (
    FilesystemVoiceArtifactStore,
    SQLiteVoiceStore,
)
from project_master.integrations.voice.profiles import (
    ConsentRecord,
    ConsentScope,
    RenderPurpose,
    RightsBasis,
    VoiceProfile,
    VoiceReference,
)
from project_master.integrations.voice.projects import (
    PronunciationEntry,
    RenderSettings,
    ScriptBlock,
    VoiceProject,
)
from project_master.integrations.voice.resources import (
    ResourceLease,
    ResourceLeaseProvider,
    VoiceResourceRequest,
)
from project_master.integrations.voice.service import VoiceStudioService

__all__ = [
    "ChatterboxWorkerAdapter",
    "CHATTERBOX_PACK_TEMPLATE",
    "QWEN3_TTS_PACK_TEMPLATE",
    "CancellationAck",
    "ChunkCache",
    "ConsentRecord",
    "ConsentScope",
    "EngineAdapter",
    "EngineCapability",
    "EngineHealth",
    "EngineRecovery",
    "EnginePackTemplate",
    "EngineRenderRequest",
    "EspeakNgAdapter",
    "ESPEAK_NG_PACK_TEMPLATE",
    "FilesystemVoiceArtifactStore",
    "GovernorVoiceLeaseProvider",
    "InMemoryChunkCache",
    "InMemoryRenderJobRepository",
    "InMemoryVoiceArtifactStore",
    "InstalledEnginePack",
    "ModelAsset",
    "ModelAssetFormat",
    "PronunciationEntry",
    "RenderJob",
    "RenderChunkStatus",
    "RenderJobRepository",
    "RenderJobStatus",
    "RenderPurpose",
    "RenderSettings",
    "RenderedAudio",
    "ResourceLease",
    "ResourceLeaseProvider",
    "RightsBasis",
    "ScriptBlock",
    "SQLiteVoiceStore",
    "VoiceArtifact",
    "VoiceArtifactProvenance",
    "VoiceArtifactStore",
    "VoiceChunkPlan",
    "VoiceProfile",
    "VoiceProject",
    "VoiceReference",
    "VoiceResourceRequest",
    "VoiceStudioService",
    "build_chunk_plan",
    "chatterbox_python",
    "discover_chatterbox_pack",
    "discover_espeak_pack",
]
