import {
  getComfyArtifactContent,
  type ComfyArtifactSummary,
} from "../../lib/projectMasterApi";
import { useAppPreferences } from "../../hooks/useAppPreferences";
import { Stamp } from "../workspaces/DashboardPrimitives";
import {
  mediaPreviewKind,
  shouldAutoLoadPreview,
} from "./mediaPreview";
import { useViewportMediaPreview } from "./useViewportMediaPreview";

function safeArtifactDownloadName(
  artifact: ComfyArtifactSummary,
): string {
  const pathParts = artifact.originalFilename.split(/[/\\]/).filter(Boolean);
  const basename = pathParts[pathParts.length - 1] ?? "";
  const sanitized = basename
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .slice(0, 180);
  return sanitized || `${artifact.id}.bin`;
}

export function ComfyArtifactCard({
  jobId,
  artifact,
}: {
  jobId: string;
  artifact: ComfyArtifactSummary;
}) {
  const preferences = useAppPreferences();
  const previewKind = mediaPreviewKind(artifact.mediaType);
  const {
    cardRef,
    url,
    loading,
    error,
    load,
    release,
    reportDecodeError,
  } = useViewportMediaPreview({
    autoLoad: shouldAutoLoadPreview(
      previewKind,
      artifact.verified,
      preferences.autoLoadMediaPreviews,
    ),
    expectedSize: artifact.sizeBytes,
    loadBlob: (signal) =>
      getComfyArtifactContent(jobId, artifact.id, signal),
    sizeMismatchMessage:
      "Downloaded artifact size did not match its verified manifest.",
  });

  return (
    <article className="comfy-artifact-card" ref={cardRef}>
      <header>
        <div>
          <strong>{artifact.originalFilename}</strong>
          <span>
            {artifact.mediaType} · {(artifact.sizeBytes / 1024).toFixed(1)} KB
          </span>
        </div>
        <b className={artifact.verified ? "is-verified" : ""}>
          {artifact.verified ? "VERIFIED" : "UNVERIFIED"}
        </b>
      </header>
      {loading && previewKind ? (
        <div
          className={`creator-media-preview-state is-${previewKind}`}
          role="status"
        >
          Loading verified {previewKind} preview…
        </div>
      ) : null}
      {url && previewKind === "image" ? (
        <img
          className="comfy-artifact-preview"
          src={url}
          alt={`ComfyUI output ${artifact.originalFilename}`}
          loading="lazy"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this image preview.",
            )
          }
        />
      ) : null}
      {url && previewKind === "audio" ? (
        <audio
          className="comfy-artifact-preview"
          src={url}
          controls
          preload="metadata"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this audio preview.",
            )
          }
        />
      ) : null}
      {url && previewKind === "video" ? (
        <video
          className="comfy-artifact-preview"
          src={url}
          controls
          preload="metadata"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this video preview.",
            )
          }
        />
      ) : null}
      <dl>
        <div>
          <dt>SHA-256</dt>
          <dd title={artifact.sha256}>{artifact.sha256}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            node {artifact.provenance.nodeId} ·{" "}
            {artifact.provenance.category} #{artifact.provenance.outputIndex}
          </dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd title={artifact.provenance.workflowDigest}>
            {artifact.provenance.workflowRevisionId} ·{" "}
            {artifact.provenance.workflowDigest.slice(0, 12)}…
          </dd>
        </div>
        <div>
          <dt>Prompt</dt>
          <dd>{artifact.provenance.remotePromptId}</dd>
        </div>
        <div>
          <dt>Imported</dt>
          <dd>
            <Stamp value={artifact.provenance.fetchedAt || artifact.createdAt} />
          </dd>
        </div>
      </dl>
      <div className="decision-actions">
        {!url ? (
          <button
            className="button button--secondary"
            type="button"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading
              ? "Loading…"
              : error
                ? previewKind
                  ? "Retry preview"
                  : "Retry download"
                : previewKind
                  ? "Load preview"
                  : "Prepare download"}
          </button>
        ) : (
          <>
            <a
              className="button button--secondary"
              href={url}
              download={safeArtifactDownloadName(artifact)}
            >
              Download
            </a>
            <button
              className="button button--secondary"
              type="button"
              onClick={release}
            >
              {previewKind ? "Release preview" : "Release download"}
            </button>
          </>
        )}
      </div>
      {error ? (
        <small className="artifact-error" role="alert">
          {error}
        </small>
      ) : null}
    </article>
  );
}
