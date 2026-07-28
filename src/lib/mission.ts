import type { ProjectMasterRunActivity } from "./projectMasterApi";

export type MissionPhase =
  | "idle"
  | "council"
  | "workers"
  | "synthesis"
  | "delivered"
  | "cancelled"
  | "failed";

export interface MissionDecision {
  label: string;
  detail: string;
  failed: boolean;
}

export interface MissionToolEvent {
  tool: string;
  ok: boolean;
}

export interface MissionState {
  phase: MissionPhase;
  statusLine: string;
  progress: number;
  workersCompleted: number;
  workersFailed: number;
  decisions: MissionDecision[];
  toolEvents: MissionToolEvent[];
}

const DECISION_KINDS = new Set([
  "model_skipped",
  "worker_completed",
  "worker_failed",
  "worker_cancelled",
  "synthesis_completed",
  "synthesis_failed",
  "council_completed",
  "council_cancelled",
]);

function decisionLabel(activity: ProjectMasterRunActivity): string {
  const actor = activity.role ?? "council";
  switch (activity.kind) {
    case "worker_completed":
      return `${actor} completed`;
    case "worker_failed":
      return `${actor} failed`;
    case "worker_cancelled":
      return `${actor} cancelled`;
    case "model_skipped":
      return "model skipped";
    case "synthesis_completed":
      return "lead merged the council output";
    case "synthesis_failed":
      return "lead synthesis failed";
    case "council_completed":
      return "delivered";
    case "council_cancelled":
      return "run cancelled";
    default:
      return activity.kind.replace(/_/g, " ");
  }
}

export function deriveMissionState(
  activities: ProjectMasterRunActivity[],
  isStreaming: boolean,
): MissionState {
  let workersCompleted = 0;
  let workersFailed = 0;
  let activeWorker: ProjectMasterRunActivity | null = null;
  let sawCouncil = false;
  let sawSynthesis = false;
  let delivered = false;
  let cancelled = false;
  let synthesisFailed = false;
  const decisions: MissionDecision[] = [];
  const toolEvents: MissionToolEvent[] = [];

  for (const activity of activities) {
    switch (activity.kind) {
      case "council_started":
        sawCouncil = true;
        break;
      case "worker_started":
        activeWorker = activity;
        break;
      case "worker_completed":
        workersCompleted += 1;
        activeWorker = null;
        break;
      case "worker_failed":
        workersFailed += 1;
        activeWorker = null;
        break;
      case "worker_cancelled":
        activeWorker = null;
        break;
      case "synthesis_started":
        sawSynthesis = true;
        activeWorker = null;
        break;
      case "synthesis_completed":
        sawSynthesis = true;
        break;
      case "synthesis_failed":
        sawSynthesis = true;
        synthesisFailed = true;
        break;
      case "council_completed":
        delivered = true;
        break;
      case "council_cancelled":
        cancelled = true;
        break;
      case "tool_completed":
      case "tool_failed":
        if (activity.tool) {
          toolEvents.push({ tool: activity.tool, ok: activity.ok !== false });
        }
        break;
      default:
        break;
    }
    if (DECISION_KINDS.has(activity.kind)) {
      decisions.push({
        label: decisionLabel(activity),
        detail: activity.message,
        failed:
          activity.kind.includes("failed") ||
          activity.kind.includes("cancelled"),
      });
    }
  }

  let phase: MissionPhase = "idle";
  if (cancelled) phase = "cancelled";
  else if (delivered) phase = "delivered";
  else if (synthesisFailed && !isStreaming) phase = "failed";
  else if (sawSynthesis) phase = "synthesis";
  else if (activeWorker || workersCompleted || workersFailed) phase = "workers";
  else if (sawCouncil) phase = "council";
  if (phase === "idle" && isStreaming) phase = "council";

  let statusLine: string;
  switch (phase) {
    case "delivered":
      statusLine = "Delivered";
      break;
    case "cancelled":
      statusLine = "Cancelled";
      break;
    case "failed":
      statusLine = "Synthesis failed";
      break;
    case "synthesis":
      statusLine = "Lead synthesizing…";
      break;
    case "workers":
      statusLine = activeWorker
        ? `${activeWorker.role ?? "specialist"} working…`
        : "Council working…";
      break;
    case "council":
      statusLine = "Assembling council…";
      break;
    default:
      statusLine = "Standing by";
      break;
  }

  let progress = 0;
  if (phase === "delivered" || phase === "cancelled" || phase === "failed") {
    progress = 1;
  } else if (phase === "synthesis") {
    progress = 0.85;
  } else if (phase === "workers") {
    progress = Math.min(
      0.75,
      0.1 + (workersCompleted + workersFailed) * 0.15 + (activeWorker ? 0.05 : 0),
    );
  } else if (phase === "council") {
    progress = 0.05;
  }

  return {
    phase,
    statusLine,
    progress,
    workersCompleted,
    workersFailed,
    decisions,
    toolEvents,
  };
}
