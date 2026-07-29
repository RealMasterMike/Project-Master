import { useEffect, useState } from "react";

import type { MediaAssetSummary } from "../lib/projectMasterApi";

interface ChatImageAttachmentsProps {
  availableImages: MediaAssetSummary[];
  selectedImages: MediaAssetSummary[];
  isLoading: boolean;
  error: string | null;
  selectionError: string | null;
  disabled: boolean;
  visionModelAvailable: boolean;
  onAdd: (assetId: string) => void;
  onRemove: (assetId: string) => void;
  onRetry: () => void;
}

function formatBytes(value: number): string {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

export function ChatImageAttachments({
  availableImages,
  selectedImages,
  isLoading,
  error,
  selectionError,
  disabled,
  visionModelAvailable,
  onAdd,
  onRemove,
  onRetry,
}: ChatImageAttachmentsProps) {
  const selectedIds = new Set(selectedImages.map((asset) => asset.id));
  const remainingImages = availableImages.filter(
    (asset) => !selectedIds.has(asset.id),
  );
  const [candidateId, setCandidateId] = useState("");

  useEffect(() => {
    if (!remainingImages.some((asset) => asset.id === candidateId)) {
      setCandidateId(remainingImages[0]?.id ?? "");
    }
  }, [candidateId, remainingImages]);

  const atLimit = selectedImages.length >= 3;
  const canAdd =
    Boolean(candidateId) &&
    !atLimit &&
    !disabled &&
    !isLoading &&
    visionModelAvailable;

  return (
    <section className="chat-image-attachments" aria-label="Project image attachments">
      <div className="chat-image-attachments__heading">
        <span>
          <strong>PROJECT IMAGES</strong>
          <small>Direct vision analysis · {selectedImages.length}/3 selected</small>
        </span>
        {isLoading ? <span className="chat-image-attachments__status">Loading…</span> : null}
      </div>

      {error ? (
        <div className="chat-image-attachments__notice" role="alert">
          <span>{error}</span>
          <button type="button" onClick={onRetry} disabled={disabled}>
            Retry
          </button>
        </div>
      ) : null}

      {selectionError ? (
        <p className="chat-image-attachments__notice" role="alert">
          {selectionError}
        </p>
      ) : null}

      {!visionModelAvailable ? (
        <p className="chat-image-attachments__notice" role="status">
          Choose an installed vision model in Settings. Automatic selection
          requires the documented uncensored model's exact tag and tested
          manifest.
        </p>
      ) : null}

      {selectedImages.length ? (
        <ul className="chat-image-attachments__selected">
          {selectedImages.map((asset) => (
            <li key={asset.id}>
              <span>
                <strong>{asset.name}</strong>
                <small>
                  {asset.width}×{asset.height} · {formatBytes(asset.sizeBytes)}
                </small>
              </span>
              <button
                type="button"
                aria-label={`Remove ${asset.name}`}
                onClick={() => onRemove(asset.id)}
                disabled={disabled}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="chat-image-attachments__picker">
        <select
          aria-label="Creator Media image"
          value={candidateId}
          onChange={(event) => setCandidateId(event.currentTarget.value)}
          disabled={
            disabled ||
            isLoading ||
            atLimit ||
            remainingImages.length === 0
          }
        >
          {remainingImages.length === 0 ? (
            <option value="">
              {isLoading ? "Loading project images…" : "No images available"}
            </option>
          ) : (
            remainingImages.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.name} · {asset.width}×{asset.height}
              </option>
            ))
          )}
        </select>
        <button
          className="button button--secondary"
          type="button"
          disabled={!canAdd}
          onClick={() => {
            onAdd(candidateId);
            setCandidateId("");
          }}
        >
          Add image
        </button>
      </div>
    </section>
  );
}
