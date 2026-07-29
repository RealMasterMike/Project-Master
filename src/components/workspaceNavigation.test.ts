import { describe, expect, it } from "vitest";

import {
  WORKSPACES,
  isRovingKey,
  isWorkspaceSelectable,
  nextRovingIndex,
  presentMasterStatus,
  resolveActiveWorkspace,
  type MasterStatusInput,
} from "./workspaceNavigation";

function status(overrides: Partial<MasterStatusInput> = {}) {
  return presentMasterStatus({
    connectionState: "ready",
    modelCount: 4,
    toolsEnabled: 6,
    toolsTotal: 9,
    appVersion: "0.4.0",
    ...overrides,
  });
}

function valueFor(rows: ReturnType<typeof status>, label: string) {
  const row = rows.find((item) => item.label === label);
  if (!row) throw new Error(`Missing status row: ${label}`);
  return row;
}

describe("workspace definitions", () => {
  it("keeps every route unique and resolves the active entry", () => {
    const ids = WORKSPACES.map((workspace) => workspace.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(resolveActiveWorkspace("creator").label).toBe("Creator");
    expect(resolveActiveWorkspace("settings").marker).toBe("APP");
  });

  it("falls back to the first workspace when the active id is unknown", () => {
    expect(
      resolveActiveWorkspace("nonexistent" as never).id,
    ).toBe(WORKSPACES[0].id);
  });
});

describe("streaming lock", () => {
  it("locks every workspace except the current one while streaming", () => {
    expect(isWorkspaceSelectable("creator", "chat", true)).toBe(false);
    expect(isWorkspaceSelectable("chat", "chat", true)).toBe(true);
  });

  it("leaves every workspace selectable when nothing is streaming", () => {
    for (const workspace of WORKSPACES) {
      expect(isWorkspaceSelectable(workspace.id, "chat", false)).toBe(true);
    }
  });
});

describe("roving focus", () => {
  it("wraps in both directions", () => {
    expect(nextRovingIndex("ArrowDown", 2, 4)).toBe(3);
    expect(nextRovingIndex("ArrowDown", 3, 4)).toBe(0);
    expect(nextRovingIndex("ArrowUp", 1, 4)).toBe(0);
    expect(nextRovingIndex("ArrowUp", 0, 4)).toBe(3);
  });

  it("enters the list from outside at the near edge for each direction", () => {
    expect(nextRovingIndex("ArrowDown", -1, 4)).toBe(0);
    expect(nextRovingIndex("ArrowUp", -1, 4)).toBe(3);
  });

  it("jumps to the ends with Home and End", () => {
    expect(nextRovingIndex("Home", 2, 4)).toBe(0);
    expect(nextRovingIndex("End", 2, 4)).toBe(3);
  });

  it("ignores keys that are not navigation keys", () => {
    expect(nextRovingIndex("Enter", 1, 4)).toBeNull();
    expect(nextRovingIndex("a", 1, 4)).toBeNull();
    expect(isRovingKey("Escape")).toBe(false);
    expect(isRovingKey("End")).toBe(true);
  });

  it("moves nowhere when every item is disabled", () => {
    for (const key of ["ArrowDown", "ArrowUp", "Home", "End"]) {
      expect(nextRovingIndex(key, -1, 0)).toBeNull();
    }
  });
});

describe("MASTER status", () => {
  it("reports the real counts it was given", () => {
    const rows = status();

    expect(rows.map((row) => row.label)).toEqual([
      "Local models",
      "Tools",
      "Version",
    ]);
    expect(valueFor(rows, "Local models").value).toBe("4 available");
    expect(valueFor(rows, "Tools").value).toBe("6 of 9 enabled");
    expect(valueFor(rows, "Version").value).toBe("0.4.0");
    expect(rows.every((row) => row.tone === "ready")).toBe(true);
  });

  it("never invents a count while a value is still being read", () => {
    const rows = status({
      connectionState: "checking",
      toolsEnabled: null,
      toolsTotal: null,
      appVersion: null,
    });

    expect(valueFor(rows, "Local models")).toMatchObject({
      value: "Checking…",
      tone: "pending",
    });
    expect(valueFor(rows, "Tools")).toMatchObject({
      value: "Reading…",
      tone: "pending",
    });
    expect(valueFor(rows, "Version")).toMatchObject({
      value: "Reading…",
      tone: "pending",
    });
  });

  it("says so plainly when a value cannot be read at all", () => {
    const rows = status({
      connectionState: "offline",
      toolsEnabled: false,
      toolsTotal: false,
      appVersion: false,
    });

    expect(valueFor(rows, "Local models")).toMatchObject({
      value: "Backend offline",
      tone: "unavailable",
    });
    expect(valueFor(rows, "Tools")).toMatchObject({
      value: "Unavailable",
      tone: "unavailable",
    });
    expect(valueFor(rows, "Version")).toMatchObject({
      value: "Unavailable",
      tone: "unavailable",
    });
  });

  it("distinguishes an empty install from a failed read", () => {
    const empty = status({ modelCount: 0, toolsEnabled: 0, toolsTotal: 0 });

    expect(valueFor(empty, "Local models")).toMatchObject({
      value: "None installed",
      tone: "unavailable",
    });
    expect(valueFor(empty, "Tools")).toMatchObject({
      value: "None registered",
      tone: "unavailable",
    });
  });

  it("flags models that are installed but cannot drive chat", () => {
    const rows = status({ connectionState: "empty", modelCount: 2 });

    expect(valueFor(rows, "Local models")).toMatchObject({
      value: "2 available",
      tone: "unavailable",
    });
  });

  it("flags a tool set where nothing is enabled", () => {
    const rows = status({ toolsEnabled: 0, toolsTotal: 9 });

    expect(valueFor(rows, "Tools")).toMatchObject({
      value: "0 of 9 enabled",
      tone: "unavailable",
    });
  });
});
