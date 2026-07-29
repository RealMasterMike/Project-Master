import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { deriveMissionState } from "../lib/mission";
import type { ProjectMasterRunActivity } from "../lib/projectMasterApi";

interface MissionViewProps {
  goal: string;
  runId?: string;
  activities: ProjectMasterRunActivity[];
  isStreaming: boolean;
  answer: string;
  answerStatus: "complete" | "streaming" | "stopped" | "error";
  answerError?: string;
  onRetry?: () => void;
  deliveryAction?: ReactNode;
}

export function MissionView({
  goal,
  runId,
  activities,
  isStreaming,
  answer,
  answerStatus,
  answerError,
  onRetry,
  deliveryAction,
}: MissionViewProps) {
  const mission = deriveMissionState(activities, isStreaming);
  const showDelivery =
    answerStatus === "complete" || (answer && !isStreaming);

  return (
    <div className="mission-view" aria-label="Team mission">
      <header className="mission-view__header">
        <span className="mission-view__kicker">MISSION</span>
        {runId ? <code title={runId}>RUN {runId.slice(0, 8)}</code> : null}
      </header>

      <section className="mission-view__section">
        <h3>Goal</h3>
        <p className="mission-view__goal">{goal || "No objective yet."}</p>
      </section>

      <section className="mission-view__section">
        <h3>Status</h3>
        <p className="mission-view__status">
          {mission.statusLine}
          {mission.workersCompleted || mission.workersFailed ? (
            <small>
              {" "}
              · {mission.workersCompleted} specialist
              {mission.workersCompleted === 1 ? "" : "s"} completed
              {mission.workersFailed ? ` · ${mission.workersFailed} failed` : ""}
            </small>
          ) : null}
        </p>
        <div
          className="mission-view__progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(mission.progress * 100)}
          aria-valuetext={mission.statusLine}
        >
          <span
            className={`mission-view__progress-fill ${
              mission.phase === "cancelled" || mission.phase === "failed"
                ? "is-failed"
                : ""
            }`}
            style={{ width: `${Math.round(mission.progress * 100)}%` }}
          />
        </div>
      </section>

      {mission.toolEvents.length ? (
        <section className="mission-view__section">
          <h3>Tool activity</h3>
          <ul className="mission-view__list">
            {mission.toolEvents.map((event, index) => (
              <li
                key={`${event.tool}-${index}`}
                className={`is-${event.outcome}`}
              >
                <strong>{event.tool}</strong>
                <span>{event.outcome}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {mission.decisions.length ? (
        <section className="mission-view__section">
          <h3>Council decisions</h3>
          <ul className="mission-view__list">
            {mission.decisions.map((decision, index) => (
              <li
                key={`${decision.label}-${index}`}
                className={decision.failed ? "is-failed" : undefined}
              >
                <strong>{decision.label}</strong>
                <span>{decision.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {showDelivery && answer ? (
        <section className="mission-view__section mission-view__delivery">
          <header>
            <h3>Delivery</h3>
            {deliveryAction}
          </header>
          <div className="message-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
          </div>
        </section>
      ) : null}

      {answerStatus === "stopped" ? (
        <p className="message-muted">Generation stopped.</p>
      ) : null}

      {answerError ? (
        <div className="message-error" role="alert">
          <span>{answerError}</span>
          {onRetry ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={onRetry}
              disabled={isStreaming}
            >
              Retry
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
