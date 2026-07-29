from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol

from project_master.llm.curated import curated_model_purposes
from project_master.team.models import CatalogModel, ModelDetails, _bounded_text


class OllamaCatalogProvider(Protocol):
    def list_models(self) -> list[dict[str, Any]]:
        """Return entries from Ollama's /api/tags endpoint."""

    def show_model(self, model: str) -> dict[str, Any]:
        """Return one entry from Ollama's /api/show endpoint."""


class OllamaModelCatalog:
    """Build an alias-preserving catalog while inspecting each physical model once."""

    def __init__(
        self,
        provider: OllamaCatalogProvider,
        max_models: int = 256,
        *,
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        if max_models < 1:
            raise ValueError("max_models must be positive")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative")
        self.provider = provider
        self.max_models = max_models
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: tuple[CatalogModel, ...] | None = None
        self._cached_at = 0.0
        self._lock = threading.Lock()

    def load(self, *, refresh: bool = False) -> tuple[CatalogModel, ...]:
        now = time.monotonic()
        with self._lock:
            if (
                not refresh
                and self._cache is not None
                and now - self._cached_at < self.cache_ttl_seconds
            ):
                return self._cache
            catalog = self._load_uncached()
            self._cache = catalog
            self._cached_at = now
            return catalog

    def _load_uncached(self) -> tuple[CatalogModel, ...]:
        raw_models = self.provider.list_models()[: self.max_models]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in raw_models:
            tag = _model_tag(raw)
            if not tag:
                continue
            digest = _string(raw.get("digest")).strip().casefold()
            # Digest is Ollama's physical manifest identity. Without it, conservatively keep tags
            # separate rather than claiming different tags are the same. Repeated instances of
            # the same tag are still one catalog entry.
            physical_id = f"digest:{digest}" if digest else f"tag:{tag.casefold()}"
            grouped.setdefault(physical_id, []).append(raw)

        catalog: list[CatalogModel] = []
        for physical_id, aliases in grouped.items():
            tags = tuple(sorted({_model_tag(item) for item in aliases}, key=str.casefold))
            representative = tags[0]
            show: dict[str, Any] = {}
            inspection_error: str | None = None
            try:
                raw_show = self.provider.show_model(representative)
                if isinstance(raw_show, dict):
                    show = raw_show
                else:
                    inspection_error = "Model inspection returned an invalid object"
            except Exception as exc:
                error, _truncated = _bounded_text(f"{type(exc).__name__}: {exc}", 500)
                inspection_error = error

            digest = _string(aliases[0].get("digest")) or None
            size_bytes = max((_nonnegative_int(item.get("size")) for item in aliases), default=0)
            modified_at = max(
                (_string(item.get("modified_at")) for item in aliases),
                default="",
            )
            capabilities = _capabilities(show, aliases)
            details = _details(show, aliases)
            curated_purposes = curated_model_purposes(tags, digest)
            catalog.append(
                CatalogModel(
                    physical_id=physical_id,
                    tags=tags,
                    digest=digest,
                    size_bytes=size_bytes,
                    capabilities=frozenset(capabilities),
                    details=details,
                    modified_at=modified_at or None,
                    inspection_error=inspection_error,
                    automatic_eligible=bool(
                        curated_purposes.intersection({"chat", "team", "dream"})
                    ),
                    curated_purposes=curated_purposes,
                )
            )
        return tuple(sorted(catalog, key=lambda item: item.primary_tag.casefold()))


def _model_tag(raw: Mapping[str, Any]) -> str:
    return (_string(raw.get("name")) or _string(raw.get("model"))).strip()


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _capabilities(
    show: Mapping[str, Any], aliases: list[dict[str, Any]]
) -> set[str]:
    values: list[Any] = []
    raw_show = show.get("capabilities")
    if isinstance(raw_show, list):
        values.extend(raw_show)
    for alias in aliases:
        raw_alias = alias.get("capabilities")
        if isinstance(raw_alias, list):
            values.extend(raw_alias)
    return {
        value.strip().casefold()
        for value in values
        if isinstance(value, str) and value.strip()
    }


def _details(show: Mapping[str, Any], aliases: list[dict[str, Any]]) -> ModelDetails:
    sources: list[Mapping[str, Any]] = []
    raw_show_details = show.get("details")
    if isinstance(raw_show_details, Mapping):
        sources.append(raw_show_details)
    for alias in aliases:
        raw_alias_details = alias.get("details")
        if isinstance(raw_alias_details, Mapping):
            sources.append(raw_alias_details)

    def first_string(key: str) -> str:
        for source in sources:
            value = _string(source.get(key))
            if value:
                return value
        return ""

    families: tuple[str, ...] = ()
    for source in sources:
        value = source.get("families")
        if isinstance(value, list):
            families = tuple(item for item in value if isinstance(item, str) and item)
            if families:
                break
    return ModelDetails(
        family=first_string("family"),
        families=families,
        parameter_size=first_string("parameter_size"),
        quantization_level=first_string("quantization_level"),
        format=first_string("format"),
    )
