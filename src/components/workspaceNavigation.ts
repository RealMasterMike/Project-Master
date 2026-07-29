export type MasterWorkspace =
  | "chat"
  | "dreams"
  | "creator"
  | "voice"
  | "projects"
  | "approvals"
  | "communication"
  | "settings";

export interface WorkspaceDefinition {
  id: MasterWorkspace;
  label: string;
  description: string;
  marker?: string;
}

export const WORKSPACES: WorkspaceDefinition[] = [
  { id: "chat", label: "Command", description: "Conversations and team runs" },
  {
    id: "dreams",
    label: "Dream Lab",
    description: "Review-only model councils",
    marker: "LAB",
  },
  {
    id: "creator",
    label: "Creator",
    description: "Ideas, media, editing, and generation",
    marker: "STUDIO",
  },
  {
    id: "voice",
    label: "Voice Studio",
    description: "Voices, scripts, and renders",
    marker: "TTS",
  },
  { id: "projects", label: "Projects", description: "Binders, runs, and artifacts" },
  {
    id: "approvals",
    label: "Approvals",
    description: "Review consequential actions",
  },
  {
    id: "communication",
    label: "Communication",
    description: "Local response preferences",
  },
  {
    id: "settings",
    label: "Settings",
    description: "App version and signed updates",
    marker: "APP",
  },
];

export function resolveActiveWorkspace(
  active: MasterWorkspace,
): WorkspaceDefinition {
  return WORKSPACES.find((workspace) => workspace.id === active) ?? WORKSPACES[0];
}

/**
 * While a response is streaming every workspace except the current one is locked, so a
 * switch cannot abandon an in-flight run. The active item stays enabled so the menu can
 * still be closed by re-selecting it.
 */
export function isWorkspaceSelectable(
  workspace: MasterWorkspace,
  active: MasterWorkspace,
  streaming: boolean,
): boolean {
  return !streaming || workspace === active;
}

const ROVING_KEYS = ["ArrowDown", "ArrowUp", "Home", "End"] as const;

export type RovingKey = (typeof ROVING_KEYS)[number];

export function isRovingKey(key: string): key is RovingKey {
  return (ROVING_KEYS as readonly string[]).includes(key);
}

/**
 * Roving focus across the enabled items only. `currentIndex` is -1 when focus sits
 * outside the list, in which case Arrow Down enters at the top and Arrow Up at the
 * bottom. Returns null when the key is not a navigation key or there is nothing to move to.
 */
export function nextRovingIndex(
  key: string,
  currentIndex: number,
  itemCount: number,
): number | null {
  if (itemCount <= 0 || !isRovingKey(key)) return null;
  switch (key) {
    case "ArrowDown":
      return currentIndex < 0 ? 0 : (currentIndex + 1) % itemCount;
    case "ArrowUp":
      return currentIndex < 0
        ? itemCount - 1
        : (currentIndex - 1 + itemCount) % itemCount;
    case "Home":
      return 0;
    case "End":
      return itemCount - 1;
  }
}

export type MasterStatusTone = "ready" | "pending" | "unavailable";

export interface MasterStatusRow {
  label: string;
  value: string;
  tone: MasterStatusTone;
}

export interface MasterStatusInput {
  connectionState: "checking" | "ready" | "empty" | "offline";
  modelCount: number;
  /** null while the request is in flight, false once it failed. */
  toolsEnabled: number | null | false;
  toolsTotal: number | null | false;
  /** null while unread, false when the app is not running under Tauri. */
  appVersion: string | null | false;
}

/**
 * Every row reports what was actually read. Nothing here invents a count: an unread
 * value stays "Reading…" and a failed or unavailable one says so plainly.
 */
export function presentMasterStatus(
  input: MasterStatusInput,
): MasterStatusRow[] {
  return [
    { label: "Local models", ...presentModels(input) },
    { label: "Tools", ...presentTools(input) },
    { label: "Version", ...presentVersion(input.appVersion) },
  ];
}

function presentModels({
  connectionState,
  modelCount,
}: MasterStatusInput): Omit<MasterStatusRow, "label"> {
  if (connectionState === "checking") {
    return { value: "Checking…", tone: "pending" };
  }
  if (connectionState === "offline") {
    return { value: "Backend offline", tone: "unavailable" };
  }
  if (modelCount === 0) {
    return { value: "None installed", tone: "unavailable" };
  }
  return {
    value: `${modelCount} available`,
    tone: connectionState === "empty" ? "unavailable" : "ready",
  };
}

function presentTools({
  toolsEnabled,
  toolsTotal,
}: MasterStatusInput): Omit<MasterStatusRow, "label"> {
  if (toolsEnabled === false || toolsTotal === false) {
    return { value: "Unavailable", tone: "unavailable" };
  }
  if (toolsEnabled === null || toolsTotal === null) {
    return { value: "Reading…", tone: "pending" };
  }
  if (toolsTotal === 0) {
    return { value: "None registered", tone: "unavailable" };
  }
  return {
    value: `${toolsEnabled} of ${toolsTotal} enabled`,
    tone: toolsEnabled === 0 ? "unavailable" : "ready",
  };
}

function presentVersion(
  appVersion: string | null | false,
): Omit<MasterStatusRow, "label"> {
  if (appVersion === false) return { value: "Unavailable", tone: "unavailable" };
  if (appVersion === null) return { value: "Reading…", tone: "pending" };
  return { value: appVersion, tone: "ready" };
}
