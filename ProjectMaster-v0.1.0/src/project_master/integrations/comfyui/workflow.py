from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BindingType = Literal["string", "integer", "number", "boolean", "enum"]


class WorkflowValidationError(ValueError):
    def __init__(self, *issues: str) -> None:
        self.issues = tuple(issues) or ("Workflow is invalid.",)
        super().__init__("; ".join(self.issues))


class WorkflowBinding(BaseModel):
    """A typed, user-facing value mapped to one ComfyUI node input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    node_id: str = Field(min_length=1, max_length=128)
    input_name: str = Field(min_length=1, max_length=200)
    value_type: BindingType
    required: bool = True
    default_value: Any | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] = ()
    description: str = Field(default="", max_length=500)

    @field_validator("node_id", "input_name")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("Workflow binding targets cannot contain control characters.")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> WorkflowBinding:
        if self.minimum is not None or self.maximum is not None:
            if self.value_type not in {"integer", "number"}:
                raise ValueError("Only numeric bindings may declare minimum or maximum.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Workflow binding minimum cannot exceed maximum.")
        if self.value_type == "enum":
            if not self.choices:
                raise ValueError("Enum bindings require at least one choice.")
            if any(
                not isinstance(choice, (str, int, float, bool))
                or (isinstance(choice, float) and not math.isfinite(choice))
                for choice in self.choices
            ):
                raise ValueError("Enum binding choices must be finite JSON scalar values.")
            if len({_canonical_json(choice) for choice in self.choices}) != len(self.choices):
                raise ValueError("Enum binding choices must be unique.")
        elif self.choices:
            raise ValueError("Only enum bindings may declare choices.")
        if not self.required and self.default_value is None:
            raise ValueError("Optional bindings require a non-null default_value.")
        if self.default_value is not None:
            self.validate_value(self.default_value)
        return self

    def validate_value(self, value: Any) -> Any:
        if self.value_type == "string":
            valid = isinstance(value, str)
        elif self.value_type == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif self.value_type == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            if valid and isinstance(value, float):
                valid = math.isfinite(value)
        elif self.value_type == "boolean":
            valid = isinstance(value, bool)
        else:
            valid = any(value == choice and type(value) is type(choice) for choice in self.choices)
        if not valid:
            raise WorkflowValidationError(
                f"Binding {self.id!r} expected {self.value_type}, got {type(value).__name__}."
            )
        if self.minimum is not None and value < self.minimum:
            raise WorkflowValidationError(f"Binding {self.id!r} must be at least {self.minimum}.")
        if self.maximum is not None and value > self.maximum:
            raise WorkflowValidationError(f"Binding {self.id!r} must be at most {self.maximum}.")
        return deepcopy(value)


class WorkflowRevision(BaseModel):
    """An immutable imported ComfyUI API workflow and its typed binding manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str
    name: str
    digest: str
    created_at: datetime
    workflow: dict[str, dict[str, Any]]
    bindings: tuple[WorkflowBinding, ...] = ()

    @model_validator(mode="after")
    def validate_revision_integrity(self) -> WorkflowRevision:
        expected = self._content_digest()
        if self.digest != expected or self.id != f"comfy-wf-{expected[:24]}":
            raise ValueError("Workflow revision digest does not match its content.")
        if self.created_at.tzinfo is None:
            raise ValueError("Workflow revision created_at must include a timezone.")
        return self

    @classmethod
    def import_json(
        cls,
        name: str,
        source: str | bytes | Mapping[str, Any],
        bindings: tuple[WorkflowBinding, ...] | list[WorkflowBinding] = (),
        *,
        created_at: datetime | None = None,
    ) -> WorkflowRevision:
        if not name.strip():
            raise WorkflowValidationError("Workflow name cannot be empty.")
        document = _load_document(source)
        workflow = _extract_api_workflow(document)
        normalized = _validate_workflow(workflow)
        binding_tuple = tuple(bindings)
        _validate_bindings(normalized, binding_tuple)
        manifest = {
            "workflow": normalized,
            "bindings": [binding.model_dump(mode="json") for binding in binding_tuple],
        }
        digest = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
        return cls(
            id=f"comfy-wf-{digest[:24]}",
            name=name.strip(),
            digest=digest,
            created_at=created_at or datetime.now(UTC),
            workflow=deepcopy(normalized),
            bindings=binding_tuple,
        )

    def render(self, values: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        if self.digest != self._content_digest():
            raise WorkflowValidationError("Workflow revision content no longer matches its digest.")
        supplied = dict(values or {})
        known = {binding.id for binding in self.bindings}
        unknown = sorted(set(supplied) - known)
        if unknown:
            raise WorkflowValidationError(f"Unknown workflow binding values: {', '.join(unknown)}.")

        rendered = deepcopy(self.workflow)
        for binding in self.bindings:
            if binding.id in supplied:
                value = binding.validate_value(supplied[binding.id])
            elif binding.default_value is not None:
                value = binding.validate_value(binding.default_value)
            elif binding.required:
                raise WorkflowValidationError(
                    f"Required workflow binding {binding.id!r} was not provided."
                )
            else:  # guarded by WorkflowBinding validation
                continue
            rendered[binding.node_id]["inputs"][binding.input_name] = value
        return rendered

    def _content_digest(self) -> str:
        manifest = {
            "workflow": self.workflow,
            "bindings": [binding.model_dump(mode="json") for binding in self.bindings],
        }
        return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def _load_document(source: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return deepcopy(dict(source))
    try:
        loaded = json.loads(source)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowValidationError("Workflow import is not valid JSON.") from exc
    if not isinstance(loaded, dict):
        raise WorkflowValidationError("Workflow import must be a JSON object.")
    return loaded


def _extract_api_workflow(document: Mapping[str, Any]) -> Mapping[str, Any]:
    if "prompt" in document and isinstance(document["prompt"], Mapping):
        return document["prompt"]
    if isinstance(document.get("nodes"), list):
        raise WorkflowValidationError(
            "This is a ComfyUI editor workflow. Export the workflow in API format first."
        )
    return document


def _validate_workflow(workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not workflow:
        raise WorkflowValidationError("Workflow must contain at least one node.")
    issues: list[str] = []
    normalized: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, set[str]] = {}

    for node_id, raw_node in workflow.items():
        if not isinstance(node_id, str) or not node_id:
            issues.append("Every workflow node ID must be a non-empty string.")
            continue
        if not isinstance(raw_node, Mapping):
            issues.append(f"Workflow node {node_id!r} must be an object.")
            continue
        class_type = raw_node.get("class_type")
        inputs = raw_node.get("inputs")
        if not isinstance(class_type, str) or not class_type.strip():
            issues.append(f"Workflow node {node_id!r} requires a class_type.")
        if not isinstance(inputs, Mapping):
            issues.append(f"Workflow node {node_id!r} requires an inputs object.")
            continue
        try:
            _canonical_json(raw_node)
        except (TypeError, ValueError):
            issues.append(f"Workflow node {node_id!r} contains a non-JSON or non-finite value.")
            continue
        normalized[node_id] = deepcopy(dict(raw_node))
        dependencies[node_id] = set()

    known_nodes = set(normalized)
    for node_id, node in normalized.items():
        for source_id, output_index in _connections(node["inputs"]):
            if source_id not in known_nodes:
                issues.append(f"Workflow node {node_id!r} references missing node {source_id!r}.")
            elif output_index < 0:
                issues.append(
                    f"Workflow node {node_id!r} has a negative output index for {source_id!r}."
                )
            else:
                dependencies[node_id].add(source_id)

    cycle = _find_cycle(dependencies)
    if cycle:
        issues.append(f"Workflow contains a dependency cycle: {' -> '.join(cycle)}.")
    if issues:
        raise WorkflowValidationError(*issues)
    return normalized


def _connections(value: Any) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    ):
        return [(value[0], value[1])]
    if isinstance(value, Mapping):
        for nested in value.values():
            found.extend(_connections(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_connections(nested))
    return found


def _find_cycle(graph: Mapping[str, set[str]]) -> tuple[str, ...]:
    visited: set[str] = set()
    active: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> tuple[str, ...]:
        if node in active:
            start = path.index(node)
            return tuple(path[start:] + [node])
        if node in visited:
            return ()
        visited.add(node)
        active.add(node)
        path.append(node)
        for dependency in graph.get(node, set()):
            cycle = visit(dependency)
            if cycle:
                return cycle
        path.pop()
        active.remove(node)
        return ()

    for node_id in graph:
        cycle = visit(node_id)
        if cycle:
            return cycle
    return ()


def _validate_bindings(
    workflow: Mapping[str, Mapping[str, Any]], bindings: tuple[WorkflowBinding, ...]
) -> None:
    issues: list[str] = []
    ids: set[str] = set()
    targets: set[tuple[str, str]] = set()
    for binding in bindings:
        if binding.id in ids:
            issues.append(f"Duplicate workflow binding ID {binding.id!r}.")
        ids.add(binding.id)
        target = (binding.node_id, binding.input_name)
        if target in targets:
            issues.append(
                f"Multiple bindings target node {binding.node_id!r} input {binding.input_name!r}."
            )
        targets.add(target)
        node = workflow.get(binding.node_id)
        if node is None:
            issues.append(
                f"Binding {binding.id!r} targets missing workflow node {binding.node_id!r}."
            )
        elif binding.input_name not in node["inputs"]:
            issues.append(
                f"Binding {binding.id!r} targets missing input {binding.input_name!r} "
                f"on node {binding.node_id!r}."
            )
    if issues:
        raise WorkflowValidationError(*issues)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
