import { describe, expect, it } from "vitest";

import type {
  ComfyWorkflowBinding,
  ComfyWorkflowSummary,
} from "../../lib/projectMasterApi";
import {
  approvedWorkflowsForOperation,
  automaticCreatorWorkflowId,
  classifyCreatorWorkflow,
  coerceCreatorBindingValue,
  comfyJobStatusIsTerminal,
  comfyJobStatusShouldAutoPoll,
  creatorJobValuesValid,
  creatorOperationsForIntent,
  creatorPromptBinding,
  initialCreatorJobValues,
  selectedCreatorWorkflowId,
} from "./creatorWorkflowModes";

function binding(
  id: string,
  valueType: ComfyWorkflowBinding["valueType"],
  update: Partial<ComfyWorkflowBinding> = {},
): ComfyWorkflowBinding {
  return {
    id,
    nodeId: "1",
    inputName: id,
    valueType,
    required: true,
    choices: [],
    description: "",
    ...update,
  };
}

function workflow(
  id: string,
  purpose: ComfyWorkflowSummary["purpose"],
  bindings: ComfyWorkflowBinding[],
  trustState = "approved",
  curatedDefault = false,
): ComfyWorkflowSummary {
  return {
    id,
    name: id,
    digest: id.padEnd(64, "0"),
    trustState,
    createdAt: "2026-07-28T12:00:00Z",
    purpose,
    curatedDefault,
    bindings,
  };
}

describe("Creator workflow modes", () => {
  it("classifies output purpose and verified image input into four operations", () => {
    expect(
      classifyCreatorWorkflow(
        workflow("t2i", "image", [binding("prompt", "string")]),
      ),
    ).toBe("text-to-image");
    expect(
      classifyCreatorWorkflow(
        workflow("i2i", "image", [
          binding("prompt", "string"),
          binding("source", "image_asset"),
        ]),
      ),
    ).toBe("image-to-image");
    expect(
      classifyCreatorWorkflow(
        workflow("t2v", "video", [binding("prompt", "string")]),
      ),
    ).toBe("text-to-video");
    expect(
      classifyCreatorWorkflow(
        workflow("i2v", "video", [
          binding("prompt", "string"),
          binding("source", "image_asset"),
        ]),
      ),
    ).toBe("image-to-video");
    expect(
      classifyCreatorWorkflow(workflow("audio", "audio", [])),
    ).toBeUndefined();
    expect(
      classifyCreatorWorkflow(workflow("general", "general", [])),
    ).toBeUndefined();
  });

  it("filters approved revisions and deterministically prefers curated defaults", () => {
    expect(creatorOperationsForIntent("create")).toEqual([
      "text-to-image",
      "text-to-video",
    ]);
    expect(creatorOperationsForIntent("edit")).toEqual([
      "image-to-image",
      "image-to-video",
    ]);
    const workflows = [
      workflow("manual-z", "image", [binding("prompt", "string")]),
      workflow(
        "pending",
        "image",
        [binding("prompt", "string")],
        "pending",
      ),
      workflow(
        "curated-z",
        "image",
        [binding("prompt", "string")],
        "approved",
        true,
      ),
      workflow(
        "curated-a",
        "image",
        [binding("prompt", "string")],
        "approved",
        true,
      ),
      workflow("manual-a", "image", [binding("prompt", "string")]),
      workflow("video", "video", [binding("prompt", "string")]),
    ];
    const approved = approvedWorkflowsForOperation(
      workflows,
      "text-to-image",
    );
    expect(approved.map((item) => item.id)).toEqual([
      "curated-a",
      "curated-z",
      "manual-a",
      "manual-z",
    ]);
    expect(selectedCreatorWorkflowId(approved, "")).toBe("curated-a");
    expect(selectedCreatorWorkflowId(approved, "manual-z")).toBe("manual-z");
  });

  it("chooses an explicit positive prompt binding before other text controls", () => {
    const selected = workflow("prompt-order", "image", [
      binding("negative_prompt", "string"),
      binding("style", "string"),
      binding("positive_prompt", "string", {
        description: "Positive prompt",
      }),
    ]);
    expect(creatorPromptBinding(selected)?.id).toBe("positive_prompt");
  });

  it("initializes, coerces, and validates safe job values", () => {
    const selected = workflow("bounded", "image", [
      binding("prompt", "string"),
      binding("source", "image_asset"),
      binding("seed", "integer", {
        defaultValue: 7,
        minimum: 0,
        maximum: 10,
      }),
      binding("enabled", "boolean"),
      binding("style", "enum", { choices: ["photo", "drawing"] }),
    ]);
    const initial = initialCreatorJobValues(selected);
    expect(initial).toEqual({
      prompt: "",
      source: "",
      seed: 7,
      enabled: false,
      style: "",
    });
    expect(
      coerceCreatorBindingValue(selected.bindings[2], ""),
    ).toBe("");
    expect(
      coerceCreatorBindingValue(selected.bindings[2], "9"),
    ).toBe(9);
    expect(
      coerceCreatorBindingValue(selected.bindings[4], "photo"),
    ).toBe("photo");
    expect(
      creatorJobValuesValid(selected, {
        ...initial,
        prompt: "Restyle this",
        source: "media-asset-0123456789abcdef0123456789abcdef",
        style: "photo",
      }),
    ).toBe(true);
    expect(
      creatorJobValuesValid(selected, {
        ...initial,
        prompt: "Restyle this",
        source: "media-asset-0123456789abcdef0123456789abcdef",
        seed: 11,
        style: "photo",
      }),
    ).toBe(false);
  });

  it("identifies terminal and safely auto-pollable job states", () => {
    expect(comfyJobStatusIsTerminal("succeeded")).toBe(true);
    expect(comfyJobStatusIsTerminal("failed")).toBe(true);
    expect(comfyJobStatusIsTerminal("cancelled")).toBe(true);
    expect(comfyJobStatusIsTerminal("orphaned")).toBe(false);
    expect(comfyJobStatusShouldAutoPoll("queued")).toBe(true);
    expect(comfyJobStatusShouldAutoPoll("running")).toBe(true);
    expect(comfyJobStatusShouldAutoPoll("orphaned")).toBe(false);
  });
});

describe("automatic curated selection", () => {
  it("names only the first curated workflow as the automatic one", () => {
    // Two curated defaults now exist per image operation (Chroma and RealVisXL). The
    // picker must not imply both are automatic.
    const workflows = approvedWorkflowsForOperation(
      [
        workflow("RealVisXL", "image", [binding("prompt", "string")], "approved", true),
        workflow("Chroma1-Flash", "image", [binding("prompt", "string")], "approved", true),
        workflow("SomeManual", "image", [binding("prompt", "string")], "approved", false),
      ],
      "text-to-image",
    );

    expect(workflows.map((item) => item.id)).toEqual([
      "Chroma1-Flash",
      "RealVisXL",
      "SomeManual",
    ]);
    expect(automaticCreatorWorkflowId(workflows)).toBe("Chroma1-Flash");
  });

  it("returns no automatic workflow when none are curated", () => {
    const workflows = approvedWorkflowsForOperation(
      [workflow("OnlyManual", "image", [binding("prompt", "string")], "approved", false)],
      "text-to-image",
    );

    expect(automaticCreatorWorkflowId(workflows)).toBe("");
  });
});
