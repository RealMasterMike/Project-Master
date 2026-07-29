import math

import pytest

from project_master.integrations.comfyui.workflow import (
    WorkflowBinding,
    WorkflowRevision,
    WorkflowValidationError,
)


def api_workflow() -> dict:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": "original prompt"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "seed": 1,
                "steps": 20,
            },
        },
    }


def bindings() -> tuple[WorkflowBinding, ...]:
    return (
        WorkflowBinding(
            id="prompt",
            node_id="2",
            input_name="text",
            value_type="string",
        ),
        WorkflowBinding(
            id="steps",
            node_id="3",
            input_name="steps",
            value_type="integer",
            required=False,
            default_value=30,
            minimum=1,
            maximum=100,
        ),
        WorkflowBinding(
            id="quality",
            node_id="1",
            input_name="ckpt_name",
            value_type="enum",
            required=False,
            default_value="base.safetensors",
            choices=("base.safetensors", "quality.safetensors"),
        ),
    )


def test_import_creates_deterministic_revision_and_rendered_copy() -> None:
    first = WorkflowRevision.import_json("Portrait", api_workflow(), bindings())
    second = WorkflowRevision.import_json("Renamed", {"prompt": api_workflow()}, bindings())

    rendered = first.render({"prompt": "cinematic portrait", "quality": "quality.safetensors"})

    assert first.id == second.id
    assert first.digest == second.digest
    assert rendered["2"]["inputs"]["text"] == "cinematic portrait"
    assert rendered["3"]["inputs"]["steps"] == 30
    assert rendered["1"]["inputs"]["ckpt_name"] == "quality.safetensors"
    assert first.workflow["2"]["inputs"]["text"] == "original prompt"


def test_schema_v2_binds_purpose_into_the_immutable_revision_digest() -> None:
    general = WorkflowRevision.import_json(
        "General",
        api_workflow(),
        bindings(),
        purpose="general",
    )
    video = WorkflowRevision.import_json(
        "Video",
        api_workflow(),
        bindings(),
        purpose="video",
    )

    assert general.schema_version == 2
    assert video.schema_version == 2
    assert general.id == "comfy-wf-9558f9f8daa8ddaabb1609e1"
    assert video.id == "comfy-wf-25c50c8a28de8327ce2f67cb"
    assert general.id != video.id
    assert video.purpose == "video"
    assert WorkflowRevision.model_validate_json(video.model_dump_json()) == video


def test_schema_v1_keeps_its_exact_legacy_digest_and_requires_general_purpose() -> None:
    legacy = {
        "schema_version": 1,
        "id": "comfy-wf-9ac7a06b59d8f528f30cf687",
        "name": "Legacy portrait",
        "digest": "9ac7a06b59d8f528f30cf687445bb4d97e16483d42a205d1a1a575e1e72d3244",
        "created_at": "2026-07-27T00:00:00Z",
        "workflow": api_workflow(),
        "bindings": [binding.model_dump(mode="json") for binding in bindings()],
    }

    restored = WorkflowRevision.model_validate(legacy)

    assert restored.schema_version == 1
    assert restored.purpose == "general"
    assert restored.digest == legacy["digest"]

    with pytest.raises(ValueError, match="must use the general purpose"):
        WorkflowRevision.model_validate({**legacy, "purpose": "video"})


def test_revision_detects_nested_content_mutation_before_rendering() -> None:
    revision = WorkflowRevision.import_json("Portrait", api_workflow(), bindings())
    revision.workflow["2"]["inputs"]["text"] = "mutated outside revision API"

    with pytest.raises(WorkflowValidationError, match="no longer matches"):
        revision.render({"prompt": "safe prompt"})


