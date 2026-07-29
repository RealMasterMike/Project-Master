from project_master.llm.curated import (
    CURATED_MODEL_IDENTITIES,
    curated_model_purposes,
)


def test_curated_models_require_exact_tag_and_manifest_digest() -> None:
    identity = CURATED_MODEL_IDENTITIES[1]

    assert curated_model_purposes(
        (identity.tag.upper(),),
        identity.manifest_digest.upper(),
    ) == frozenset({"chat", "vision", "team", "dream"})
    assert curated_model_purposes(
        (identity.tag,),
        "0" * 64,
    ) == frozenset()
    assert curated_model_purposes(
        ("someone-else/uncensored:latest",),
        identity.manifest_digest,
    ) == frozenset()


def test_curated_registry_preserves_publisher_evidence() -> None:
    assert len(CURATED_MODEL_IDENTITIES) == 2
    assert all(item.publisher_url.startswith("https://") for item in CURATED_MODEL_IDENTITIES)
    assert all(item.evidence_label for item in CURATED_MODEL_IDENTITIES)
