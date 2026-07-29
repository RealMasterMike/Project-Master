from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CuratedModelIdentity:
    tag: str
    manifest_digest: str
    purposes: frozenset[str]
    publisher_url: str
    evidence_label: str


CURATED_MODEL_IDENTITIES = (
    CuratedModelIdentity(
        tag="hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M",
        manifest_digest=(
            "bafec5176449e6589e4d3183bb9586e6862fc1e3146ff62a2995ef1e0babdf48"
        ),
        purposes=frozenset({"chat", "team", "dream"}),
        publisher_url=(
            "https://huggingface.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF"
        ),
        evidence_label="Publisher-documented uncensored; physical chat/tool smoke passed.",
    ),
    CuratedModelIdentity(
        tag="lukey03/qwen3.5-9b-abliterated-vision:latest",
        manifest_digest=(
            "b6ae7e073f77feef97010fd2e82a9480b400e48ea5afa035d9c86af0910650df"
        ),
        purposes=frozenset({"chat", "vision", "team", "dream"}),
        publisher_url=(
            "https://huggingface.co/lukey03/Qwen3.5-9B-abliterated"
        ),
        evidence_label=(
            "Publisher-documented abliterated vision; physical Creator image smoke passed."
        ),
    ),
)


def curated_model_purposes(
    tags: tuple[str, ...],
    manifest_digest: str | None,
) -> frozenset[str]:
    digest = (manifest_digest or "").removeprefix("sha256:").casefold()
    normalized_tags = {tag.casefold() for tag in tags}
    for identity in CURATED_MODEL_IDENTITIES:
        if (
            identity.tag.casefold() in normalized_tags
            and identity.manifest_digest.casefold() == digest
        ):
            return identity.purposes
    return frozenset()


__all__ = [
    "CURATED_MODEL_IDENTITIES",
    "CuratedModelIdentity",
    "curated_model_purposes",
]