def test_render_requires_known_typed_values_and_enforces_range() -> None:
    revision = WorkflowRevision.import_json("Portrait", api_workflow(), bindings())

    with pytest.raises(WorkflowValidationError, match="was not provided"):
        revision.render()
    with pytest.raises(WorkflowValidationError, match="Unknown"):
        revision.render({"prompt": "test", "surprise": True})
    with pytest.raises(WorkflowValidationError, match="expected integer"):
        revision.render({"prompt": "test", "steps": True})
    with pytest.raises(WorkflowValidationError, match="at most"):
        revision.render({"prompt": "test", "steps": 101})
    with pytest.raises(WorkflowValidationError, match="expected enum"):
        revision.render({"prompt": "test", "quality": "unknown.safetensors"})


def test_image_asset_binding_only_accepts_project_media_ids() -> None:
    asset_id = f"media-asset-{'a' * 32}"
    revision = WorkflowRevision.import_json(
        "Image input",
        {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "placeholder.png"},
            }
        },
        (
            WorkflowBinding(
                id="source_image",
                node_id="1",
                input_name="image",
                value_type="image_asset",
            ),
        ),
    )

    rendered = revision.render({"source_image": asset_id})

    assert rendered["1"]["inputs"]["image"] == asset_id
    with pytest.raises(WorkflowValidationError, match="expected image_asset"):
        revision.render({"source_image": "/tmp/untrusted.png"})


def test_image_asset_binding_must_target_load_image_image_input() -> None:
    wrong_node = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "placeholder"},
        }
    }
    binding = WorkflowBinding(
        id="source_image",
        node_id="1",
        input_name="text",
        value_type="image_asset",
    )

    with pytest.raises(WorkflowValidationError, match=r"LoadImage\.image"):
        WorkflowRevision.import_json("Wrong image target", wrong_node, (binding,))


def test_image_asset_bindings_cannot_be_optional_or_have_defaults() -> None:
    with pytest.raises(ValueError, match="must be required"):
        WorkflowBinding(
            id="source_image",
            node_id="1",
            input_name="image",
            value_type="image_asset",
            required=False,
        )
    with pytest.raises(ValueError, match="cannot declare a default"):
        WorkflowBinding(
            id="source_image",
            node_id="1",
            input_name="image",
            value_type="image_asset",
            default_value=f"media-asset-{'a' * 32}",
        )


def test_static_validation_rejects_editor_format_missing_links_and_cycles() -> None:
    with pytest.raises(WorkflowValidationError, match="editor workflow"):
        WorkflowRevision.import_json("Editor export", {"nodes": []})

    missing = api_workflow()
    missing["3"]["inputs"]["model"] = ["404", 0]
    with pytest.raises(WorkflowValidationError, match="missing node"):
        WorkflowRevision.import_json("Missing", missing)

    cycle = {
        "1": {"class_type": "First", "inputs": {"source": ["2", 0]}},
        "2": {"class_type": "Second", "inputs": {"source": ["1", 0]}},
    }
    with pytest.raises(WorkflowValidationError, match="dependency cycle"):
        WorkflowRevision.import_json("Cycle", cycle)


def test_static_validation_rejects_invalid_binding_targets_and_duplicates() -> None:
    duplicate_target = (
        WorkflowBinding(id="first", node_id="2", input_name="text", value_type="string"),
        WorkflowBinding(id="second", node_id="2", input_name="text", value_type="string"),
    )
    with pytest.raises(WorkflowValidationError, match="Multiple bindings"):
        WorkflowRevision.import_json("Duplicate", api_workflow(), duplicate_target)

    missing_target = (
        WorkflowBinding(
            id="missing", node_id="2", input_name="does_not_exist", value_type="string"
        ),
    )
    with pytest.raises(WorkflowValidationError, match="missing input"):
        WorkflowRevision.import_json("Missing target", api_workflow(), missing_target)


def test_import_rejects_non_json_and_non_finite_values() -> None:
    with pytest.raises(WorkflowValidationError, match="valid JSON"):
        WorkflowRevision.import_json("Bad JSON", "{")
    invalid = api_workflow()
    invalid["3"]["inputs"]["cfg"] = math.inf
    with pytest.raises(WorkflowValidationError, match="non-JSON or non-finite"):
        WorkflowRevision.import_json("Infinite", invalid)
