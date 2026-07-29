import { useRef } from "react";

import type {
  ComfyOverview,
  MasterProject,
} from "../../lib/projectMasterApi";
import {
  Empty,
  Panel,
} from "../workspaces/DashboardPrimitives";
import { ComfyArtifactCard } from "./ComfyArtifactCard";

interface ComfyJobLedgerProps {
  overview: ComfyOverview | null;
  projects: MasterProject[];
  busy: boolean;
  onRefreshJob: (jobId: string) => Promise<void>;
  onCancelJob: (jobId: string) => Promise<void>;
  onOpenMedia: (projectId: string) => void;
}

export function ComfyJobLedger({
  overview,
  projects,
  busy,
  onRefreshJob,
  onCancelJob,
  onOpenMedia,
}: ComfyJobLedgerProps) {
  const jobRefreshRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  return (
    <Panel
      title="Job ledger"
      kicker={`${overview?.jobs.length ?? 0} JOBS`}
      wide
    >
      <ul className="job-list comfy-job-list">
        {overview?.jobs.map((job) => {
          const jobWorkflow = overview.workflows.find(
            (workflow) => workflow.id === job.workflowRevisionId,
          );
          const jobProject = projects.find(
            (project) => project.id === job.projectId,
          );
          const hasVerifiedMedia = job.artifacts.some(
            (artifact) =>
              artifact.verified &&
              /^(?:image|audio|video)\//.test(artifact.mediaType),
          );
          const missingVerifiedVideo =
            job.status === "succeeded" &&
            jobWorkflow?.purpose === "video" &&
            !job.artifacts.some(
              (artifact) =>
                artifact.verified &&
                artifact.mediaType.startsWith("video/"),
            );
          return (
            <li key={job.id}>
              <div className="comfy-job-header">
                <div>
                  <strong
                    role="status"
                    aria-live="polite"
                    aria-atomic="true"
                  >
                    {job.status}
                  </strong>
                  <span>
                    {jobWorkflow ? (
                      <span
                        className={`workflow-purpose-badge is-${jobWorkflow.purpose}`}
                      >
                        {jobWorkflow.purpose}
                      </span>
                    ) : null}{" "}
                    {job.workflowRevisionId}
                  </span>
                  <span>
                    Studio:{" "}
                    {job.projectId
                      ? jobProject?.name ?? job.projectId
                      : "Unassigned legacy job"}
                  </span>
                  <code>{job.id}</code>
                </div>
                <div
                  className="comfy-artifact-status"
                  role="status"
                  aria-live="polite"
                >
                  <b>{job.artifactStatus.toUpperCase()}</b>
                  <span>
                    {job.artifacts.length} persisted artifact
                    {job.artifacts.length === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="comfy-job-actions">
                  {job.status === "succeeded" &&
                  jobProject &&
                  hasVerifiedMedia ? (
                    <button
                      className="comfy-job-actions__media"
                      type="button"
                      aria-label={`Open ${jobProject.name} Media for ComfyUI job ${job.id}`}
                      onClick={() => onOpenMedia(jobProject.id)}
                    >
                      Open Media
                    </button>
                  ) : null}
                  <button
                    ref={(node) => {
                      jobRefreshRefs.current[job.id] = node;
                    }}
                    type="button"
                    disabled={busy}
                    aria-label={`Refresh ComfyUI job ${job.id}`}
                    onClick={() => void onRefreshJob(job.id)}
                  >
                    Refresh
                  </button>
                  {!["succeeded", "failed", "cancelled"].includes(job.status) ? (
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`Cancel ComfyUI job ${job.id}`}
                      onClick={() => {
                        void onCancelJob(job.id).then(() =>
                          window.requestAnimationFrame(() =>
                            jobRefreshRefs.current[job.id]?.focus(),
                          ),
                        );
                      }}
                    >
                      Cancel
                    </button>
                  ) : null}
                </div>
              </div>
              {job.error ? (
                <small className="artifact-error" role="alert">
                  {job.error}
                </small>
              ) : null}
              {job.artifactError ? (
                <small className="artifact-error" role="alert">
                  Artifact import: {job.artifactError}
                </small>
              ) : null}
              {missingVerifiedVideo ? (
                <div className="comfy-video-warning" role="alert">
                  <strong>No verified video output</strong>
                  <span>
                    This video workflow succeeded, but no verified video
                    artifact was persisted. Refresh the job, then inspect its
                    ComfyUI output nodes and referenced model weights.
                  </span>
                </div>
              ) : null}
              {job.artifacts.length ? (
                <div className="comfy-artifact-gallery">
                  {job.artifacts.map((artifact) => (
                    <ComfyArtifactCard
                      jobId={job.id}
                      artifact={artifact}
                      key={artifact.id}
                    />
                  ))}
                </div>
              ) : job.status === "succeeded" ? (
                <small>
                  No local artifact is available. Status: {job.artifactStatus}.
                </small>
              ) : null}
            </li>
          );
        })}
      </ul>
      {!overview?.jobs.length ? (
        <Empty>
          No ComfyUI jobs. Offline is expected until a profile is reachable.
        </Empty>
      ) : null}
    </Panel>
  );
}
