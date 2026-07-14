export const LAYOUT_SCHEMA_VERSION = 1 as const;
export const LAYOUT_STORAGE_KEY = "project-master.layout.v1";
export const SAVED_LAYOUTS_STORAGE_KEY = "project-master.saved-layouts.v1";

export const PANEL_IDS = ["chat_panel", "customize_panel"] as const;
export type PanelId = (typeof PANEL_IDS)[number];
export type LayoutContainer = "main" | "sidebar_tabs";

export interface PanelLayout {
  visible: boolean;
  collapsed: boolean;
  order: number;
  container: LayoutContainer;
  width: number;
}

export interface LayoutDocument {
  schemaVersion: typeof LAYOUT_SCHEMA_VERSION;
  revision: number;
  panels: Record<PanelId, PanelLayout>;
}

export type LayoutOperation =
  | { operation: "set_visibility"; target: PanelId; value: boolean }
  | { operation: "set_collapsed"; target: PanelId; value: boolean }
  | { operation: "set_order"; target: PanelId; value: number }
  | { operation: "set_tab_group"; target: PanelId; value: LayoutContainer }
  | { operation: "set_width"; target: PanelId; value: number };

export interface LayoutCommand {
  schemaVersion: typeof LAYOUT_SCHEMA_VERSION;
  baseRevision: number;
  operations: LayoutOperation[];
}

export interface SavedLayout {
  id: string;
  name: string;
  createdAt: string;
  layout: LayoutDocument;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface PanelDefinition {
  canHide: boolean;
  canCollapse: boolean;
  allowedContainers: readonly LayoutContainer[];
  minWidth: number;
  maxWidth: number;
}

const PANEL_DEFINITIONS: Record<PanelId, PanelDefinition> = {
  chat_panel: {
    canHide: false,
    canCollapse: false,
    allowedContainers: ["main"],
    minWidth: 55,
    maxWidth: 100,
  },
  customize_panel: {
    canHide: true,
    canCollapse: true,
    allowedContainers: ["sidebar_tabs"],
    minWidth: 260,
    maxWidth: 480,
  },
};

const DEFAULT_PANELS: Record<PanelId, PanelLayout> = {
  chat_panel: {
    visible: true,
    collapsed: false,
    order: 0,
    container: "main",
    width: 100,
  },
  customize_panel: {
    visible: false,
    collapsed: false,
    order: 1,
    container: "sidebar_tabs",
    width: 320,
  },
};

export class LayoutValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LayoutValidationError";
  }
}

export class LayoutConflictError extends LayoutValidationError {
  constructor(expected: number, received: number) {
    super(`Layout revision conflict: expected ${expected}, received ${received}.`);
    this.name = "LayoutConflictError";
  }
}

function clonePanel(panel: PanelLayout): PanelLayout {
  return { ...panel };
}

export function cloneLayout(layout: LayoutDocument): LayoutDocument {
  return {
    schemaVersion: LAYOUT_SCHEMA_VERSION,
    revision: layout.revision,
    panels: {
      chat_panel: clonePanel(layout.panels.chat_panel),
      customize_panel: clonePanel(layout.panels.customize_panel),
    },
  };
}

