from __future__ import annotations

from typing import Any

from project_master.team.catalog import OllamaModelCatalog


class FakeCatalogProvider:
    def __init__(
        self,
        models: list[dict[str, Any]],
        shows: dict[str, dict[str, Any] | Exception],
    ) -> None:
        self.models = models
        self.shows = shows
        self.show_calls: list[str] = []

    def list_models(self) -> list[dict[str, Any]]:
        return self.models

    def show_model(self, model: str) -> dict[str, Any]:
        self.show_calls.append(model)
        result = self.shows[model]
        if isinstance(result, Exception):
            raise result
        return result


def test_catalog_deduplicates_physical_models_and_preserves_every_alias() -> None:
    provider = FakeCatalogProvider(
        models=[
            {
                "name": "atlas:latest",
                "digest": "ABC123",
                "size": 12_000,
                "modified_at": "2026-07-20T00:00:00Z",
                "details": {"family": "qwen", "parameter_size": "12B"},
            },
            {
                "model": "atlas:12b",
                "digest": "abc123",
                "size": 12_000,
                "modified_at": "2026-07-21T00:00:00Z",
            },
            {
                "name": "critic:7b",
                "digest": "DEF456",
                "size": 7_000,
                "modified_at": "2026-07-19T00:00:00Z",
            },
        ],
        shows={
            "atlas:12b": {
                "capabilities": ["completion", "tools", "thinking"],
                "details": {
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "12B",
                    "quantization_level": "Q4_K_M",
                    "format": "gguf",
                },
            },
            "critic:7b": {"capabilities": ["completion"]},
        },
    )

    models = OllamaModelCatalog(provider).load()

    assert len(models) == 2
    atlas = next(item for item in models if item.digest == "ABC123")
    assert atlas.physical_id == "digest:abc123"
    assert atlas.tags == ("atlas:12b", "atlas:latest")
    assert atlas.size_bytes == 12_000
    assert atlas.modified_at == "2026-07-21T00:00:00Z"
    assert atlas.capabilities == frozenset({"completion", "tools", "thinking"})
    assert atlas.automatic_eligible is False
    assert atlas.curated_purposes == frozenset()
    assert atlas.details.parameter_size == "12B"
    assert atlas.details.quantization_level == "Q4_K_M"
    assert provider.show_calls == ["atlas:12b", "critic:7b"]


def test_catalog_keeps_digestless_tags_separate_and_survives_inspection_failure() -> None:
    provider = FakeCatalogProvider(
        models=[
            {
                "name": "legacy:a",
                "size": 10,
                "details": {"family": "llama"},
            },
            {
                "name": "legacy:b",
                "size": 11,
                "details": {"family": "llama"},
            },
        ],
        shows={
            "legacy:a": RuntimeError("inspection unavailable"),
            "legacy:b": {"capabilities": []},
        },
    )

    models = OllamaModelCatalog(provider).load()

    assert len(models) == 2
    failed = next(item for item in models if item.primary_tag == "legacy:a")
    assert failed.inspection_error == "RuntimeError: inspection unavailable"
    assert failed.supports_completion is True
    assert models[0].physical_id != models[1].physical_id


def test_catalog_collapses_duplicate_digestless_entries_for_the_same_tag() -> None:
    provider = FakeCatalogProvider(
        models=[
            {"name": "legacy:latest", "size": 10},
            {"name": "legacy:latest", "size": 11},
        ],
        shows={"legacy:latest": {"capabilities": ["completion"]}},
    )

    models = OllamaModelCatalog(provider).load()

    assert len(models) == 1
    assert models[0].tags == ("legacy:latest",)
    assert models[0].size_bytes == 11
    assert provider.show_calls == ["legacy:latest"]


def test_catalog_does_not_treat_embedding_only_model_as_conversational() -> None:
    provider = FakeCatalogProvider(
        models=[
            {
                "name": "embedder:latest",
                "digest": "emb",
                "size": 100,
                "details": {"family": "bert"},
            }
        ],
        shows={"embedder:latest": {"capabilities": ["embedding"]}},
    )

    (model,) = OllamaModelCatalog(provider).load()

    assert model.supports_completion is False


def test_catalog_caches_tags_and_inspection_metadata_until_refresh() -> None:
    provider = FakeCatalogProvider(
        models=[{"name": "lead", "digest": "abc", "size": 1}],
        shows={"lead": {"capabilities": ["completion", "tools"]}},
    )
    catalog = OllamaModelCatalog(provider)

    assert catalog.load() == catalog.load()
    assert provider.show_calls == ["lead"]

    catalog.load(refresh=True)
    assert provider.show_calls == ["lead", "lead"]


def test_catalog_marks_only_exact_curated_manifest_as_automatic() -> None:
    tag = "hf.co/TrevorJS/gemma-4-E4B-it-uncensored-GGUF:Q4_K_M"
    provider = FakeCatalogProvider(
        models=[
            {
                "name": tag,
                "digest": (
                    "bafec5176449e6589e4d3183bb9586e6862fc1e3146ff62a2995ef1e0babdf48"
                ),
                "size": 5_335_286_046,
            }
        ],
        shows={tag: {"capabilities": ["completion", "tools"]}},
    )

    (model,) = OllamaModelCatalog(provider).load()

    assert model.automatic_eligible is True
    assert model.curated_purposes == frozenset({"chat", "team", "dream"})
