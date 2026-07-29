import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { getVersion } from "@tauri-apps/api/app";

import { isAppUpdateRuntime } from "../lib/autoUpdater";
import { getToolStatus } from "../lib/projectMasterApi";
import {
  WORKSPACES,
  isRovingKey,
  isWorkspaceSelectable,
  nextRovingIndex,
  presentMasterStatus,
  resolveActiveWorkspace,
} from "./workspaceNavigation";
import type { MasterWorkspace } from "./workspaceNavigation";

export type { MasterWorkspace } from "./workspaceNavigation";

interface WorkspaceNavigationProps {
  active: MasterWorkspace;
  disabled: boolean;
  connectionState: "checking" | "ready" | "empty" | "offline";
  modelCount: number;
  onChange: (workspace: MasterWorkspace) => void;
}

export function WorkspaceNavigation({
  active,
  disabled,
  connectionState,
  modelCount,
  onChange,
}: WorkspaceNavigationProps) {
  const [open, setOpen] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState<number | null | false>(null);
  const [toolsTotal, setToolsTotal] = useState<number | null | false>(null);
  const [appVersion, setAppVersion] = useState<string | null | false>(
    isAppUpdateRuntime() ? null : false,
  );
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const toolsControllerRef = useRef<AbortController | null>(null);
  const activeWorkspace = resolveActiveWorkspace(active);
  const status = presentMasterStatus({
    connectionState,
    modelCount,
    toolsEnabled,
    toolsTotal,
    appVersion,
  });

  const closeAndRestoreFocus = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  // Status is read once the panel is first opened rather than polled, so the menu never
  // adds background traffic to the catalog and tool endpoints the workspaces already use.
  useEffect(() => {
    if (!open || toolsEnabled !== null) return;
    const controller = new AbortController();
    toolsControllerRef.current = controller;
    getToolStatus(controller.signal)
      .then(({ tools }) => {
        if (controller.signal.aborted) return;
        setToolsTotal(tools.length);
        setToolsEnabled(tools.filter((tool) => tool.enabled).length);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setToolsTotal(false);
        setToolsEnabled(false);
      });
    return () => controller.abort();
  }, [open, toolsEnabled]);

  useEffect(() => {
    if (!open || appVersion !== null) return;
    let cancelled = false;
    void getVersion()
      .then((version) => {
        if (!cancelled) setAppVersion(version);
      })
      .catch(() => {
        if (!cancelled) setAppVersion(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appVersion]);

  useEffect(() => () => toolsControllerRef.current?.abort(), []);

  useEffect(() => {
    if (!open) return;
    const focusFrame = window.requestAnimationFrame(() => {
      const preferredIndex = WORKSPACES.findIndex(
        (workspace) => workspace.id === active,
      );
      const preferred = itemRefs.current[preferredIndex];
      const fallback = itemRefs.current.find((item) => item && !item.disabled);
      (preferred && !preferred.disabled ? preferred : fallback)?.focus();
    });
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      closeAndRestoreFocus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [active, closeAndRestoreFocus, open]);

  function moveItemFocus(event: ReactKeyboardEvent<HTMLElement>) {
    if (!isRovingKey(event.key)) return;
    const items = itemRefs.current.filter(
      (item): item is HTMLButtonElement => Boolean(item && !item.disabled),
    );
    const nextIndex = nextRovingIndex(
      event.key,
      items.indexOf(document.activeElement as HTMLButtonElement),
      items.length,
    );
    if (nextIndex === null) return;
    event.preventDefault();
    items[nextIndex]?.focus();
  }

  return (
    <div className="workspace-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        className="workspace-menu__trigger"
        type="button"
        aria-expanded={open}
        aria-controls="workspace-navigation-panel"
        aria-label={`${open ? "Close" : "Open"} workspace menu. Current workspace: ${activeWorkspace.label}`}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="workspace-menu__icon" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className="workspace-menu__label">{activeWorkspace.label}</span>
        {activeWorkspace.marker ? (
          <small>{activeWorkspace.marker}</small>
        ) : null}
      </button>

      {open ? (
        <nav
          className="workspace-menu__panel"
          id="workspace-navigation-panel"
          aria-label="Project Master workspaces"
          onKeyDown={moveItemFocus}
        >
          <div className="workspace-menu__items">
            {WORKSPACES.map((workspace, index) => (
              <button
                ref={(node) => {
                  itemRefs.current[index] = node;
                }}
                className={workspace.id === active ? "is-active" : undefined}
                type="button"
                key={workspace.id}
                aria-current={workspace.id === active ? "page" : undefined}
                disabled={!isWorkspaceSelectable(workspace.id, active, disabled)}
                onClick={() => {
                  onChange(workspace.id);
                  closeAndRestoreFocus();
                }}
              >
                <span className="workspace-menu__copy">
                  <strong>{workspace.label}</strong>
                  <span>{workspace.description}</span>
                </span>
                <span className="workspace-menu__meta">
                  {workspace.marker ? <small>{workspace.marker}</small> : null}
                  {workspace.id === active ? (
                    <span aria-label="Current workspace">✓</span>
                  ) : null}
                </span>
              </button>
            ))}
          </div>

          <dl className="workspace-menu__status" aria-label="MASTER status">
            <div className="workspace-menu__status-heading" aria-hidden="true">
              MASTER status
            </div>
            {status.map((row) => (
              <div className="workspace-menu__status-row" key={row.label}>
                <dt>{row.label}</dt>
                <dd data-tone={row.tone}>{row.value}</dd>
              </div>
            ))}
          </dl>
        </nav>
      ) : null}
    </div>
  );
}
