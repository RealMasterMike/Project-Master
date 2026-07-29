import { useEffect, useRef } from "react";

import type {
  ProjectMasterRunActivity,
  ProjectMasterTeamCatalogModel,
} from "../lib/projectMasterApi";

const FOLLOW_SCROLL_THRESHOLD_PX = 64;

interface TeamStripProps {
  available: boolean;
  catalog: ProjectMasterTeamCatalogModel[];
  runId?: string;
  isStreaming: boolean;
}

export function TeamStrip({
  available,
  catalog,
  runId,
  isStreaming,
}: TeamStripProps) {
  const conversational = catalog.filter(
    (model) =>
      model.automaticEligible &&
      model.curatedPurposes.includes("team") &&
      (model.capabilities.length === 0 ||
        model.capabilities.some((capability) =>
          ["chat", "completion", "generate"].includes(capability.toLowerCase()),
        )),
  );
  const toolCapable = conversational.filter((model) =>
    model.capabilities.some((capability) =>
      ["tool", "tools", "tool_calling"].includes(capability.toLowerCase()),
    ),
  );
  return (
    <div className="team-strip" role="status" aria-label="Model team status">
      <span className={`team-strip__pulse ${isStreaming ? "is-running" : ""}`} />
      <strong>{isStreaming ? "TEAM RUNNING" : "MODEL TEAM"}</strong>
      <span>
        {available
          ? `${conversational.length} chat-ready physical model${conversational.length === 1 ? "" : "s"} · ${toolCapable.length} tool-capable`
          : "No curated Team models available"}
      </span>
      {runId ? <code title={runId}>RUN {runId.slice(0, 8)}</code> : null}
      <span className="team-strip__safety">FINAL OUTPUT ONLY</span>
    </div>
  );
}

interface RunRailProps {
  activities: ProjectMasterRunActivity[];
  runId?: string;
  isStreaming: boolean;
  onClear: () => void;
  open: boolean;
  onClose: () => void;
}

function activityLabel(kind: string): string {
  return kind.replace(/_/g, " ").toUpperCase();
}

function activityOutcome(
  activity: ProjectMasterRunActivity,
): NonNullable<ProjectMasterRunActivity["outcome"]> {
  if (activity.outcome) return activity.outcome;
  if (activity.kind.includes("failed")) return "failed";
  if (activity.kind.includes("cancelled")) return "cancelled";
  if (activity.kind.includes("skipped")) return "skipped";
  if (activity.kind.includes("completed")) return "success";
  if (activity.kind.includes("started")) return "running";
  return "info";
}

export function RunRail({
  activities,
  runId,
  isStreaming,
  onClear,
  open,
  onClose,
}: RunRailProps) {
  const railRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const followOutputRef = useRef(true);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Follow new activity only while the reader is already at the bottom.
  useEffect(() => {
    const rail = railRef.current;
    if (!rail) return;
    const onScroll = () => {
      const distanceFromBottom =
        rail.scrollHeight - rail.scrollTop - rail.clientHeight;
      followOutputRef.current = distanceFromBottom <= FOLLOW_SCROLL_THRESHOLD_PX;
    };
    rail.addEventListener("scroll", onScroll, { passive: true });
    return () => rail.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const rail = railRef.current;
    if (rail && followOutputRef.current) {
      rail.scrollTop = rail.scrollHeight;
    }
  }, [activities]);

  useEffect(() => {
    if (!open || !window.matchMedia("(max-width: 1180px)").matches) return;
    const focusFrame = window.requestAnimationFrame(() =>
      closeButtonRef.current?.focus(),
    );
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onCloseRef.current();
      window.requestAnimationFrame(() =>
        document.getElementById("team-run-rail-toggle")?.focus(),
      );
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function closeOverlay() {
    onClose();
    window.requestAnimationFrame(() =>
      document.getElementById("team-run-rail-toggle")?.focus(),
    );
  }

  return (
    <aside
      className={`run-rail ${open ? "is-open" : ""}`}
      id="team-run-rail"
      ref={railRef}
      aria-label="Team run activity"
    >
      <header className="run-rail__header">
        <div>
          <span>RUN RAIL</span>
          <h2>{isStreaming ? "In progress" : runId ? "Last run" : "Standing by"}</h2>
        </div>
        <div className="run-rail__actions">
          {activities.length ? (
            <button type="button" onClick={onClear} disabled={isStreaming}>
              Clear
            </button>
          ) : null}
          <button
            ref={closeButtonRef}
            className="run-rail__close"
            type="button"
            onClick={closeOverlay}
            aria-label="Close team activity"
          >
            ×
          </button>
        </div>
      </header>
      {runId ? <code className="run-rail__id">{runId}</code> : null}
      <div className="run-rail__notice">
        Specialist drafts and private reasoning stay hidden. Tool details are
        bounded and redact common credential fields.
      </div>
      {activities.length ? (
        <ol className="run-rail__events">
          {activities.map((activity, index) => {
            const outcome = activityOutcome(activity);
            return (
              <li
                className={`is-${outcome}`}
                key={`${activity.kind}-${index}`}
              >
                <div className="run-rail__event-heading">
                  <span>{activityLabel(activity.kind)}</span>
                  <b>{outcome}</b>
                </div>
                <strong>{activity.message}</strong>
                {activity.model || activity.tool ? (
                  <small>
                    {activity.role ? `${activity.role} · ` : ""}
                    {activity.model ?? activity.tool}
                  </small>
                ) : null}
                {activity.inputDetail || activity.outputDetail ? (
                  <details className="run-rail__tool-detail">
                    <summary>Inspect tool details</summary>
                    {activity.inputDetail ? (
                      <div>
                        <span>INPUT</span>
                        <pre>{activity.inputDetail}</pre>
                      </div>
                    ) : null}
                    {activity.outputDetail ? (
                      <div>
                        <span>OUTPUT</span>
                        <pre>{activity.outputDetail}</pre>
                      </div>
                    ) : null}
                  </details>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="run-rail__empty">
          Send in Team mode to see workers, synthesis, the lead tool loop, and delivery
          checkpoints.
        </p>
      )}
    </aside>
  );
}
