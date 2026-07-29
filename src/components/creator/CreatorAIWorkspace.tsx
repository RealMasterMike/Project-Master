import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { useAppPreferences } from "../../hooks/useAppPreferences";
import {
  createComfyJob,
  formatProjectMasterError,
  getComfyWorkflowCompatibility,
  getMediaAssetContent,
  isAbortError,
  listProjectMediaAssets,
  refreshComfyJob,
  type ComfyJobSummary,
  type ComfyOverview,
  type ComfyWorkflowBinding,
  type ComfyWorkflowCompatibility,
  type MasterProject,
  type MediaAssetSummary,
} from "../../lib/projectMasterApi";
import { ComfyArtifactCard } from "./ComfyArtifactCard";
import {
  approvedWorkflowsForOperation,
  automaticCreatorWorkflowId,
  coerceCreatorBindingValue,
  comfyJobStatusIsTerminal,
  comfyJobStatusShouldAutoPoll,
  CREATOR_OPERATION_DEFINITIONS,
  creatorJobValuesValid,
  creatorOperationsForIntent,
  creatorPromptBinding,
  imageAssetBindings,
  initialCreatorJobValues,
  selectedCreatorWorkflowId,
  type CreatorIntent,
  type CreatorOperation,
} from "./creatorWorkflowModes";
import { shouldAutoLoadPreview } from "./mediaPreview";
import { useViewportMediaPreview } from "./useViewportMediaPreview";

const JOB_POLL_INTERVAL_MS = 2_500;

interface CreatorAIWorkspaceProps {
  intent: CreatorIntent;
  project?: MasterProject;
  overview: ComfyOverview | null;
  selectedProfile: string;
  onSelectProfile: (profileId: string) => void;
  onRefreshOverview: () => Promise<void>;
  onViewMedia: () => void;
  onOpenWorkflows: () => void;
}

interface ActiveRunContext {
  operation: CreatorOperation;
  workflowName: string;
}

function defaultOperation(
  intent: CreatorIntent,
  output: "image" | "video",
): CreatorOperation {
  if (intent === "create") {
    return output === "image" ? "text-to-image" : "text-to-video";
  }
  return output === "image" ? "image-to-image" : "image-to-video";
}

function operationActionLabel(operation: CreatorOperation): string {
  if (operation === "text-to-image") return "Generate image";
  if (operation === "image-to-image") return "Edit image with AI";
  if (operation === "text-to-video") return "Generate video";
  return "Animate image";
}

