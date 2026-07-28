import type {
  ProjectMasterRunActivity,
  ProjectMasterTeamCatalogModel,
} from "../lib/projectMasterApi";

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
      model.capabilities.length === 0 ||
      model.capabilities.some((capability) =>
        ["chat", "completion", "generate"].includes(capability.toLowerCase()),
      ),
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
          : "Team catalog unavailable"}
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
}

function activityLabel(kind: string): string {
  return kind.replace(/_/g, " ").toUpperCase();
}

export function RunRail({
  activities,
  runId,
  isStreaming,
  onClear,
}: RunRailProps) {
  return (
    <aside className="run-rail" aria-label="Team run activity">
      <header className="run-rail__header">
        <div>
          <span>RUN RAIL</span>
          <h2>{isStreaming ? "In progress" : runId ? "Last run" : "Standing by"}</h2>
        </div>
        {activities.length ? (
          <button type="button" onClick={onClear} disabled={isStreaming}>
            Clear
          </button>
        ) : null}
      </header>
      {runId ? <code className="run-rail__id">{runId}</code> : null}
      <div className="run-rail__notice">
        Specialist drafts and private reasoning stay hidden. Tool details are
        bounded and redact common credential fields.
      </div>
      {activities.length ? (
        <ol className="run-rail__events">
          {activities.map((activity, index) => (
            <li
              className={
                activity.kind.includes("failed") ? "is-failed" : undefined
              }
              key={`${activity.kind}-${index}`}
            >
              <span>{activityLabel(activity.kind)}</span>
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
          ))}
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
