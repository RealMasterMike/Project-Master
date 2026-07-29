import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  formatProjectMasterError,
  getMediaAssetContent,
  getMediaHealth,
  isAbortError,
  listProjectMediaAssets,
  trimProjectVideo,
  type MasterProject,
  type MediaAssetSummary,
  type MediaHealth,
} from "../../lib/projectMasterApi";
import {
  TRIM_CONTROL_STEP_SECONDS,
  TRIM_NUDGE_SECONDS,
  clamp,
  finiteDuration,
  nudgeTrimPoint,
  parseTrimControlNumber,
  setTrimPointFromPlayhead,
  validateTrimBounds,
  type TrimBoundsIssue,
  type TrimPoint,
  type TrimRange,
} from "./videoTrimControls";

function controlValue(value: number): string {
  return value.toFixed(2);
}

function formatTime(value: number): string {
  const safe = Math.max(0, value);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  return [
    ...(hours ? [String(hours).padStart(2, "0")] : []),
    String(minutes).padStart(2, "0"),
    seconds.toFixed(2).padStart(5, "0"),
  ].join(":");
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

function defaultOutputName(sourceName: string): string {
  const parts = sourceName.split(/[/\\]/).filter(Boolean);
  const basename = parts[parts.length - 1] ?? "video";
  const stem = basename.replace(/\.[^.]+$/, "").slice(0, 150) || "video";
  return `${stem}-trim.mp4`;
}

function sourceDescription(asset: MediaAssetSummary): string {
  const duration = finiteDuration(asset.durationSeconds);
  const dimensions =
    asset.width !== undefined && asset.height !== undefined
      ? `${asset.width}×${asset.height}`
      : "frame unknown";
  return `${duration ? formatTime(duration) : "duration pending"} · ${dimensions} · ${formatBytes(asset.sizeBytes)}`;
}

function boundsIssueMessage(
  issue: TrimBoundsIssue,
  previewLoading: boolean,
): string {
  switch (issue) {
    case "duration_required":
      return previewLoading
        ? "Reading verified video duration…"
        : "A verified duration is required before trimming.";
    case "start_nonnegative":
      return "Start must be zero or later.";
    case "end_after_start":
      return "End must be later than start.";
    case "end_after_duration":
      return "End cannot exceed the source duration.";
    case "span_too_short":
      return "Choose at least 0.01 seconds of video.";
  }
}

interface VideoTrimEditorProps {
  project?: MasterProject;
  onViewMedia: () => void;
}

export function VideoTrimEditor({
  project,
  onViewMedia,
}: VideoTrimEditorProps) {
  const [health, setHealth] = useState<MediaHealth | null>(null);
  const [videos, setVideos] = useState<MediaAssetSummary[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [loading, setLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [trimming, setTrimming] = useState(false);
  const [resultPreviewLoading, setResultPreviewLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [trimError, setTrimError] = useState("");
  const [resultPreviewError, setResultPreviewError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [resultPreviewUrl, setResultPreviewUrl] = useState("");
  const [previewDuration, setPreviewDuration] = useState<number>();
  const [startInput, setStartInput] = useState("0.00");
  const [endInput, setEndInput] = useState("");
  const [outputName, setOutputName] = useState("");
  const [result, setResult] = useState<MediaAssetSummary>();
  const [resultMessage, setResultMessage] = useState("");
  const loadControllerRef = useRef<AbortController | null>(null);
  const previewControllerRef = useRef<AbortController | null>(null);
  const trimControllerRef = useRef<AbortController | null>(null);
  const resultPreviewControllerRef = useRef<AbortController | null>(null);
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const refreshSourcesButtonRef = useRef<HTMLButtonElement | null>(null);
  const resultArticleRef = useRef<HTMLElement | null>(null);
  const projectId = project?.id;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!projectId) {
        setHealth(null);
        setVideos([]);
        setSelectedAssetId("");
        setLoading(false);
        return;
      }
      setLoading(true);
      setLoadError("");
      try {
        const [nextHealth, assets] = await Promise.all([
          getMediaHealth(signal),
          listProjectMediaAssets(projectId, signal),
        ]);
        if (signal?.aborted) return;
        const nextVideos = assets.filter((asset) => asset.kind === "video");
        setHealth(nextHealth);
        setVideos(nextVideos);
        setSelectedAssetId((current) =>
          nextVideos.some((asset) => asset.id === current)
            ? current
            : nextVideos[0]?.id ?? "",
        );
      } catch (caught) {
        if (!signal?.aborted && !isAbortError(caught)) {
          setLoadError(formatProjectMasterError(caught));
        }
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    loadControllerRef.current?.abort();
    previewControllerRef.current?.abort();
    trimControllerRef.current?.abort();
    resultPreviewControllerRef.current?.abort();
    setHealth(null);
    setVideos([]);
    setSelectedAssetId("");
    setPreviewUrl("");
    setResultPreviewUrl("");
    setResult(undefined);
    setResultMessage("");
    setLoading(false);
    setPreviewLoading(false);
    setTrimming(false);
    setResultPreviewLoading(false);
    setLoadError("");
    setPreviewError("");
    setTrimError("");
    setResultPreviewError("");
    if (!projectId) return;

    const controller = new AbortController();
    loadControllerRef.current = controller;
    void load(controller.signal).finally(() => {
      if (loadControllerRef.current === controller) {
        loadControllerRef.current = null;
      }
    });
    return () => controller.abort();
  }, [load, projectId]);

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  useEffect(
    () => () => {
      if (resultPreviewUrl) URL.revokeObjectURL(resultPreviewUrl);
    },
    [resultPreviewUrl],
  );

  const selectedAsset = videos.find(
    (asset) => asset.id === selectedAssetId,
  );

  useEffect(() => {
    previewControllerRef.current?.abort();
    resultPreviewControllerRef.current?.abort();
    setPreviewUrl("");
    setResultPreviewUrl("");
    setPreviewDuration(undefined);
    setPreviewError("");
    setTrimError("");
    setResultPreviewError("");
    setResult(undefined);
    setResultMessage("");
    setPreviewLoading(false);
    setResultPreviewLoading(false);
    setStartInput("0.00");
    setEndInput(
      finiteDuration(selectedAsset?.durationSeconds) === undefined
        ? ""
        : controlValue(selectedAsset!.durationSeconds!),
    );
    setOutputName(selectedAsset ? defaultOutputName(selectedAsset.name) : "");
    if (!selectedAsset) {
      setPreviewLoading(false);
      return;
    }
    if (!selectedAsset.mediaType.startsWith("video/")) {
      setPreviewError(
        `This asset is labeled ${selectedAsset.mediaType}, not a supported video type.`,
      );
      setPreviewLoading(false);
      return;
    }

    const controller = new AbortController();
    previewControllerRef.current = controller;
    setPreviewLoading(true);
    void getMediaAssetContent(selectedAsset.id, controller.signal)
      .then((blob) => {
        if (blob.size !== selectedAsset.sizeBytes) {
          throw new Error(
            "Downloaded video size did not match its verified manifest.",
          );
        }
        if (blob.type && !blob.type.startsWith("video/")) {
          throw new Error(
            "Downloaded media did not have a verified video content type.",
          );
        }
        if (controller.signal.aborted) return;
        const nextUrl = URL.createObjectURL(blob);
        if (controller.signal.aborted) {
          URL.revokeObjectURL(nextUrl);
          return;
        }
        setPreviewUrl(nextUrl);
      })
      .catch((caught) => {
        if (!controller.signal.aborted && !isAbortError(caught)) {
          setPreviewError(formatProjectMasterError(caught));
        }
      })
      .finally(() => {
        if (previewControllerRef.current === controller) {
          previewControllerRef.current = null;
          if (!controller.signal.aborted) setPreviewLoading(false);
        }
      });
    return () => controller.abort();
  }, [selectedAsset]);

  useEffect(
    () => () => {
      loadControllerRef.current?.abort();
      previewControllerRef.current?.abort();
      trimControllerRef.current?.abort();
      resultPreviewControllerRef.current?.abort();
    },
    [],
  );

  const assetDuration = finiteDuration(selectedAsset?.durationSeconds);
  const knownDuration = assetDuration ?? finiteDuration(previewDuration);
  const startSeconds = parseTrimControlNumber(startInput);
  const endSeconds = parseTrimControlNumber(endInput);
  const spanSeconds =
    startSeconds !== undefined && endSeconds !== undefined
      ? endSeconds - startSeconds
      : undefined;

  const boundsIssue = validateTrimBounds(
    startSeconds,
    endSeconds,
    knownDuration,
  );
  let validationMessage = "";
  if (!selectedAsset) {
    validationMessage = "Choose a source video.";
  } else if (!selectedAsset.mediaType.startsWith("video/")) {
    validationMessage = "The selected asset does not have video metadata.";
  } else if (boundsIssue) {
    validationMessage = boundsIssueMessage(boundsIssue, previewLoading);
  } else if (outputName.trim().length > 180) {
    validationMessage = "Output name must be 180 characters or fewer.";
  } else if (/[\\/]/.test(outputName)) {
    validationMessage = "Output name must be a file name, not a path.";
  } else if (/[\u0000-\u001f\u007f]/.test(outputName)) {
    validationMessage = "Output name cannot contain control characters.";
  } else if (
    outputName.trim() &&
    !outputName.trim().toLowerCase().endsWith(".mp4")
  ) {
    validationMessage = "Output name must end in .mp4.";
  }

  const processingUnavailable =
    health?.available === false || health?.ffmpegAvailable === false;
  const canTrim =
    Boolean(projectId) &&
    Boolean(selectedAsset) &&
    !processingUnavailable &&
    !validationMessage &&
    !loading &&
    !trimming;
  const rangeDuration = Math.max(
    knownDuration ?? 1,
    TRIM_CONTROL_STEP_SECONDS,
  );
  const rangeAvailable =
    knownDuration !== undefined &&
    knownDuration >= TRIM_CONTROL_STEP_SECONDS;
  const rangeStart = clamp(startSeconds ?? 0, 0, rangeDuration);
  const rangeEnd = clamp(endSeconds ?? rangeDuration, 0, rangeDuration);
  const pointActionsAvailable =
    rangeAvailable &&
    boundsIssue === null &&
    startSeconds !== undefined &&
    endSeconds !== undefined &&
    !trimming;
  const playheadActionsAvailable =
    pointActionsAvailable &&
    Boolean(previewUrl) &&
    previewDuration !== undefined;

  function applyTrimRange(range: TrimRange | undefined) {
    if (!range) return;
    setStartInput(controlValue(range.startSeconds));
    setEndInput(controlValue(range.endSeconds));
  }

  function setPointFromPlayhead(point: TrimPoint) {
    if (
      !playheadActionsAvailable ||
      knownDuration === undefined ||
      startSeconds === undefined ||
      endSeconds === undefined
    ) {
      return;
    }
    const playheadSeconds = previewRef.current?.currentTime;
    if (playheadSeconds === undefined) return;
    applyTrimRange(
      setTrimPointFromPlayhead(
        point,
        playheadSeconds,
        { startSeconds, endSeconds },
        knownDuration,
      ),
    );
  }

  function nudgePoint(point: TrimPoint, deltaSeconds: number) {
    if (
      !pointActionsAvailable ||
      knownDuration === undefined ||
      startSeconds === undefined ||
      endSeconds === undefined
    ) {
      return;
    }
    applyTrimRange(
      nudgeTrimPoint(
        point,
        deltaSeconds,
        { startSeconds, endSeconds },
        knownDuration,
      ),
    );
  }

  async function loadResultPreview(
    asset: MediaAssetSummary,
    parentSignal?: AbortSignal,
  ) {
    resultPreviewControllerRef.current?.abort();
    const controller = new AbortController();
    const abortFromParent = () => controller.abort();
    parentSignal?.addEventListener("abort", abortFromParent, { once: true });
    resultPreviewControllerRef.current = controller;
    setResultPreviewLoading(true);
    setResultPreviewError("");
    setResultPreviewUrl("");
    try {
      const blob = await getMediaAssetContent(asset.id, controller.signal);
      if (blob.size !== asset.sizeBytes) {
        throw new Error(
          "Trim preview size did not match its verified manifest.",
        );
      }
      if (blob.type && !blob.type.startsWith("video/")) {
        throw new Error("Trim result did not have a verified video content type.");
      }
      if (controller.signal.aborted) return;
      const nextUrl = URL.createObjectURL(blob);
      if (controller.signal.aborted) {
        URL.revokeObjectURL(nextUrl);
        return;
      }
      setResultPreviewUrl(nextUrl);
    } catch (caught) {
      if (!controller.signal.aborted && !isAbortError(caught)) {
        setResultPreviewError(formatProjectMasterError(caught));
      }
    } finally {
      parentSignal?.removeEventListener("abort", abortFromParent);
      if (resultPreviewControllerRef.current === controller) {
        resultPreviewControllerRef.current = null;
        if (!controller.signal.aborted) setResultPreviewLoading(false);
      }
    }
  }

  async function submitTrim(event: FormEvent) {
    event.preventDefault();
    if (
      !canTrim ||
      !projectId ||
      !selectedAsset ||
      startSeconds === undefined ||
      endSeconds === undefined
    ) {
      return;
    }
    trimControllerRef.current?.abort();
    resultPreviewControllerRef.current?.abort();
    const controller = new AbortController();
    trimControllerRef.current = controller;
    setTrimming(true);
    setTrimError("");
    setResult(undefined);
    setResultMessage("");
    setResultPreviewUrl("");
    setResultPreviewError("");
    try {
      const nextResult = await trimProjectVideo(
        projectId,
        selectedAsset.id,
        {
          startSeconds,
          endSeconds,
          outputName: outputName.trim() || undefined,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setResult(nextResult);
      setResultMessage(
        `${nextResult.name} was saved to ${project?.name ?? "project"} Media.`,
      );
      void loadResultPreview(nextResult, controller.signal);
    } catch (caught) {
      if (!controller.signal.aborted && !isAbortError(caught)) {
        setTrimError(formatProjectMasterError(caught));
      }
    } finally {
      if (trimControllerRef.current === controller) {
        trimControllerRef.current = null;
        if (!controller.signal.aborted) setTrimming(false);
      }
    }
  }

  if (!project) {
    return (
      <section className="video-trim video-trim--empty">
        <span className="video-trim__eyebrow">NON-DESTRUCTIVE EDIT</span>
        <h2>Choose a Creator project</h2>
        <p>
          The trim editor reads and writes verified media inside the active
          studio.
        </p>
      </section>
    );
  }

  return (
    <section
      className="video-trim"
      aria-labelledby="creator-video-trim-title"
      aria-busy={loading || previewLoading || trimming}
    >
      <header className="video-trim__header">
        <div>
          <span className="video-trim__eyebrow">NON-DESTRUCTIVE EDIT</span>
          <h2 id="creator-video-trim-title">Video trim</h2>
          <p>
            Select an in and out point. The source stays unchanged and the
            H.264/AAC result is added to <strong>{project.name}</strong> Media.
          </p>
        </div>
        <div className="video-trim__header-actions">
          <button
            ref={refreshSourcesButtonRef}
            className="button button--secondary"
            type="button"
            onClick={onViewMedia}
          >
            View Media
          </button>
          <button
            className="button button--secondary"
            type="button"
            disabled={loading || trimming}
            onClick={() => void load()}
          >
            {loading ? "Refreshing…" : "Refresh sources"}
          </button>
        </div>
      </header>

      <div className="video-trim__status">
        <span
          className={
            health?.available && health.ffmpegAvailable !== false
              ? "is-ready"
              : undefined
          }
        >
          {loading && health === null
            ? "CHECKING EDITOR"
            : health?.available === false
              ? "MEDIA STORE UNAVAILABLE"
              : health?.ffmpegAvailable === false
                ? "FFMPEG UNAVAILABLE"
                : health
                  ? "LOCAL EDITOR READY"
                  : "EDITOR STATUS UNKNOWN"}
        </span>
        <span>
          {videos.length} video source{videos.length === 1 ? "" : "s"}
        </span>
      </div>

      {health?.ffmpegAvailable === false ? (
        <div className="video-trim__alert" role="alert">
          <strong>FFmpeg is unavailable</strong>
          <span>
            Trimming stays disabled until Project Master can locate its bundled
            or system FFmpeg executable.
          </span>
        </div>
      ) : null}
      {health?.available === false ? (
        <div className="video-trim__alert" role="alert">
          <strong>Media store is unavailable</strong>
          <span>Restore the local media service before creating a trim.</span>
        </div>
      ) : null}
      {loadError ? (
        <div className="video-trim__alert" role="alert">
          <strong>Video sources could not be loaded</strong>
          <span>{loadError}</span>
          <button
            type="button"
            onClick={() => {
              void load().finally(() =>
                window.requestAnimationFrame(() =>
                  refreshSourcesButtonRef.current?.focus(),
                ),
              );
            }}
          >
            Retry
          </button>
        </div>
      ) : null}

      {loading && !videos.length ? (
        <div className="video-trim__loading" role="status">
          Loading verified video sources…
        </div>
      ) : !videos.length ? (
        <div className="video-trim__empty">
          <strong>No project video to trim</strong>
          <p>
            Import an MP4, WebM, MOV, or MKV source in Media, then return here
            to create a new cut.
          </p>
          <button
            className="button button--primary"
            type="button"
            onClick={onViewMedia}
          >
            Import video in Media
          </button>
        </div>
      ) : (
        <div className="video-trim__workspace">
          <form className="video-trim__controls" onSubmit={submitTrim}>
            <label className="video-trim__source">
              Source video
              <select
                value={selectedAssetId}
                disabled={trimming}
                onChange={(event) =>
                  setSelectedAssetId(event.currentTarget.value)
                }
              >
                {videos.map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.name}
                  </option>
                ))}
              </select>
              {selectedAsset ? (
                <small>{sourceDescription(selectedAsset)}</small>
              ) : null}
            </label>

            <fieldset className="video-trim__timeline">
              <legend>Trim range</legend>
              <div className="video-trim__number-grid">
                <div className="video-trim__point-control">
                  <label htmlFor="video-trim-start">Start</label>
                  <span className="video-trim__number-input">
                    <input
                      id="video-trim-start"
                      type="number"
                      min={0}
                      max={
                        knownDuration === undefined
                          ? undefined
                          : Math.max(
                              0,
                              knownDuration - TRIM_CONTROL_STEP_SECONDS,
                            )
                      }
                      step={TRIM_CONTROL_STEP_SECONDS}
                      value={startInput}
                      disabled={trimming}
                      onChange={(event) =>
                        setStartInput(event.currentTarget.value)
                      }
                    />
                    <span>sec</span>
                  </span>
                  <span
                    className="video-trim__nudge-controls"
                    role="group"
                    aria-label="Nudge trim start"
                  >
                    <button
                      type="button"
                      disabled={!pointActionsAvailable}
                      aria-label="Nudge trim start earlier by 0.10 seconds"
                      onClick={() =>
                        nudgePoint("start", -TRIM_NUDGE_SECONDS)
                      }
                    >
                      −0.10s
                    </button>
                    <button
                      type="button"
                      disabled={!pointActionsAvailable}
                      aria-label="Nudge trim start later by 0.10 seconds"
                      onClick={() =>
                        nudgePoint("start", TRIM_NUDGE_SECONDS)
                      }
                    >
                      +0.10s
                    </button>
                  </span>
                </div>
                <div className="video-trim__point-control">
                  <label htmlFor="video-trim-end">End</label>
                  <span className="video-trim__number-input">
                    <input
                      id="video-trim-end"
                      type="number"
                      min={TRIM_CONTROL_STEP_SECONDS}
                      max={knownDuration}
                      step={TRIM_CONTROL_STEP_SECONDS}
                      value={endInput}
                      disabled={trimming}
                      onChange={(event) =>
                        setEndInput(event.currentTarget.value)
                      }
                    />
                    <span>sec</span>
                  </span>
                  <span
                    className="video-trim__nudge-controls"
                    role="group"
                    aria-label="Nudge trim end"
                  >
                    <button
                      type="button"
                      disabled={!pointActionsAvailable}
                      aria-label="Nudge trim end earlier by 0.10 seconds"
                      onClick={() =>
                        nudgePoint("end", -TRIM_NUDGE_SECONDS)
                      }
                    >
                      −0.10s
                    </button>
                    <button
                      type="button"
                      disabled={!pointActionsAvailable}
                      aria-label="Nudge trim end later by 0.10 seconds"
                      onClick={() =>
                        nudgePoint("end", TRIM_NUDGE_SECONDS)
                      }
                    >
                      +0.10s
                    </button>
                  </span>
                </div>
              </div>
              <div className="video-trim__range-stack">
                <label>
                  <span>Start position</span>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(
                      0,
                      rangeDuration - TRIM_CONTROL_STEP_SECONDS,
                    )}
                    step={TRIM_CONTROL_STEP_SECONDS}
                    value={Math.min(
                      rangeStart,
                      Math.max(
                        0,
                        rangeDuration - TRIM_CONTROL_STEP_SECONDS,
                      ),
                    )}
                    disabled={!rangeAvailable || trimming}
                    onChange={(event) => {
                      const next = event.currentTarget.valueAsNumber;
                      setStartInput(controlValue(next));
                      if (rangeEnd <= next) {
                        setEndInput(
                          controlValue(
                            Math.min(
                              rangeDuration,
                              next + TRIM_CONTROL_STEP_SECONDS,
                            ),
                          ),
                        );
                      }
                    }}
                  />
                </label>
                <label>
                  <span>End position</span>
                  <input
                    type="range"
                    min={TRIM_CONTROL_STEP_SECONDS}
                    max={rangeDuration}
                    step={TRIM_CONTROL_STEP_SECONDS}
                    value={Math.max(TRIM_CONTROL_STEP_SECONDS, rangeEnd)}
                    disabled={!rangeAvailable || trimming}
                    onChange={(event) => {
                      const next = event.currentTarget.valueAsNumber;
                      setEndInput(controlValue(next));
                      if (rangeStart >= next) {
                        setStartInput(
                          controlValue(
                            Math.max(
                              0,
                              next - TRIM_CONTROL_STEP_SECONDS,
                            ),
                          ),
                        );
                      }
                    }}
                  />
                </label>
              </div>
              <output className="video-trim__span" aria-live="polite">
                {validationMessage ? (
                  validationMessage
                ) : (
                  <>
                    <strong>
                      {formatTime(startSeconds!)} → {formatTime(endSeconds!)}
                    </strong>
                    <span>{spanSeconds!.toFixed(2)} second output</span>
                  </>
                )}
              </output>
            </fieldset>

            <label>
              Output name
              <input
                value={outputName}
                maxLength={180}
                disabled={trimming}
                onChange={(event) => setOutputName(event.currentTarget.value)}
                placeholder="source-trim.mp4"
              />
              <small>Optional; MP4 is used for the verified output.</small>
            </label>

            <button
              className="button button--primary video-trim__submit"
              disabled={!canTrim}
            >
              {trimming ? "Creating verified trim…" : "Create trim"}
            </button>
            {trimError ? (
              <small className="video-trim__error" role="alert">
                {trimError}
              </small>
            ) : null}
          </form>

          <div className="video-trim__preview">
            <div className="video-trim__preview-heading">
              <span>SOURCE PREVIEW</span>
              {selectedAsset ? <strong>{selectedAsset.name}</strong> : null}
            </div>
            {previewLoading ? (
              <div className="video-trim__preview-state" role="status">
                Verifying and loading source…
              </div>
            ) : previewUrl ? (
              <>
                <video
                  ref={previewRef}
                  src={previewUrl}
                  controls
                  preload="metadata"
                  aria-label={`Source preview for ${selectedAsset?.name ?? "video"}`}
                  onError={() =>
                    setPreviewError(
                      "The browser could not decode this source preview.",
                    )
                  }
                  onLoadedMetadata={(event) => {
                    const detected = finiteDuration(
                      event.currentTarget.duration,
                    );
                    if (detected === undefined) return;
                    setPreviewDuration(detected);
                    if (assetDuration === undefined) {
                      setEndInput((current) =>
                        current || controlValue(detected),
                      );
                    }
                  }}
                />
                <div
                  className="video-trim__playhead-controls"
                  role="group"
                  aria-label="Set trim points from source preview playhead"
                >
                  <button
                    className="button button--secondary"
                    type="button"
                    disabled={!playheadActionsAvailable}
                    onClick={() => setPointFromPlayhead("start")}
                  >
                    Set in from playhead
                  </button>
                  <button
                    className="button button--secondary"
                    type="button"
                    disabled={!playheadActionsAvailable}
                    onClick={() => setPointFromPlayhead("end")}
                  >
                    Set out from playhead
                  </button>
                </div>
                <small className="video-trim__playhead-note">
                  Uses the browser preview position. The numeric in/out values
                  remain authoritative.
                </small>
              </>
            ) : (
              <div className="video-trim__preview-state">
                Source preview is unavailable.
              </div>
            )}
            {previewError ? (
              <small className="video-trim__error" role="alert">
                {previewError}
              </small>
            ) : null}
          </div>
        </div>
      )}

      {result ? (
        <article
          ref={resultArticleRef}
          className="video-trim__result"
          aria-live="polite"
          tabIndex={-1}
        >
          <div className="video-trim__result-copy">
            <span>TRIM COMPLETE</span>
            <h3>{result.name}</h3>
            <p>{resultMessage}</p>
            {result.derivation ? (
              <small>
                {formatTime(result.derivation.startSeconds)} →{" "}
                {formatTime(result.derivation.endSeconds)} ·{" "}
                {result.derivation.recipe}
              </small>
            ) : null}
            <div className="video-trim__result-actions">
              <button
                className="button button--primary"
                type="button"
                onClick={onViewMedia}
              >
                View in Media
              </button>
              {!resultPreviewUrl && !resultPreviewLoading ? (
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => {
                    void loadResultPreview(result).finally(() =>
                      window.requestAnimationFrame(() =>
                        resultArticleRef.current?.focus(),
                      ),
                    );
                  }}
                >
                  Retry preview
                </button>
              ) : null}
            </div>
          </div>
          <div className="video-trim__result-preview">
            {resultPreviewLoading ? (
              <div role="status">Loading verified output preview…</div>
            ) : resultPreviewUrl ? (
              <video
                src={resultPreviewUrl}
                controls
                preload="metadata"
                aria-label={`Trim result preview for ${result.name}`}
                onError={() =>
                  setResultPreviewError(
                    "The browser could not decode this trim preview.",
                  )
                }
              />
            ) : (
              <div>Output is saved even though its preview is not loaded.</div>
            )}
            {resultPreviewError ? (
              <small className="video-trim__error" role="alert">
                Preview: {resultPreviewError}
              </small>
            ) : null}
          </div>
        </article>
      ) : null}
    </section>
  );
}