function CreatorImageSourcePreview({
  asset,
}: {
  asset: MediaAssetSummary;
}) {
  const preferences = useAppPreferences();
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
      "image",
      true,
      preferences.autoLoadMediaPreviews,
    ),
    expectedSize: asset.sizeBytes,
    loadBlob: (signal) => getMediaAssetContent(asset.id, signal),
    sizeMismatchMessage:
      "Downloaded source size did not match its verified media manifest.",
  });

  return (
    <article className="creator-ai-source-preview" ref={cardRef}>
      <header>
        <span>SELECTED SOURCE</span>
        <strong title={asset.name}>{asset.name}</strong>
      </header>
      {loading ? (
        <div className="creator-media-preview-state is-image" role="status">
          Loading verified image preview…
        </div>
      ) : url ? (
        <img
          src={url}
          alt={`Selected source ${asset.name}`}
          loading="lazy"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this source image preview.",
            )
          }
        />
      ) : (
        <div className="creator-media-preview-state is-image">
          Source preview is not loaded.
        </div>
      )}
      <div className="creator-ai-source-preview__meta">
        <span>{asset.mediaType}</span>
        <span>
          {asset.width !== undefined && asset.height !== undefined
            ? `${asset.width} × ${asset.height}`
            : "Dimensions unavailable"}
        </span>
      </div>
      <div className="decision-actions">
        {!url ? (
          <button
            className="button button--secondary"
            type="button"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? "Loading…" : error ? "Retry preview" : "Load preview"}
          </button>
        ) : (
          <button
            className="button button--secondary"
            type="button"
            onClick={release}
          >
            Release preview
          </button>
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

export function CreatorAIWorkspace({
  intent,
  project,
  overview,
  selectedProfile,
  onSelectProfile,
  onRefreshOverview,
  onViewMedia,
  onOpenWorkflows,
}: CreatorAIWorkspaceProps) {
  const preferences = useAppPreferences();
  const [operation, setOperation] = useState<CreatorOperation>(() =>
    defaultOperation(intent, preferences.creatorGenerationDefault),
  );
  const [selectedWorkflow, setSelectedWorkflow] = useState("");
  const [jobValues, setJobValues] = useState<Record<string, unknown>>({});
  const [images, setImages] = useState<MediaAssetSummary[]>([]);
  const [selectedImageAssetId, setSelectedImageAssetId] = useState("");
  const [loadingImages, setLoadingImages] = useState(false);
  const [imageError, setImageError] = useState("");
  const [compatibility, setCompatibility] =
    useState<ComfyWorkflowCompatibility | null>(null);
  const [checkingCompatibility, setCheckingCompatibility] = useState(false);
  const [compatibilityError, setCompatibilityError] = useState("");
  const [compatibilityCheckVersion, setCompatibilityCheckVersion] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [runError, setRunError] = useState("");
  const [activeJob, setActiveJob] = useState<ComfyJobSummary>();
  const [activeRunContext, setActiveRunContext] =
    useState<ActiveRunContext>();
  const [polling, setPolling] = useState(false);
  const [pollError, setPollError] = useState("");
  const pollControllerRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(
    null,
  );
  const pollSequenceRef = useRef(0);
  const submitSequenceRef = useRef(0);
  const projectId = project?.id;
  const operationDefinition = CREATOR_OPERATION_DEFINITIONS[operation];
  const requiresImage = operationDefinition.input === "image";
  const availableOperations = creatorOperationsForIntent(intent);
  const matchingWorkflows = approvedWorkflowsForOperation(
    overview?.workflows ?? [],
    operation,
  );
  const curatedWorkflows = matchingWorkflows.filter(
    (workflow) => workflow.curatedDefault,
  );
  const manualWorkflows = matchingWorkflows.filter(
    (workflow) => !workflow.curatedDefault,
  );
  const automaticWorkflowId = automaticCreatorWorkflowId(matchingWorkflows);
  const selectedWorkflowSummary = matchingWorkflows.find(
    (workflow) => workflow.id === selectedWorkflow,
  );
  const promptBinding = creatorPromptBinding(selectedWorkflowSummary);
  const sourceBindings = imageAssetBindings(selectedWorkflowSummary);
  const sourceBinding = sourceBindings[0];
  const selectedImage = images.find(
    (asset) => asset.id === selectedImageAssetId,
  );

  const cancelPolling = useCallback((updateState = true) => {
    pollSequenceRef.current += 1;
    pollControllerRef.current?.abort();
    pollControllerRef.current = null;
    if (pollTimerRef.current !== null) {
      globalThis.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (updateState) setPolling(false);
  }, []);

  const startPolling = useCallback(
    (jobId: string) => {
      cancelPolling();
      const sequence = pollSequenceRef.current;
      setPolling(true);
      setPollError("");

      const poll = async () => {
        if (pollSequenceRef.current !== sequence) return;
        const controller = new AbortController();
        pollControllerRef.current = controller;
        try {
          const nextJob = await refreshComfyJob(jobId, controller.signal);
          if (
            controller.signal.aborted ||
            pollSequenceRef.current !== sequence
          ) {
            return;
          }
          setActiveJob(nextJob);
          if (
            comfyJobStatusIsTerminal(nextJob.status) ||
            !comfyJobStatusShouldAutoPoll(nextJob.status)
          ) {
            pollControllerRef.current = null;
            setPolling(false);
            void onRefreshOverview().catch(() => undefined);
            return;
          }
          pollControllerRef.current = null;
          pollTimerRef.current = globalThis.setTimeout(() => {
            pollTimerRef.current = null;
            void poll();
          }, JOB_POLL_INTERVAL_MS);
        } catch (caught) {
          if (
            controller.signal.aborted ||
            pollSequenceRef.current !== sequence ||
            isAbortError(caught)
          ) {
            return;
          }
          pollControllerRef.current = null;
          setPolling(false);
          setPollError(formatProjectMasterError(caught));
        }
      };

      void poll();
    },
    [cancelPolling, onRefreshOverview],
  );

  useEffect(() => {
    setOperation(
      defaultOperation(intent, preferences.creatorGenerationDefault),
    );
  }, [intent, preferences.creatorGenerationDefault]);

  useEffect(() => {
    setSelectedWorkflow((current) =>
      selectedCreatorWorkflowId(matchingWorkflows, current),
    );
    // Workflow revisions are immutable, so the overview collection and
    // operation fully describe the available selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation, overview?.workflows]);

  useEffect(() => {
    setJobValues(initialCreatorJobValues(selectedWorkflowSummary));
  }, [selectedWorkflowSummary]);

  useEffect(() => {
    const controller = new AbortController();
    setImages([]);
    setSelectedImageAssetId("");
    setImageError("");
    setLoadingImages(false);
    if (!projectId || !requiresImage) return () => controller.abort();

    setLoadingImages(true);
    void listProjectMediaAssets(projectId, controller.signal)
      .then((assets) => {
        if (controller.signal.aborted) return;
        const nextImages = assets.filter(
          (asset) =>
            asset.kind === "image" &&
            asset.mediaType.toLocaleLowerCase().startsWith("image/"),
        );
        setImages(nextImages);
        setSelectedImageAssetId(nextImages[0]?.id ?? "");
      })
      .catch((caught) => {
        if (!controller.signal.aborted && !isAbortError(caught)) {
          setImageError(formatProjectMasterError(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingImages(false);
      });
    return () => controller.abort();
  }, [projectId, requiresImage]);

  useEffect(() => {
    const controller = new AbortController();
    setCompatibility(null);
    setCompatibilityError("");
    setCheckingCompatibility(false);
    if (!selectedProfile || !selectedWorkflowSummary) {
      return () => controller.abort();
    }
    setCheckingCompatibility(true);
    void getComfyWorkflowCompatibility(
      selectedProfile,
      selectedWorkflowSummary.id,
      controller.signal,
    )
      .then((result) => {
        if (controller.signal.aborted) return;
        if (
          result.profileId !== selectedProfile ||
          result.workflowRevisionId !== selectedWorkflowSummary.id
        ) {
          throw new Error(
            "ComfyUI compatibility response did not match the selected profile and workflow.",
          );
        }
        setCompatibility(result);
      })
      .catch((caught) => {
        if (!controller.signal.aborted && !isAbortError(caught)) {
          setCompatibilityError(formatProjectMasterError(caught));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCheckingCompatibility(false);
      });
    return () => controller.abort();
  }, [
    compatibilityCheckVersion,
    selectedProfile,
    selectedWorkflowSummary,
  ]);

  useEffect(() => {
    submitSequenceRef.current += 1;
    cancelPolling();
    setSubmitting(false);
    setRunError("");
    setPollError("");
    setActiveJob(undefined);
    setActiveRunContext(undefined);
  }, [cancelPolling, intent, projectId]);

  useEffect(
    () => () => {
      submitSequenceRef.current += 1;
      cancelPolling(false);
    },
    [cancelPolling],
  );

  const compatibilityConfirmed =
    compatibility?.compatible === true &&
    compatibility.profileId === selectedProfile &&
    compatibility.workflowRevisionId === selectedWorkflowSummary?.id;
  const missingResources = compatibility?.missingResources ?? [];
  const snapshotValues = {
    ...jobValues,
    ...(requiresImage && sourceBinding
      ? { [sourceBinding.id]: selectedImageAssetId }
      : {}),
  };
  const promptValue = promptBinding
    ? String(snapshotValues[promptBinding.id] ?? "")
    : "";
  const sourceContractReady =
    !requiresImage ||
    (sourceBindings.length === 1 &&
      Boolean(selectedImage) &&
      selectedImage?.id === selectedImageAssetId);
  const formReady =
    Boolean(projectId) &&
    Boolean(selectedProfile) &&
    Boolean(selectedWorkflowSummary) &&
    Boolean(promptBinding) &&
    Boolean(promptValue.trim()) &&
    sourceContractReady &&
    creatorJobValuesValid(selectedWorkflowSummary, snapshotValues) &&
    compatibilityConfirmed &&
    !checkingCompatibility &&
    !submitting;
  const advancedBindings =
    selectedWorkflowSummary?.bindings.filter(
      (binding) =>
        binding.id !== promptBinding?.id &&
        binding.valueType !== "image_asset",
    ) ?? [];
  const verifiedArtifacts =
    activeJob?.artifacts.filter(
      (artifact) =>
        artifact.verified &&
        /^(?:image|audio|video)\//.test(artifact.mediaType),
    ) ?? [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !formReady ||
      !projectId ||
      !selectedWorkflowSummary ||
      !promptBinding
    ) {
      return;
    }
    const submitSequence = submitSequenceRef.current + 1;
    submitSequenceRef.current = submitSequence;
    cancelPolling();
    setSubmitting(true);
    setRunError("");
    setPollError("");
    setActiveJob(undefined);
    setActiveRunContext(undefined);
    const input = {
      profileId: selectedProfile,
      workflowRevisionId: selectedWorkflowSummary.id,
      projectId,
      values: { ...snapshotValues },
    };
    try {
      const created = await createComfyJob(input);
      if (submitSequenceRef.current !== submitSequence) return;
      setActiveJob(created);
      setActiveRunContext({
        operation,
        workflowName: selectedWorkflowSummary.name,
      });
      if (comfyJobStatusShouldAutoPoll(created.status)) {
        startPolling(created.id);
      } else {
        void onRefreshOverview().catch(() => undefined);
      }
    } catch (caught) {
      if (submitSequenceRef.current === submitSequence) {
        setRunError(formatProjectMasterError(caught));
      }
    } finally {
      if (submitSequenceRef.current === submitSequence) {
        setSubmitting(false);
      }
    }
  }

  if (!project) {
    return (
      <section className="creator-ai creator-ai--empty">
        <span className="creator-ai__eyebrow">
          {intent === "create" ? "PROMPT-DRIVEN CREATION" : "AI EDITING"}
        </span>
        <h2>Choose a Creator project</h2>
        <p>
          AI creation and editing keep source media and generated results
          inside the active studio.
        </p>
      </section>
    );
  }

  return (
    <section
      className="creator-ai"
      aria-labelledby={`creator-ai-${intent}-title`}
      aria-busy={submitting || polling || undefined}
    >
      <header className="creator-ai__header">
        <div>
          <span className="creator-ai__eyebrow">
            {intent === "create" ? "PROMPT-DRIVEN CREATION" : "AI EDITING"}
          </span>
          <h2 id={`creator-ai-${intent}-title`}>
            {intent === "create" ? "Create with AI" : "AI editor"}
          </h2>
          <p>
            {intent === "create"
              ? `Create new images or video for ${project.name} with an approved local workflow.`
              : `Transform or animate a verified image from ${project.name} with an approved local workflow.`}
          </p>
        </div>
        <button
          className="button button--secondary"
          type="button"
          onClick={onViewMedia}
        >
          Open Media
        </button>
      </header>

      <form className="creator-ai__form" onSubmit={submit}>
        <fieldset className="creator-operation-picker">
          <legend>
            {intent === "create" ? "Choose an output" : "Choose an AI edit"}
          </legend>
          <div>
            {availableOperations.map((item) => {
              const definition = CREATOR_OPERATION_DEFINITIONS[item];
              return (
                <label
                  className={`creator-operation-card is-${definition.output} ${
                    operation === item ? "is-selected" : ""
                  }`}
                  key={item}
                >
                  <input
                    type="radio"
                    name={`creator-operation-${intent}`}
                    value={item}
                    checked={operation === item}
                    disabled={submitting}
                    onChange={() => setOperation(item)}
                  />
                  <span>
                    <strong>{definition.label}</strong>
                    <small>{definition.description}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <div className="creator-ai__workspace">
          <div className="creator-ai__controls">
            {requiresImage ? (
              <div className="creator-ai__source-control">
                <label htmlFor={`creator-ai-source-${intent}`}>
                  Verified source image
                  <select
                    id={`creator-ai-source-${intent}`}
                    value={selectedImageAssetId}
                    disabled={loadingImages || submitting || !images.length}
                    onChange={(event) =>
                      setSelectedImageAssetId(event.currentTarget.value)
                    }
                    required
                  >
                    <option value="">
                      {loadingImages
                        ? "Loading project images…"
                        : "Choose a project image"}
                    </option>
                    {images.map((asset) => (
                      <option value={asset.id} key={asset.id}>
                        {asset.name}
                      </option>
                    ))}
                  </select>
                </label>
                <small>
                  Only the selected project media ID is submitted. Arbitrary
                  filesystem paths are never accepted.
                </small>
                {imageError ? (
                  <div className="creator-ai__alert" role="alert">
                    <strong>Project images could not be loaded</strong>
                    <span>{imageError}</span>
                  </div>
                ) : null}
                {!loadingImages && !imageError && !images.length ? (
                  <div className="comfy-generation-blocker" role="note">
                    <strong>No verified project images</strong>
                    <p>
                      Import an image in Media before using an image-driven
                      workflow.
                    </p>
                    <button
                      className="button button--secondary"
                      type="button"
                      onClick={onViewMedia}
                    >
                      Import image in Media
                    </button>
                  </div>
                ) : null}
                {sourceBindings.length > 1 ? (
                  <div className="creator-ai__alert" role="alert">
                    <strong>Workflow needs multiple source images</strong>
                    <span>
                      The focused AI editor supports one verified image input
                      per workflow revision.
                    </span>
                  </div>
                ) : null}
              </div>
            ) : null}

            {promptBinding ? (
              <label className="creator-ai__prompt">
                Prompt
                <textarea
                  rows={5}
                  value={String(jobValues[promptBinding.id] ?? "")}
                  disabled={submitting}
                  placeholder={
                    requiresImage
                      ? "Describe the transformation or motion you want"
                      : "Describe what you want to create"
                  }
                  onChange={(event) =>
                    setJobValues((current) => ({
                      ...current,
                      [promptBinding.id]: event.currentTarget.value,
                    }))
                  }
                  required
                />
                <small>
                  {promptBinding.id} → node {promptBinding.nodeId}.
                  {promptBinding.inputName}
                </small>
              </label>
            ) : selectedWorkflowSummary ? (
              <div className="creator-ai__alert" role="alert">
                <strong>This workflow does not expose a prompt</strong>
                <span>
                  Import a revision with a validated text prompt binding before
                  using it in the AI workspace.
                </span>
              </div>
            ) : null}

            <label>
              Approved local workflow
              <select
                value={selectedWorkflow}
                disabled={!matchingWorkflows.length || submitting}
                onChange={(event) =>
                  setSelectedWorkflow(event.currentTarget.value)
                }
              >
                {!matchingWorkflows.length ? (
                  <option value="">No matching approved workflows</option>
                ) : null}
                {curatedWorkflows.length ? (
                  <optgroup label="Curated defaults">
                    {curatedWorkflows.map((workflow) => (
                      <option value={workflow.id} key={workflow.id}>
                        {workflow.id === automaticWorkflowId
                          ? `${workflow.name} — automatic`
                          : workflow.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {manualWorkflows.length ? (
                  <optgroup label="Manual / provenance unverified">
                    {manualWorkflows.map((workflow) => (
                      <option value={workflow.id} key={workflow.id}>
                        {workflow.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
              {selectedWorkflowSummary ? (
                <small>
                  {selectedWorkflowSummary.curatedDefault
                    ? selectedWorkflowSummary.id === automaticWorkflowId
                      ? "Curated Project Master default with documented model provenance. Chosen automatically."
                      : "Curated Project Master workflow with documented model provenance."
                    : "Manual workflow — Project Master has not verified its model or content-policy provenance."}
                </small>
              ) : null}
            </label>
            {!matchingWorkflows.length ? (
              <div className="comfy-generation-blocker" role="note">
                <strong>
                  No approved {operationDefinition.label} workflows
                </strong>
                <p>
                  Import, review, and approve a matching immutable ComfyUI
                  workflow revision before creating.
                </p>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={onOpenWorkflows}
                >
                  Open workflow setup
                </button>
              </div>
            ) : null}

            <label>
              ComfyUI profile
              <select
                value={selectedProfile}
                disabled={submitting || !overview?.profiles.length}
                onChange={(event) =>
                  onSelectProfile(event.currentTarget.value)
                }
              >
                {!overview?.profiles.length ? (
                  <option value="">No configured profile</option>
                ) : null}
                {overview?.profiles.map((profile) => (
                  <option value={profile.id} key={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>

            {selectedWorkflowSummary ? (
              <div
                className={`comfy-compatibility ${
                  checkingCompatibility
                    ? "is-checking"
                    : compatibilityError
                      ? "is-error"
                      : compatibility?.compatible === false
                        ? "is-incompatible"
                        : compatibilityConfirmed
                          ? "is-compatible"
                          : ""
                }`}
                role={
                  compatibilityError || compatibility?.compatible === false
                    ? "alert"
                    : "status"
                }
                aria-live="polite"
              >
                <div>
                  <strong>
                    {checkingCompatibility
                      ? "Checking node types"
                      : compatibilityError
                        ? "Compatibility check failed"
                        : compatibility?.compatible === false
                          ? missingResources.length
                            ? "Required model files are missing"
                            : "Required nodes are missing"
                          : compatibilityConfirmed
                            ? "Workflow requirements are present"
                            : "Node compatibility not checked"}
                  </strong>
                  <span>
                    {checkingCompatibility
                      ? "Comparing this immutable graph with the selected ComfyUI endpoint…"
                      : compatibilityError
                        ? compatibilityError
                        : compatibility?.compatible === false
                          ? [
                              compatibility.missingNodeTypes.length
                                ? `Node types: ${compatibility.missingNodeTypes.join(", ")}`
                                : "",
                              missingResources.length
                                ? `Model files: ${missingResources
                                    .map((item) => item.resourceName)
                                    .join(", ")}`
                                : "",
                            ]
                              .filter(Boolean)
                              .join(". ") ||
                            "ComfyUI did not report the missing requirement."
                          : compatibilityConfirmed
                            ? "The selected endpoint reports every node class and audited fixed model file used by this graph."
                            : "Choose a profile and workflow to run the preflight."}
                  </span>
                </div>
                <button
                  type="button"
                  disabled={
                    checkingCompatibility ||
                    !selectedProfile ||
                    !selectedWorkflowSummary
                  }
                  onClick={() =>
                    setCompatibilityCheckVersion((current) => current + 1)
                  }
                >
                  {checkingCompatibility ? "Checking…" : "Recheck"}
                </button>
                {compatibilityConfirmed ? (
                  <small>
                    Dynamic inputs and arbitrary third-party loader resources
                    remain subject to the rendered submission preflight.
                  </small>
                ) : null}
              </div>
            ) : null}

            {advancedBindings.length ? (
              <details className="creator-ai__advanced">
                <summary>
                  Advanced workflow controls
                  <span>{advancedBindings.length}</span>
                </summary>
                <div>
                  {advancedBindings.map((binding) => (
                    <CreatorAdvancedBinding
                      binding={binding}
                      disabled={submitting}
                      key={binding.id}
                      value={jobValues[binding.id]}
                      onChange={(value) =>
                        setJobValues((current) => ({
                          ...current,
                          [binding.id]: value,
                        }))
                      }
                    />
                  ))}
                </div>
              </details>
            ) : null}

            <button
              className="button button--primary creator-ai__submit"
              disabled={!formReady}
            >
              {submitting
                ? "Submitting approved workflow…"
                : checkingCompatibility
                  ? "Checking workflow nodes…"
                  : operationActionLabel(operation)}
            </button>
            {runError ? (
              <small className="artifact-error" role="alert">
                {runError}
              </small>
            ) : null}
          </div>

          <div className="creator-ai__visual">
            {requiresImage && selectedImage ? (
              <CreatorImageSourcePreview
                asset={selectedImage}
                key={selectedImage.id}
              />
            ) : (
              <div className="creator-ai__prompt-visual">
                <span>{operationDefinition.label}</span>
                <strong>
                  {requiresImage
                    ? "Choose a verified source image"
                    : "Your prompt starts the workflow"}
                </strong>
                <p>{operationDefinition.description}.</p>
              </div>
            )}
          </div>
        </div>
      </form>

      {activeJob ? (
        <article
          className={`creator-ai-result is-${activeJob.status}`}
          aria-busy={polling || undefined}
        >
          <header>
            <div>
              <span>LATEST AI RUN</span>
              <h3>
                {activeRunContext?.workflowName ??
                  activeJob.workflowRevisionId}
              </h3>
              <p>
                {CREATOR_OPERATION_DEFINITIONS[
                  activeRunContext?.operation ?? operation
                ].label}
              </p>
            </div>
            <strong
              className="creator-ai-result__status"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              {polling ? `${activeJob.status} · updating` : activeJob.status}
            </strong>
          </header>
          {activeJob.error ? (
            <div className="creator-ai__alert" role="alert">
              <strong>Generation failed</strong>
              <span>{activeJob.error}</span>
            </div>
          ) : null}
          {activeJob.artifactError ? (
            <div className="creator-ai__alert" role="alert">
              <strong>Some output could not be imported</strong>
              <span>{activeJob.artifactError}</span>
            </div>
          ) : null}
          {pollError ? (
            <div className="creator-ai__alert" role="alert">
              <strong>Automatic status update paused</strong>
              <span>{pollError}</span>
            </div>
          ) : null}
          {verifiedArtifacts.length ? (
            <div className="comfy-artifact-gallery">
              {verifiedArtifacts.map((artifact) => (
                <ComfyArtifactCard
                  jobId={activeJob.id}
                  artifact={artifact}
                  key={artifact.id}
                />
              ))}
            </div>
          ) : activeJob.status === "succeeded" ? (
            <div className="comfy-video-warning" role="alert">
              <strong>No verified media output</strong>
              <span>
                The workflow completed, but no browser-previewable verified
                artifact was persisted.
              </span>
            </div>
          ) : (
            <p className="creator-ai-result__progress">
              {activeJob.status === "orphaned"
                ? "The remote submission needs a manual status refresh."
                : "Project Master is waiting for verified local output."}
            </p>
          )}
          <div className="creator-ai-result__actions">
            {activeJob.status === "succeeded" && verifiedArtifacts.length ? (
              <button
                className="button button--primary"
                type="button"
                onClick={onViewMedia}
              >
                Open result in Media
              </button>
            ) : null}
            {!polling && !comfyJobStatusIsTerminal(activeJob.status) ? (
              <button
                className="button button--secondary"
                type="button"
                onClick={() => startPolling(activeJob.id)}
              >
                Refresh status
              </button>
            ) : null}
          </div>
        </article>
      ) : null}
    </section>
  );
}

function CreatorAdvancedBinding({
  binding,
  value,
  disabled,
  onChange,
}: {
  binding: ComfyWorkflowBinding;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
}) {
  const label = binding.description || binding.id;
  return (
    <label>
      {label}
      {binding.valueType === "boolean" ? (
        <input
          type="checkbox"
          checked={Boolean(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
      ) : binding.valueType === "enum" ? (
        <select
          value={String(value ?? "")}
          disabled={disabled}
          required={binding.required}
          onChange={(event) =>
            onChange(
              coerceCreatorBindingValue(binding, event.currentTarget.value),
            )
          }
        >
          <option value="">Choose…</option>
          {binding.choices.map((choice) => (
            <option value={String(choice)} key={String(choice)}>
              {String(choice)}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={
            ["integer", "number"].includes(binding.valueType)
              ? "number"
              : "text"
          }
          step={binding.valueType === "integer" ? 1 : undefined}
          min={binding.minimum}
          max={binding.maximum}
          value={String(value ?? "")}
          disabled={disabled}
          required={binding.required}
          onChange={(event) =>
            onChange(
              coerceCreatorBindingValue(binding, event.currentTarget.value),
            )
          }
        />
      )}
      <small>
        {binding.id} → node {binding.nodeId}.{binding.inputName}
      </small>
    </label>
  );
}
