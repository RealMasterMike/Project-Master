import { useCallback, useEffect, useState } from "react";
import {
  LAYOUT_SCHEMA_VERSION,
  applyLayoutCommand,
  applySavedLayout,
  cloneLayout,
  createDefaultLayout,
  createSavedLayout,
  loadLayout,
  loadSavedLayouts,
  persistLayout,
  persistSavedLayouts,
  type LayoutDocument,
  type LayoutOperation,
  type SavedLayout,
} from "../lib/layout";

interface LayoutControllerState {
  layout: LayoutDocument;
  history: LayoutDocument[];
  savedLayouts: SavedLayout[];
}

const MAX_UNDO_STEPS = 50;

function pushHistory(history: LayoutDocument[], layout: LayoutDocument): LayoutDocument[] {
  return [...history, cloneLayout(layout)].slice(-MAX_UNDO_STEPS);
}

export function useLayoutController() {
  const [state, setState] = useState<LayoutControllerState>(() => ({
    layout: loadLayout(),
    history: [],
    savedLayouts: loadSavedLayouts(),
  }));

  useEffect(() => persistLayout(state.layout), [state.layout]);
  useEffect(() => persistSavedLayouts(state.savedLayouts), [state.savedLayouts]);

  const applyOperations = useCallback((operations: LayoutOperation[]) => {
    setState((current) => {
      const next = applyLayoutCommand(current.layout, {
        schemaVersion: LAYOUT_SCHEMA_VERSION,
        baseRevision: current.layout.revision,
        operations,
      });
      return {
        ...current,
        layout: next,
        history: pushHistory(current.history, current.layout),
      };
    });
  }, []);

  const undo = useCallback(() => {
    setState((current) => {
      const previous = current.history[current.history.length - 1];
      if (!previous) return current;
      const restored = cloneLayout(previous);
      restored.revision = current.layout.revision + 1;
      return {
        ...current,
        layout: restored,
        history: current.history.slice(0, -1),
      };
    });
  }, []);

  const reset = useCallback(() => {
    setState((current) => ({
      ...current,
      layout: createDefaultLayout(current.layout.revision + 1),
      history: pushHistory(current.history, current.layout),
    }));
  }, []);

  const saveCurrent = useCallback((name: string) => {
    setState((current) => ({
      ...current,
      savedLayouts: [
        createSavedLayout(current.layout, name),
        ...current.savedLayouts,
      ].slice(0, 20),
    }));
  }, []);

  const applySaved = useCallback((id: string) => {
    setState((current) => {
      const saved = current.savedLayouts.find((item) => item.id === id);
      if (!saved) return current;
      return {
        ...current,
        layout: applySavedLayout(current.layout, saved),
        history: pushHistory(current.history, current.layout),
      };
    });
  }, []);

  const deleteSaved = useCallback((id: string) => {
    setState((current) => ({
      ...current,
      savedLayouts: current.savedLayouts.filter((item) => item.id !== id),
    }));
  }, []);

  return {
    layout: state.layout,
    canUndo: state.history.length > 0,
    savedLayouts: state.savedLayouts,
    applyOperations,
    undo,
    reset,
    saveCurrent,
    applySaved,
    deleteSaved,
  };
}