export function createDefaultLayout(revision = 0): LayoutDocument {
  return {
    schemaVersion: LAYOUT_SCHEMA_VERSION,
    revision,
    panels: {
      chat_panel: clonePanel(DEFAULT_PANELS.chat_panel),
      customize_panel: clonePanel(DEFAULT_PANELS.customize_panel),
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireExactKeys(
  value: Record<string, unknown>,
  allowedKeys: readonly string[],
  label: string,
): void {
  const unexpected = Object.keys(value).filter((key) => !allowedKeys.includes(key));
  if (unexpected.length > 0) {
    throw new LayoutValidationError(`${label} contains unsupported fields: ${unexpected.join(", ")}.`);
  }
}

function requireInteger(value: unknown, label: string, min: number, max: number): number {
  if (!Number.isInteger(value) || (value as number) < min || (value as number) > max) {
    throw new LayoutValidationError(`${label} must be an integer from ${min} to ${max}.`);
  }
  return value as number;
}

function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new LayoutValidationError(`${label} must be a boolean.`);
  }
  return value;
}

function requirePanelId(value: unknown): PanelId {
  if (typeof value !== "string" || !PANEL_IDS.includes(value as PanelId)) {
    throw new LayoutValidationError(`Unknown layout panel: ${String(value)}.`);
  }
  return value as PanelId;
}

function requireContainer(value: unknown, label: string): LayoutContainer {
  if (value !== "main" && value !== "sidebar_tabs") {
    throw new LayoutValidationError(`${label} must be main or sidebar_tabs.`);
  }
  return value;
}

function validatePanel(id: PanelId, input: unknown): PanelLayout {
  if (!isRecord(input)) {
    throw new LayoutValidationError(`${id} must be an object.`);
  }
  requireExactKeys(input, ["visible", "collapsed", "order", "container", "width"], id);
  const definition = PANEL_DEFINITIONS[id];
  const visible = requireBoolean(input.visible, `${id}.visible`);
  const collapsed = requireBoolean(input.collapsed, `${id}.collapsed`);
  const order = requireInteger(input.order, `${id}.order`, 0, 100);
  const container = requireContainer(input.container, `${id}.container`);
  const width = requireInteger(
    input.width,
    `${id}.width`,
    definition.minWidth,
    definition.maxWidth,
  );

  if (!definition.canHide && !visible) {
    throw new LayoutValidationError(`${id} cannot be hidden.`);
  }
  if (!definition.canCollapse && collapsed) {
    throw new LayoutValidationError(`${id} cannot be collapsed.`);
  }
  if (!definition.allowedContainers.includes(container)) {
    throw new LayoutValidationError(`${id} cannot be placed in ${container}.`);
  }
  return { visible, collapsed, order, container, width };
}

export function parseLayoutDocument(input: unknown): LayoutDocument {
  if (!isRecord(input) || input.schemaVersion !== LAYOUT_SCHEMA_VERSION) {
    throw new LayoutValidationError("Unsupported or missing layout schema version.");
  }
  requireExactKeys(input, ["schemaVersion", "revision", "panels"], "Layout document");
  const revision = requireInteger(input.revision, "revision", 0, Number.MAX_SAFE_INTEGER);
  if (!isRecord(input.panels)) {
    throw new LayoutValidationError("Layout panels must be an object.");
  }
  const panelKeys = Object.keys(input.panels);
  if (
    panelKeys.length !== PANEL_IDS.length ||
    panelKeys.some((panelId) => !PANEL_IDS.includes(panelId as PanelId))
  ) {
    throw new LayoutValidationError("Layout panels must exactly match the registered panels.");
  }
  return {
    schemaVersion: LAYOUT_SCHEMA_VERSION,
    revision,
    panels: {
      chat_panel: validatePanel("chat_panel", input.panels.chat_panel),
      customize_panel: validatePanel("customize_panel", input.panels.customize_panel),
    },
  };
}

function parseOperation(input: unknown): LayoutOperation {
  if (!isRecord(input) || typeof input.operation !== "string") {
    throw new LayoutValidationError("Each layout operation must be an object with an operation.");
  }
  requireExactKeys(input, ["operation", "target", "value"], "Layout operation");
  const target = requirePanelId(input.target);
  switch (input.operation) {
    case "set_visibility":
      return { operation: input.operation, target, value: requireBoolean(input.value, "value") };
    case "set_collapsed":
      return { operation: input.operation, target, value: requireBoolean(input.value, "value") };
    case "set_order":
      return {
        operation: input.operation,
        target,
        value: requireInteger(input.value, "value", 0, 100),
      };
    case "set_tab_group":
      return {
        operation: input.operation,
        target,
        value: requireContainer(input.value, "value"),
      };
    case "set_width": {
      const definition = PANEL_DEFINITIONS[target];
      return {
        operation: input.operation,
        target,
        value: requireInteger(input.value, "value", definition.minWidth, definition.maxWidth),
      };
    }
    default:
      throw new LayoutValidationError(`Unsupported layout operation: ${input.operation}.`);
  }
}

export function parseLayoutCommand(input: unknown): LayoutCommand {
  if (!isRecord(input) || input.schemaVersion !== LAYOUT_SCHEMA_VERSION) {
    throw new LayoutValidationError("Unsupported or missing layout command schema version.");
  }
  requireExactKeys(input, ["schemaVersion", "baseRevision", "operations"], "Layout command");
  const baseRevision = requireInteger(
    input.baseRevision,
    "baseRevision",
    0,
    Number.MAX_SAFE_INTEGER,
  );
  if (!Array.isArray(input.operations) || input.operations.length < 1) {
    throw new LayoutValidationError("A layout command must contain at least one operation.");
  }
  if (input.operations.length > 32) {
    throw new LayoutValidationError("A layout command cannot contain more than 32 operations.");
  }
  return {
    schemaVersion: LAYOUT_SCHEMA_VERSION,
    baseRevision,
    operations: input.operations.map(parseOperation),
  };
}

export function applyLayoutCommand(current: LayoutDocument, input: unknown): LayoutDocument {
  const command = parseLayoutCommand(input);
  if (command.baseRevision !== current.revision) {
    throw new LayoutConflictError(current.revision, command.baseRevision);
  }
  const next = cloneLayout(current);
  for (const operation of command.operations) {
    const panel = next.panels[operation.target];
    switch (operation.operation) {
      case "set_visibility":
        panel.visible = operation.value;
        break;
      case "set_collapsed":
        panel.collapsed = operation.value;
        break;
      case "set_order":
        panel.order = operation.value;
        break;
      case "set_tab_group":
        panel.container = operation.value;
        break;
      case "set_width":
        panel.width = operation.value;
        break;
    }
  }
  next.revision += 1;
  return parseLayoutDocument(next);
}

function browserStorage(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function loadLayout(storage: StorageLike | null = browserStorage()): LayoutDocument {
  if (!storage) return createDefaultLayout();
  try {
    const value = storage.getItem(LAYOUT_STORAGE_KEY);
    return value ? parseLayoutDocument(JSON.parse(value) as unknown) : createDefaultLayout();
  } catch {
    return createDefaultLayout();
  }
}

export function persistLayout(
  layout: LayoutDocument,
  storage: StorageLike | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(parseLayoutDocument(layout)));
  } catch {
    // The interface remains usable when storage is unavailable or full.
  }
}

