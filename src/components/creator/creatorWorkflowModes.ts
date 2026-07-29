import type {
  ComfyWorkflowBinding,
  ComfyWorkflowSummary,
} from "../../lib/projectMasterApi";

export type CreatorIntent = "create" | "edit";

export type CreatorOperation =
  | "text-to-image"
  | "image-to-image"
  | "text-to-video"
  | "image-to-video";

export interface CreatorOperationDefinition {
  id: CreatorOperation;
  label: string;
  description: string;
  output: "image" | "video";
  input: "text" | "image";
}

export const CREATOR_OPERATION_DEFINITIONS: Record<
  CreatorOperation,
  CreatorOperationDefinition
> = {
  "text-to-image": {
    id: "text-to-image",
    label: "Text → Image",
    description: "Create an image from a prompt",
    output: "image",
    input: "text",
  },
  "image-to-image": {
    id: "image-to-image",
    label: "Image → Image",
    description: "Transform a project image with a prompt",
    output: "image",
    input: "image",
  },
  "text-to-video": {
    id: "text-to-video",
    label: "Text → Video",
    description: "Create motion from a prompt",
    output: "video",
    input: "text",
  },
  "image-to-video": {
    id: "image-to-video",
    label: "Image → Video",
    description: "Animate a project image with a prompt",
    output: "video",
    input: "image",
  },
};

const CREATE_OPERATIONS: readonly CreatorOperation[] = [
  "text-to-image",
  "text-to-video",
];

const EDIT_OPERATIONS: readonly CreatorOperation[] = [
  "image-to-image",
  "image-to-video",
];

export function creatorOperationsForIntent(
  intent: CreatorIntent,
): readonly CreatorOperation[] {
  return intent === "create" ? CREATE_OPERATIONS : EDIT_OPERATIONS;
}

export function classifyCreatorWorkflow(
  workflow: ComfyWorkflowSummary,
): CreatorOperation | undefined {
  const acceptsImage = workflow.bindings.some(
    (binding) => binding.valueType === "image_asset",
  );
  if (workflow.purpose === "image") {
    return acceptsImage ? "image-to-image" : "text-to-image";
  }
  if (workflow.purpose === "video") {
    return acceptsImage ? "image-to-video" : "text-to-video";
  }
  return undefined;
}

export function approvedWorkflowsForOperation(
  workflows: readonly ComfyWorkflowSummary[],
  operation: CreatorOperation,
): ComfyWorkflowSummary[] {
  return workflows
    .filter(
      (workflow) =>
        workflow.trustState === "approved" &&
        classifyCreatorWorkflow(workflow) === operation,
    )
    .sort((left, right) => {
      if (left.curatedDefault !== right.curatedDefault) {
        return left.curatedDefault ? -1 : 1;
      }
      if (left.name !== right.name) return left.name < right.name ? -1 : 1;
      if (left.id === right.id) return 0;
      return left.id < right.id ? -1 : 1;
    });
}

/**
 * The curated workflow that is chosen when the user has not picked one. More than one
 * curated default can exist for a single operation, so the picker needs to say which of
 * them is actually automatic rather than implying all of them are.
 */
export function automaticCreatorWorkflowId(
  workflows: readonly ComfyWorkflowSummary[],
): string {
  return workflows.find((workflow) => workflow.curatedDefault)?.id ?? "";
}

export function selectedCreatorWorkflowId(
  workflows: readonly ComfyWorkflowSummary[],
  currentId: string,
): string {
  const current = workflows.find((workflow) => workflow.id === currentId);
  if (current) return current.id;
  return automaticCreatorWorkflowId(workflows) || workflows[0]?.id || "";
}

export function imageAssetBindings(
  workflow: ComfyWorkflowSummary | undefined,
): ComfyWorkflowBinding[] {
  return (
    workflow?.bindings.filter(
      (binding) => binding.valueType === "image_asset",
    ) ?? []
  );
}

export function creatorPromptBinding(
  workflow: ComfyWorkflowSummary | undefined,
): ComfyWorkflowBinding | undefined {
  if (!workflow) return undefined;
  const stringBindings = workflow.bindings.filter(
    (binding) => binding.valueType === "string",
  );
  return (
    stringBindings.find(
      (binding) => binding.id.toLocaleLowerCase() === "prompt",
    ) ??
    stringBindings.find((binding) => {
      const id = binding.id.toLocaleLowerCase();
      const description = binding.description.toLocaleLowerCase();
      return (
        !id.includes("negative") &&
        (id.includes("positive") || description.includes("positive prompt"))
      );
    }) ??
    stringBindings.find(
      (binding) => !binding.id.toLocaleLowerCase().includes("negative"),
    )
  );
}

export function initialCreatorJobValues(
  workflow: ComfyWorkflowSummary | undefined,
): Record<string, unknown> {
  if (!workflow) return {};
  return Object.fromEntries(
    workflow.bindings.map((binding) => [
      binding.id,
      binding.defaultValue ??
        (binding.valueType === "boolean" ? false : ""),
    ]),
  );
}

export function coerceCreatorBindingValue(
  binding: ComfyWorkflowBinding,
  raw: string | boolean,
): unknown {
  if (binding.valueType === "boolean") return Boolean(raw);
  if (binding.valueType === "enum") {
    if (raw === "") return "";
    return binding.choices.find(
      (choice) =>
        String(choice) === String(raw) &&
        (typeof choice !== "boolean" || String(choice) === raw),
    );
  }
  if (raw === "") return "";
  if (binding.valueType === "integer") {
    return Number.parseInt(String(raw), 10);
  }
  if (binding.valueType === "number") {
    return Number.parseFloat(String(raw));
  }
  return raw;
}

function valueIsMissing(value: unknown): boolean {
  return value === "" || value === undefined || value === null;
}

export function creatorJobValuesValid(
  workflow: ComfyWorkflowSummary | undefined,
  values: Readonly<Record<string, unknown>>,
): boolean {
  if (!workflow) return false;
  return workflow.bindings.every((binding) => {
    const value = values[binding.id];
    if (valueIsMissing(value)) return !binding.required;

    if (binding.valueType === "string") return typeof value === "string";
    if (binding.valueType === "image_asset") {
      return typeof value === "string" && value.length > 0;
    }
    if (binding.valueType === "boolean") return typeof value === "boolean";
    if (binding.valueType === "enum") {
      return binding.choices.some(
        (choice) => choice === value && typeof choice === typeof value,
      );
    }
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      (binding.valueType === "integer" && !Number.isInteger(value))
    ) {
      return false;
    }
    return (
      (binding.minimum === undefined || value >= binding.minimum) &&
      (binding.maximum === undefined || value <= binding.maximum)
    );
  });
}

export function comfyJobStatusIsTerminal(status: string): boolean {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

export function comfyJobStatusShouldAutoPoll(status: string): boolean {
  return [
    "submitting",
    "queued",
    "running",
    "cancel_requested",
  ].includes(status);
}
