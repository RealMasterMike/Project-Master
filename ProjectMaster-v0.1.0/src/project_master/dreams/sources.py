from __future__ import annotations

from collections.abc import Sequence

from project_master.dreams.models import DreamRecipe
from project_master.dreams.snapshots import DreamSource, SourceKind


def unsupported_scheduled_source_scopes(
    scopes: Sequence[str],
) -> tuple[str, ...]:
    """Return scopes that the durable scheduled-source resolver cannot enumerate."""
    supported_kinds = {SourceKind.PROJECT.value, SourceKind.MEMORY.value}
    unsupported: list[str] = []
    for raw_scope in scopes:
        scope = raw_scope.strip()
        if scope in {"*", "all"}:
            continue
        if scope in supported_kinds:
            continue
        if scope.startswith("kind:") and scope.removeprefix("kind:") in supported_kinds:
            continue
        matched = False
        for kind in supported_kinds:
            prefix = f"{kind}:"
            if scope.startswith(prefix) and bool(scope.removeprefix(prefix)):
                matched = True
                break
        if not matched:
            unsupported.append(scope)
    return tuple(unsupported)


def enforce_recipe_source_scopes(
    recipe: DreamRecipe,
    sources: Sequence[DreamSource],
) -> None:
    """Reject explicitly supplied sources outside a recipe's declared scope."""
    if not recipe.source_scopes:
        return
    rejected = [
        source.source_id
        for source in sources
        if not source_matches_scopes(source, recipe.source_scopes)
    ]
    if rejected:
        rendered = ", ".join(sorted(rejected))
        raise ValueError(
            f"dream sources are outside recipe source_scopes: {rendered}"
        )


def source_matches_scopes(
    source: DreamSource,
    scopes: Sequence[str],
) -> bool:
    return any(_source_matches_scope(source, scope.strip()) for scope in scopes)


def _source_matches_scope(source: DreamSource, scope: str) -> bool:
    if scope in {"*", "all"}:
        return True
    if scope in {kind.value for kind in SourceKind}:
        return source.kind.value == scope
    if scope.startswith("kind:"):
        return source.kind.value == scope.removeprefix("kind:")
    if scope.startswith("source:"):
        return source.source_id == scope.removeprefix("source:")
    if scope.startswith("locator:"):
        return source.locator.startswith(scope.removeprefix("locator:"))
    for kind in (
        SourceKind.PROJECT,
        SourceKind.MEMORY,
        SourceKind.CONVERSATION,
        SourceKind.DECISION,
        SourceKind.ARTIFACT,
    ):
        prefix = f"{kind.value}:"
        if not scope.startswith(prefix) or source.kind is not kind:
            continue
        scope_id = scope.removeprefix(prefix)
        if scope_id == "*":
            return True
        locator_root = f"{kind.value}://{scope_id}"
        return source.locator == locator_root or source.locator.startswith(
            (f"{locator_root}/", f"{locator_root}#", f"{locator_root}:")
        )
    return False