function parseSavedLayout(input: unknown): SavedLayout {
  if (!isRecord(input)) throw new LayoutValidationError("Saved layout must be an object.");
  requireExactKeys(input, ["id", "name", "createdAt", "layout"], "Saved layout");
  if (typeof input.id !== "string" || !/^[A-Za-z0-9_-]{1,100}$/.test(input.id)) {
    throw new LayoutValidationError("Saved layout ID is invalid.");
  }
  if (typeof input.name !== "string" || input.name.trim().length < 1 || input.name.length > 40) {
    throw new LayoutValidationError("Saved layout name must contain 1 to 40 characters.");
  }
  if (typeof input.createdAt !== "string" || Number.isNaN(Date.parse(input.createdAt))) {
    throw new LayoutValidationError("Saved layout date is invalid.");
  }
  return {
    id: input.id,
    name: input.name.trim(),
    createdAt: input.createdAt,
    layout: parseLayoutDocument(input.layout),
  };
}

export function loadSavedLayouts(storage: StorageLike | null = browserStorage()): SavedLayout[] {
  if (!storage) return [];
  try {
    const value = storage.getItem(SAVED_LAYOUTS_STORAGE_KEY);
    if (!value) return [];
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, 20).map(parseSavedLayout);
  } catch {
    return [];
  }
}

export function persistSavedLayouts(
  layouts: SavedLayout[],
  storage: StorageLike | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(
      SAVED_LAYOUTS_STORAGE_KEY,
      JSON.stringify(layouts.slice(0, 20).map(parseSavedLayout)),
    );
  } catch {
    // Saved layouts are optional; storage failure must not break chat.
  }
}

export function createSavedLayout(layout: LayoutDocument, name: string): SavedLayout {
  const normalizedName = name.trim();
  if (normalizedName.length < 1 || normalizedName.length > 40) {
    throw new LayoutValidationError("Saved layout name must contain 1 to 40 characters.");
  }
  const id = globalThis.crypto?.randomUUID?.() ?? `layout-${Date.now()}`;
  return {
    id,
    name: normalizedName,
    createdAt: new Date().toISOString(),
    layout: cloneLayout(layout),
  };
}

export function applySavedLayout(current: LayoutDocument, saved: SavedLayout): LayoutDocument {
  const parsed = parseSavedLayout(saved);
  const next = cloneLayout(parsed.layout);
  next.revision = current.revision + 1;
  return parseLayoutDocument(next);
}
