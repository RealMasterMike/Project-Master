import { useEffect, useRef, useState } from "react";

import type {
  MasterProject,
  ProjectMasterChatMode,
  ProjectMasterModel,
} from "../lib/projectMasterApi";
import {
  isCuratedTeamModel,
  isCuratedUncensoredChatModel,
  isVisionCapableModel,
} from "../lib/projectMasterApi";

interface ChatSessionToolbarProps {
  mode: ProjectMasterChatMode;
  onModeChange: (mode: ProjectMasterChatMode) => void;
  teamAvailable: boolean;
  models: ProjectMasterModel[];
  selectedModel: string;
  onModelChange: (model: string) => void;
  projects: MasterProject[];
  selectedProjectId: string;
  onProjectChange: (projectId: string) => void;
  allowMutations: boolean;
  onAllowMutationsChange: (allowed: boolean) => void;
  allowWebSearch: boolean;
  onAllowWebSearchChange: (allowed: boolean) => void;
  requiresVision: boolean;
  isBusy: boolean;
  activityCount: number;
  railAvailable: boolean;
  railOpen: boolean;
  onToggleRail: () => void;
}

export function ChatSessionToolbar({
  mode,
  onModeChange,
  teamAvailable,
  models,
  selectedModel,
  onModelChange,
  projects,
  selectedProjectId,
  onProjectChange,
  allowMutations,
  onAllowMutationsChange,
  allowWebSearch,
  onAllowWebSearchChange,
  requiresVision,
  isBusy,
  activityCount,
  railAvailable,
  railOpen,
  onToggleRail,
}: ChatSessionToolbarProps) {
  const [contextOpen, setContextOpen] = useState(false);
  const contextRef = useRef<HTMLDivElement | null>(null);
  const contextTriggerRef = useRef<HTMLButtonElement | null>(null);
  const contextCloseRef = useRef<HTMLButtonElement | null>(null);
  const selectedModelInfo = models.find((model) => model.name === selectedModel);
  const selectableModels =
    mode === "team" ? models.filter(isCuratedTeamModel) : models;
  const selectedProject = projects.find(
    (project) => project.id === selectedProjectId,
  );

  useEffect(() => {
    if (!contextOpen) return;
    const focusFrame = window.requestAnimationFrame(() =>
      contextCloseRef.current?.focus(),
    );
    const onPointerDown = (event: PointerEvent) => {
      if (!contextRef.current?.contains(event.target as Node)) {
        setContextOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setContextOpen(false);
      window.requestAnimationFrame(() => contextTriggerRef.current?.focus());
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [contextOpen]);

  return (
    <div className="chat-toolbar" aria-label="Chat session controls">
      <div className="mode-control" role="group" aria-label="Chat mode">
        <button
          className={mode === "direct" ? "is-active" : undefined}
          type="button"
          aria-pressed={mode === "direct"}
          onClick={() => onModeChange("direct")}
          disabled={isBusy}
        >
          Direct
        </button>
        <button
          className={mode === "team" ? "is-active" : undefined}
          type="button"
          aria-pressed={mode === "team"}
          title={
            requiresVision
              ? "Project image attachments are available in Direct mode only"
              : teamAvailable
              ? "Use the local model council and one authorized lead"
              : "The backend did not report a compatible team catalog"
          }
          onClick={() => onModeChange("team")}
          disabled={!teamAvailable || requiresVision || isBusy}
        >
          Team
        </button>
      </div>

      <label className="chat-toolbar__model" htmlFor="model-select">
        <span>{mode === "team" ? "Lead model" : "Model"}</span>
        <select
          id="model-select"
          value={selectedModel}
          onChange={(event) => onModelChange(event.currentTarget.value)}
          disabled={selectableModels.length === 0 || isBusy}
          aria-describedby={
            selectedModelInfo ? "model-readiness-description" : undefined
          }
        >
          {selectableModels.length === 0 ? (
            <option value="">
              {mode === "team"
                ? "No curated Team models available"
                : "No models available"}
            </option>
          ) : (
            selectableModels.map((model) => (
              <option
                key={model.name}
                value={model.name}
                disabled={
                  !model.conversational ||
                  (requiresVision && !isVisionCapableModel(model))
                }
              >
                {model.name} —{" "}
                {mode === "team" && isCuratedTeamModel(model)
                  ? "curated automatic team · "
                  : isCuratedUncensoredChatModel(model)
                  ? "curated uncensored / abliterated · "
                  : "manual / unverified · "}
                {!model.conversational
                  ? "not chat-compatible"
                  : requiresVision && !isVisionCapableModel(model)
                    ? "no vision"
                    : isVisionCapableModel(model)
                      ? "vision"
                  : model.toolCapable
                    ? "chat + tools"
                    : model.capabilities.length
                      ? "chat only"
                      : "chat · capabilities unreported"}
              </option>
            ))
          )}
        </select>
      </label>

      {selectedModelInfo ? (
        <span
          className={`model-readiness ${
            selectedModelInfo.toolCapable ||
            isVisionCapableModel(selectedModelInfo)
              ? "is-ready"
              : ""
          }`}
          id="model-readiness-description"
          title={
            isVisionCapableModel(selectedModelInfo)
              ? "This installed model reports local image understanding."
              : selectedModelInfo.toolCapable
              ? "This model can use registered tools."
              : "Completion-only model; tool requests may not execute in Direct mode."
          }
        >
          {isVisionCapableModel(selectedModelInfo)
            ? "VISION READY"
            : selectedModelInfo.toolCapable
              ? "TOOLS READY"
              : "CHAT ONLY"}
        </span>
      ) : null}

      <div className="chat-context" ref={contextRef}>
        <button
          ref={contextTriggerRef}
          className={`chat-context__trigger ${
            allowMutations || allowWebSearch ? "is-enabled" : ""
          }`}
          type="button"
          aria-expanded={contextOpen}
          aria-controls="chat-context-panel"
          onClick={() => setContextOpen((current) => !current)}
        >
          <span>Context &amp; access</span>
          <small>
            {selectedProject ? "Binder attached" : "No Binder"}
            {" · "}
            {allowMutations ? "Changes on" : "Read only"}
            {" · "}
            {allowWebSearch ? "Online on" : "Local only"}
          </small>
          <span className="chat-context__chevron" aria-hidden="true">
            {contextOpen ? "▴" : "▾"}
          </span>
        </button>

        {contextOpen ? (
          <section
            className="chat-context__panel"
            id="chat-context-panel"
            aria-label="Context and tool access"
          >
            <header>
              <div>
                <span className="panel-kicker">CURRENT CONVERSATION</span>
                <h2>Context &amp; tool access</h2>
              </div>
              <button
                ref={contextCloseRef}
                className="icon-button"
                type="button"
                aria-label="Close context and access"
                onClick={() => {
                  setContextOpen(false);
                  window.requestAnimationFrame(() =>
                    contextTriggerRef.current?.focus(),
                  );
                }}
              >
                ×
              </button>
            </header>

            <label className="chat-context__field" htmlFor="binder-select">
              <span>Project Binder</span>
              <select
                id="binder-select"
                value={selectedProjectId}
                onChange={(event) =>
                  onProjectChange(event.currentTarget.value)
                }
                disabled={isBusy}
              >
                <option value="">No project context</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
              <small>
                Adds cited excerpts from the selected local project to this
                conversation.
              </small>
            </label>

            <label
              className={`access-card ${
                allowMutations ? "is-enabled" : ""
              }`}
            >
              <span>
                <strong>Allow project changes</strong>
                <small>
                  Off by default. This applies only to chat tool calls in the
                  current conversation.
                </small>
              </span>
              <input
                type="checkbox"
                checked={allowMutations}
                onChange={(event) =>
                  onAllowMutationsChange(event.currentTarget.checked)
                }
                disabled={isBusy}
              />
            </label>

            <label
              className={`access-card ${
                allowWebSearch ? "is-enabled" : ""
              }`}
            >
              <span>
                <strong>Allow web access</strong>
                <small>
                  Lets the model search through your configured SearXNG
                  service and read bounded text from public web pages. Off by
                  default and limited to this conversation.
                </small>
              </span>
              <input
                type="checkbox"
                checked={allowWebSearch}
                onChange={(event) =>
                  onAllowWebSearchChange(event.currentTarget.checked)
                }
                disabled={isBusy}
              />
            </label>

            <p className="chat-context__privacy">
              {allowWebSearch
                ? "The model may send search terms and public page URLs derived from this conversation or its selected Binder. Retrieved page text returns to the local model."
                : "Local files and prompts stay on this machine. Dashboard actions remain explicit."}
            </p>
          </section>
        ) : null}
      </div>

      {railAvailable ? (
        <button
          id="team-run-rail-toggle"
          className="rail-toggle"
          type="button"
          aria-expanded={railOpen}
          aria-controls="team-run-rail"
          onClick={onToggleRail}
        >
          Activity
          {activityCount ? <span>{activityCount}</span> : null}
        </button>
      ) : null}
    </div>
  );
}
