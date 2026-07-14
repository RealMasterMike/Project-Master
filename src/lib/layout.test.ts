import { describe, expect, it } from "vitest";
import {
  LAYOUT_SCHEMA_VERSION,
  LAYOUT_STORAGE_KEY,
  SAVED_LAYOUTS_STORAGE_KEY,
  LayoutConflictError,
  LayoutValidationError,
  applyLayoutCommand,
  applySavedLayout,
  createDefaultLayout,
  createSavedLayout,
  loadLayout,
  loadSavedLayouts,
  persistLayout,
  persistSavedLayouts,
  type StorageLike,
} from "./layout";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

function command(baseRevision: number, operations: unknown[]) {
  return { schemaVersion: LAYOUT_SCHEMA_VERSION, baseRevision, operations };
}

describe("declarative layout commands", () => {
  it("applies a validated operation and advances the revision", () => {
    const current = createDefaultLayout();
    const next = applyLayoutCommand(
      current,
      command(0, [{ operation: "set_width", target: "chat_panel", value: 80 }]),
    );

    expect(next.panels.chat_panel.width).toBe(80);
    expect(next.revision).toBe(1);
    expect(current.panels.chat_panel.width).toBe(100);
  });

  it("rejects stale commands", () => {
    expect(() =>
      applyLayoutCommand(
        createDefaultLayout(4),
        command(3, [{ operation: "set_width", target: "chat_panel", value: 80 }]),
      ),
    ).toThrow(LayoutConflictError);
  });

  it("rejects unknown panels and operations", () => {
    const current = createDefaultLayout();
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [{ operation: "set_width", target: "shell_panel", value: 80 }]),
      ),
    ).toThrow(LayoutValidationError);
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [{ operation: "inject_css", target: "chat_panel", value: "*{}" }]),
      ),
    ).toThrow(LayoutValidationError);
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [
          {
            operation: "set_width",
            target: "chat_panel",
            value: 80,
            arbitraryCss: "display: none",
          },
        ]),
      ),
    ).toThrow(/unsupported fields/);
  });

  it("prevents hiding or collapsing the required chat panel", () => {
    const current = createDefaultLayout();
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [{ operation: "set_visibility", target: "chat_panel", value: false }]),
      ),
    ).toThrow(/cannot be hidden/);
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [{ operation: "set_collapsed", target: "chat_panel", value: true }]),
      ),
    ).toThrow(/cannot be collapsed/);
  });

  it("applies a batch transactionally when a later operation is invalid", () => {
    const current = createDefaultLayout();
    const snapshot = JSON.stringify(current);
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [
          { operation: "set_width", target: "chat_panel", value: 80 },
          { operation: "set_tab_group", target: "customize_panel", value: "main" },
        ]),
      ),
    ).toThrow(/cannot be placed/);
    expect(JSON.stringify(current)).toBe(snapshot);
  });

  it("bounds operation batches and panel dimensions", () => {
    const current = createDefaultLayout();
    const tooMany = Array.from({ length: 33 }, () => ({
      operation: "set_width",
      target: "chat_panel",
      value: 80,
    }));
    expect(() => applyLayoutCommand(current, command(0, tooMany))).toThrow(/more than 32/);
    expect(() =>
      applyLayoutCommand(
        current,
        command(0, [{ operation: "set_width", target: "customize_panel", value: 900 }]),
      ),
    ).toThrow(/260 to 480/);
  });
});

describe("layout persistence", () => {
  it("round-trips a valid layout and falls back safely from corrupt storage", () => {
    const storage = new MemoryStorage();
    const layout = applyLayoutCommand(
      createDefaultLayout(),
      command(0, [{ operation: "set_width", target: "chat_panel", value: 75 }]),
    );
    persistLayout(layout, storage);
    expect(loadLayout(storage)).toEqual(layout);

    storage.setItem(LAYOUT_STORAGE_KEY, '{"schemaVersion":1,"panels":"broken"}');
    expect(loadLayout(storage)).toEqual(createDefaultLayout());
  });

  it("saves, loads, and reapplies named layouts with a fresh revision", () => {
    const storage = new MemoryStorage();
    const customized = applyLayoutCommand(
      createDefaultLayout(),
      command(0, [
        { operation: "set_visibility", target: "customize_panel", value: true },
        { operation: "set_width", target: "chat_panel", value: 70 },
      ]),
    );
    const saved = createSavedLayout(customized, "Research");
    persistSavedLayouts([saved], storage);
    const loaded = loadSavedLayouts(storage);

    expect(storage.getItem(SAVED_LAYOUTS_STORAGE_KEY)).not.toBeNull();
    expect(loaded).toHaveLength(1);
    const applied = applySavedLayout(createDefaultLayout(12), loaded[0]);
    expect(applied.revision).toBe(13);
    expect(applied.panels.chat_panel.width).toBe(70);
  });
});
