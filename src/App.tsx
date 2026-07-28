import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import { ConversationLibrary } from "./components/ConversationLibrary";
import { FeatureWorkspace } from "./components/FeatureWorkspace";
import { LayoutCustomizer } from "./components/LayoutCustomizer";
import { MissionView } from "./components/MissionView";
import { RunRail, TeamStrip } from "./components/TeamRunPanel";
import { UpdateNotice } from "./components/UpdateNotice";
import {
  WorkspaceNavigation,
  type MasterWorkspace,
} from "./components/WorkspaceNavigation";
import { useLayoutController } from "./hooks/useLayoutController";
import { withCurrentMutationAuthorization } from "./lib/chatAuthorization";
import {
  cancelChat,
  ensureManagedBackend,
  formatProjectMasterError,
  getCommunicationProfile,
  getConversation,
  getModelStatus,
  isAbortError,
  listConversations,
  listProjects,
  submitCommunicationFeedback,
  type CommunicationFeedbackCategory,
  type ProjectMasterChatMode,
  type ProjectMasterConversation,
  type ProjectMasterCommunicationProfile,
  ProjectMasterUnavailableError,
  type ProjectMasterRunActivity,
  type ProjectMasterTeamCatalogModel,
  type MasterProject,
  streamChat,
  type ProjectMasterModel,
} from "./lib/projectMasterApi";

type MessageStatus = "complete" | "streaming" | "stopped" | "error";
type ConnectionState = "checking" | "ready" | "empty" | "offline";

interface UiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  error?: string;
}

interface RetryRequest {
  model: string;
  message: string;
  mode: ProjectMasterChatMode;
  allowMutations: boolean;
  projectId?: string;
  conversationId?: string;
}

interface ActiveStream {
  controller: AbortController;
  requestId: string;
}

let nextMessageId = 0;

function createMessageId(role: UiMessage["role"]): string {
  nextMessageId += 1;
  return `${role}-${Date.now()}-${nextMessageId}`;
}

function App() {
  const layoutController = useLayoutController();
  const [models, setModels] = useState<ProjectMasterModel[]>([]);
  const [binderProjects, setBinderProjects] = useState<MasterProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [teamCatalog, setTeamCatalog] = useState<ProjectMasterTeamCatalogModel[]>([]);
  const [chatMode, setChatMode] = useState<ProjectMasterChatMode>("team");
  // Deliberately session-only: never read from or write to persistent storage.
  const [allowMutations, setAllowMutations] = useState(false);
  const [teamAvailable, setTeamAvailable] = useState(false);
  const [runActivities, setRunActivities] = useState<ProjectMasterRunActivity[]>([]);
  const [activeRunId, setActiveRunId] = useState<string>();
  const [teamView, setTeamView] = useState<"mission" | "transcript">("mission");
  const [activeWorkspace, setActiveWorkspace] = useState<MasterWorkspace>("chat");
  const [selectedModel, setSelectedModel] = useState("");
  const [conversationId, setConversationId] = useState<string>();
  const [conversations, setConversations] = useState<ProjectMasterConversation[]>([]);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [communicationProfile, setCommunicationProfile] =
    useState<ProjectMasterCommunicationProfile | null>(null);
  const [communicationLoading, setCommunicationLoading] = useState(false);
  const [communicationError, setCommunicationError] = useState<string | null>(null);
  const [contextLength, setContextLength] = useState(32768);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [composer, setComposer] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const modelLoadControllerRef = useRef<AbortController | null>(null);
  const conversationListControllerRef = useRef<AbortController | null>(null);
  const conversationLoadControllerRef = useRef<AbortController | null>(null);
  const communicationLoadControllerRef = useRef<AbortController | null>(null);
  const streamControllerRef = useRef<ActiveStream | null>(null);
  const retryRequestsRef = useRef(new Map<string, RetryRequest>());
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  const loadConversations = useCallback(async () => {
    conversationListControllerRef.current?.abort();
    const controller = new AbortController();
    conversationListControllerRef.current = controller;
    setConversationLoading(true);
    setConversationError(null);

    try {
      const availableConversations = await listConversations(controller.signal);
      if (!controller.signal.aborted) setConversations(availableConversations);
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) {
        setConversationError(formatProjectMasterError(error));
      }
    } finally {
      if (conversationListControllerRef.current === controller) {
        conversationListControllerRef.current = null;
        setConversationLoading(false);
      }
    }
  }, []);

  const loadCommunicationProfile = useCallback(async () => {
    communicationLoadControllerRef.current?.abort();
    const controller = new AbortController();
    communicationLoadControllerRef.current = controller;
    setCommunicationLoading(true);
    setCommunicationError(null);

    try {
      const profile = await getCommunicationProfile(controller.signal);
      if (!controller.signal.aborted) setCommunicationProfile(profile);
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) {
        setCommunicationError(formatProjectMasterError(error));
      }
    } finally {
      if (communicationLoadControllerRef.current === controller) {
        communicationLoadControllerRef.current = null;
        setCommunicationLoading(false);
      }
    }
  }, []);

  const recordCommunicationFeedback = useCallback(
    async (
      category: CommunicationFeedbackCategory,
      note: string,
      scope: "global" | "situational",
    ): Promise<void> => {
      try {
        const profile = await submitCommunicationFeedback(category, note, scope);
        setCommunicationProfile(profile);
        setCommunicationError(null);
      } catch (error) {
        const message = formatProjectMasterError(error);
        setCommunicationError(message);
        throw new Error(message);
      }
    },
    [],
  );

  const loadAvailableModels = useCallback(async () => {
    modelLoadControllerRef.current?.abort();
    const controller = new AbortController();
    modelLoadControllerRef.current = controller;

    setConnectionState("checking");
    setConnectionError(null);

    try {
      await ensureManagedBackend();
      const status = await getModelStatus(controller.signal);
      const availableModels = status.models;
      const conversationalModels = availableModels.filter(
        (model) => model.conversational,
      );
      if (controller.signal.aborted) {
        return;
      }

      setModels(availableModels);
      setSelectedModel((currentModel) => {
        if (
          conversationalModels.some((model) => model.name === currentModel)
        ) {
          return currentModel;
        }
        return conversationalModels.some(
          (model) => model.name === status.configuredModel,
        )
          ? status.configuredModel
          : conversationalModels[0]?.name ?? "";
      });
      setContextLength(status.contextLength);
      setTeamCatalog(status.teamCatalog);
      setTeamAvailable(status.teamAvailable);
      setChatMode((currentMode) =>
        currentMode === "team" && !status.teamAvailable ? "direct" : currentMode,
      );
      setConnectionState(
        status.ollamaReachable && conversationalModels.length > 0
          ? "ready"
          : status.ollamaReachable
            ? "empty"
            : "offline",
      );
      if (!status.ollamaReachable) {
        setConnectionError("Ollama is not reachable through the Project Master backend.");
      }
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) {
        return;
      }

      setModels([]);
      setTeamCatalog([]);
      setTeamAvailable(false);
      setChatMode("direct");
      setSelectedModel("");
      setConnectionState("offline");
      setConnectionError(formatProjectMasterError(error));
    } finally {
      if (modelLoadControllerRef.current === controller) {
        modelLoadControllerRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    void loadAvailableModels();

    return () => {
      modelLoadControllerRef.current?.abort();
      conversationListControllerRef.current?.abort();
      conversationLoadControllerRef.current?.abort();
      communicationLoadControllerRef.current?.abort();
    };
  }, [loadAvailableModels]);

  useEffect(() => {
    if (connectionState !== "ready") return;
    void loadConversations();
    void loadCommunicationProfile();
    void listProjects()
      .then((projects) => {
        setBinderProjects(projects);
        setSelectedProjectId((current) =>
          !current || projects.some((project) => project.id === current)
            ? current
            : "",
        );
      })
      .catch(() => undefined);
  }, [connectionState, loadCommunicationProfile, loadConversations]);

  useEffect(() => {
    return () => {
      const activeStream = streamControllerRef.current;
      if (activeStream) {
        activeStream.controller.abort();
        void cancelChat(activeStream.requestId).catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    const messageList = messageListRef.current;
    if (messageList) {
      messageList.scrollTop = messageList.scrollHeight;
    }
  }, [messages]);

  async function runAssistantResponse(
    assistantId: string,
    request: RetryRequest,
  ): Promise<void> {
    const controller = new AbortController();
    const requestId = crypto.randomUUID();
    streamControllerRef.current = { controller, requestId };
    retryRequestsRef.current.set(assistantId, request);
    setIsStreaming(true);
    setConnectionError(null);
    setMessages((currentMessages) =>
      currentMessages.map((message) =>
        message.id === assistantId
          ? { ...message, content: "", error: undefined, status: "streaming" }
          : message,
      ),
    );

    try {
      await streamChat({
        requestId,
        model: request.model,
        message: request.message,
        mode: request.mode,
        allowMutations: request.allowMutations,
        projectId: request.projectId,
        conversationId: request.conversationId,
        signal: controller.signal,
        onConversation: setConversationId,
        onRun: setActiveRunId,
        onActivity: (activity) => {
          if (controller.signal.aborted) return;
          setRunActivities((current) => [...current, activity].slice(-60));
        },
        onToken: (token) => {
          if (controller.signal.aborted) {
            return;
          }

          setMessages((currentMessages) =>
            currentMessages.map((message) =>
              message.id === assistantId
                ? { ...message, content: message.content + token }
                : message,
            ),
          );
        },
      });

      setMessages((currentMessages) =>
        currentMessages.map((message) =>
          message.id === assistantId
            ? { ...message, status: "complete" }
            : message,
        ),
      );
      retryRequestsRef.current.delete(assistantId);
      setConnectionState("ready");
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) {
        setMessages((currentMessages) =>
          currentMessages.map((message) =>
            message.id === assistantId
              ? { ...message, error: undefined, status: "stopped" }
              : message,
          ),
        );
        retryRequestsRef.current.delete(assistantId);
      } else {
        const displayError = formatProjectMasterError(error);
        setMessages((currentMessages) =>
          currentMessages.map((message) =>
            message.id === assistantId
              ? { ...message, error: displayError, status: "error" }
              : message,
          ),
        );

        if (error instanceof ProjectMasterUnavailableError) {
          setConnectionState("offline");
        }
      }
    } finally {
      void loadConversations();
      if (streamControllerRef.current?.controller === controller) {
        streamControllerRef.current = null;
        setIsStreaming(false);
      }
    }
  }

  function resetComposerHeight(): void {
    requestAnimationFrame(() => {
      if (composerRef.current) {
        composerRef.current.style.height = "auto";
      }
    });
  }

  function submitMessage(): void {
    const content = composer.trim();
    if (!content || !selectedModel || isStreaming) {
      return;
    }

    const userMessage: UiMessage = {
      id: createMessageId("user"),
      role: "user",
      content,
      status: "complete",
    };
    const assistantMessage: UiMessage = {
      id: createMessageId("assistant"),
      role: "assistant",
      content: "",
      status: "streaming",
    };
    const request: RetryRequest = {
      model: selectedModel,
      message: content,
      mode: chatMode,
      allowMutations,
      projectId: selectedProjectId || undefined,
      conversationId,
    };

    setRunActivities([]);
    setActiveRunId(undefined);
    if (chatMode === "team") setTeamView("mission");
    setMessages((currentMessages) => [
      ...currentMessages,
      userMessage,
      assistantMessage,
    ]);
    setComposer("");
    resetComposerHeight();
    void runAssistantResponse(assistantMessage.id, request);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    submitMessage();
  }

  function handleComposerChange(event: ChangeEvent<HTMLTextAreaElement>): void {
    setComposer(event.currentTarget.value);
    event.currentTarget.style.height = "auto";
    event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 180)}px`;
  }

  function handleComposerKeyDown(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ): void {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submitMessage();
    }
  }

  function stopStreaming(): void {
    const activeStream = streamControllerRef.current;
    if (!activeStream) {
      return;
    }
    activeStream.controller.abort();
    void cancelChat(activeStream.requestId).catch(() => undefined);
  }

  function startNewSession(): void {
    if (isStreaming) return;
    setConversationId(undefined);
    setMessages([]);
    setRunActivities([]);
    setActiveRunId(undefined);
    setComposer("");
    setAllowMutations(false);
    resetComposerHeight();
  }

  async function openConversation(id: string): Promise<void> {
    if (isStreaming || id === conversationId) return;
    setAllowMutations(false);
    conversationLoadControllerRef.current?.abort();
    const controller = new AbortController();
    conversationLoadControllerRef.current = controller;
    setConversationLoading(true);
    setConversationError(null);

    try {
      const conversation = await getConversation(id, controller.signal);
      if (controller.signal.aborted) return;
      setConversationId(conversation.id);
      setRunActivities([]);
      setActiveRunId(undefined);
      setMessages(
        conversation.messages.map((message) => ({
          id: createMessageId(message.role),
          role: message.role,
          content: message.content,
          status: "complete",
        })),
      );
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) {
        setConversationError(formatProjectMasterError(error));
      }
    } finally {
      if (conversationLoadControllerRef.current === controller) {
        conversationLoadControllerRef.current = null;
        setConversationLoading(false);
      }
    }
  }

  async function retryMessage(messageId: string): Promise<void> {
    if (isStreaming) {
      return;
    }

    const request = retryRequestsRef.current.get(messageId);
    if (request) {
      setConnectionState("checking");
      try {
        await ensureManagedBackend();
        await runAssistantResponse(
          messageId,
          withCurrentMutationAuthorization(request, allowMutations),
        );
      } catch (error) {
        const displayError = formatProjectMasterError(error);
        setConnectionState("offline");
        setMessages((currentMessages) =>
          currentMessages.map((message) =>
            message.id === messageId
              ? { ...message, error: displayError, status: "error" }
              : message,
          ),
        );
      }
    }
  }

  const selectedModelInfo = models.find(
    (model) => model.name === selectedModel,
  );
  const lastUserMessage = [...messages]
    .reverse()
    .find((message) => message.role === "user");
  const lastAssistantMessage = [...messages]
    .reverse()
    .find((message) => message.role === "assistant");
  const missionAvailable =
    chatMode === "team" &&
    messages.length > 0 &&
    (isStreaming || runActivities.length > 0 || Boolean(activeRunId));
  const showMission = missionAvailable && teamView === "mission";
  const canSend = Boolean(
    composer.trim() &&
      selectedModel &&
      selectedModelInfo?.conversational &&
      !isStreaming,
  );
  const connectionLabel =
    connectionState === "checking"
      ? "Checking backend"
      : connectionState === "ready"
        ? `${models.length} local model${models.length === 1 ? "" : "s"}`
        : connectionState === "empty"
          ? models.length
            ? "No chat-capable model"
            : "No models installed"
          : "Backend offline";
  const composerPlaceholder = isStreaming
    ? "MASTER is responding…"
    : connectionState === "offline"
      ? "Project Master is offline — press Retry"
      : connectionState === "empty"
        ? models.length
          ? "Install or select a chat-capable Ollama model"
          : "Install an Ollama model to begin"
        : "Message MASTER";
  const chatLayout = layoutController.layout.panels.chat_panel;
  const customizerLayout = layoutController.layout.panels.customize_panel;
  const workspaceStyle = {
    "--chat-content-width": `${chatLayout.width}%`,
    "--customizer-width": `${customizerLayout.width}px`,
  } as CSSProperties;

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand-lockup" aria-label="Project Master AI">
          <img
            className="brand-emblem"
            src="/brand/master-ai-primary.png"
            alt=""
          />
          <span className="brand-copy">
            <span className="brand-name">MASTER</span>
            <span className="brand-subtitle">LOCAL MULTI-AI COMMAND CENTER</span>
          </span>
        </div>

        <div className="header-controls">
          <span
            className={`connection-status connection-status--${connectionState}`}
            role="status"
          >
            <span className="connection-dot" aria-hidden="true" />
            {connectionLabel}
          </span>

          <div className="mode-control" aria-label="Chat mode">
            <button
              className={chatMode === "direct" ? "is-active" : undefined}
              type="button"
              aria-pressed={chatMode === "direct"}
              onClick={() => setChatMode("direct")}
              disabled={isStreaming || activeWorkspace !== "chat"}
            >
              Direct
            </button>
            <button
              className={chatMode === "team" ? "is-active" : undefined}
              type="button"
              aria-pressed={chatMode === "team"}
              title={
                teamAvailable
                  ? "Use the local model council and one authorized lead"
                  : "The backend did not report a compatible team catalog"
              }
              onClick={() => setChatMode("team")}
              disabled={!teamAvailable || isStreaming || activeWorkspace !== "chat"}
            >
              Team
            </button>
          </div>

          <label
            className={`mutation-control ${allowMutations ? "is-enabled" : ""}`}
            title="This applies only to chat tool calls in the current conversation. Dashboard actions remain explicit."
          >
            <input
              type="checkbox"
              checked={allowMutations}
              onChange={(event) =>
                setAllowMutations(event.currentTarget.checked)
              }
              disabled={isStreaming || activeWorkspace !== "chat"}
            />
            <span>
              {allowMutations ? "Allow project changes" : "Read only"}
            </span>
          </label>

          <label className="model-control binder-control" htmlFor="binder-select">
            <span>Binder</span>
            <select
              id="binder-select"
              value={selectedProjectId}
              onChange={(event) => setSelectedProjectId(event.currentTarget.value)}
              disabled={connectionState !== "ready" || isStreaming}
              title="Attach indexed Project Binder context to chat"
            >
              <option value="">No project context</option>
              {binderProjects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>

          <label className="model-control" htmlFor="model-select">
            <span>{chatMode === "team" ? "Lead" : "Model"}</span>
            <select
              id="model-select"
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.currentTarget.value)}
              disabled={connectionState !== "ready" || isStreaming}
            >
              {models.length === 0 ? (
                <option value="">No models available</option>
              ) : (
                models.map((model) => (
                  <option
                    key={model.name}
                    value={model.name}
                    disabled={!model.conversational}
                  >
                    {model.name} —{" "}
                    {!model.conversational
                      ? "not chat-compatible"
                      : model.toolCapable
                        ? "chat + tools"
                        : model.capabilities.length
                          ? "chat only"
                          : "chat · capabilities unreported"}
                  </option>
                ))
              )}
            </select>
            {selectedModelInfo ? (
              <small className="model-readiness">
                {selectedModelInfo.toolCapable
                  ? "Tool-capable"
                  : "Completion-only; tool requests may not execute in Direct mode"}
                {selectedModelInfo.capabilities.length
                  ? ` · ${selectedModelInfo.capabilities.join(", ")}`
                  : " · Ollama did not report capabilities"}
              </small>
            ) : null}
          </label>

          <button
            className="button button--secondary button--customize"
            type="button"
            aria-expanded={
              activeWorkspace === "chat" && customizerLayout.visible
            }
            aria-controls="layout-customizer"
            onClick={() => {
              const nextVisible =
                activeWorkspace === "chat"
                  ? !customizerLayout.visible
                  : true;
              setActiveWorkspace("chat");
              layoutController.applyOperations([
                {
                  operation: "set_visibility",
                  target: "customize_panel",
                  value: nextVisible,
                },
              ]);
            }}
          >
            Customize
          </button>

          {isStreaming ? (
            <button
              className="button button--stop"
              type="button"
              onClick={stopStreaming}
            >
              Stop
            </button>
          ) : null}
        </div>
      </header>

      <UpdateNotice isBusy={isStreaming} />

      <WorkspaceNavigation
        active={activeWorkspace}
        disabled={isStreaming}
        onChange={setActiveWorkspace}
      />

      {activeWorkspace === "chat" ? (
      <div
        className={`workspace-shell ${chatMode === "team" ? "workspace-shell--team" : ""}`}
        style={workspaceStyle}
      >
        <ConversationLibrary
          conversations={conversations}
          activeConversationId={conversationId}
          isBusy={isStreaming}
          isLoading={conversationLoading}
          error={conversationError}
          onNewSession={startNewSession}
          onOpenConversation={(id) => void openConversation(id)}
          onRetry={() => void loadConversations()}
        />
        <section
          className="message-list"
          ref={messageListRef}
          aria-label="Conversation"
        >
        {chatMode === "team" ? (
          <TeamStrip
            available={teamAvailable}
            catalog={teamCatalog}
            runId={activeRunId}
            isStreaming={isStreaming}
          />
        ) : null}
        {missionAvailable ? (
          <div className="mission-toggle" aria-label="Team run view">
            <button
              type="button"
              className={teamView === "mission" ? "is-active" : undefined}
              aria-pressed={teamView === "mission"}
              onClick={() => setTeamView("mission")}
            >
              Mission
            </button>
            <button
              type="button"
              className={teamView === "transcript" ? "is-active" : undefined}
              aria-pressed={teamView === "transcript"}
              onClick={() => setTeamView("transcript")}
            >
              Transcript
            </button>
          </div>
        ) : null}
        {connectionError ? (
          <div className="connection-notice" role="alert">
            <div>
              <strong>Connection unavailable</strong>
              <p>{connectionError}</p>
            </div>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => void loadAvailableModels()}
            >
              Retry
            </button>
          </div>
        ) : null}

        {connectionState === "empty" ? (
          <div className="connection-notice" role="status">
            <div>
              <strong>
                {models.length
                  ? "No conversational Ollama model"
                  : "No Ollama models installed"}
              </strong>
              <p>
                {models.length
                  ? "Installed embedding or non-completion models cannot drive chat."
                  : "Run "}
                {!models.length ? (
                  <code>ollama pull &lt;model-name&gt;</code>
                ) : null}
                {!models.length ? ", then retry." : " Install a chat model, then retry."}
              </p>
            </div>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => void loadAvailableModels()}
            >
              Retry
            </button>
          </div>
        ) : null}

        {messages.length === 0 ? (
          <div className="empty-state">
            <img
              className="empty-state-emblem"
              src="/brand/master-ai-primary.png"
              alt="Project Master AI"
            />
            <span className="empty-state-kicker">PROJECT MASTER // LOCAL SESSION</span>
            <h1>Reason clearly. Create deliberately.</h1>
            <p>
              Choose an installed model, then start a conversation. The Python
              engine preserves the conversation, memory, tools, and evidence.
            </p>
            <div className="creator-mark">
              <img src="/brand/mm-heritage.jpg" alt="MM creator mark" />
              <span>MM // CREATOR MARK</span>
            </div>
          </div>
        ) : showMission ? (
          <MissionView
            goal={lastUserMessage?.content ?? ""}
            runId={activeRunId}
            activities={runActivities}
            isStreaming={isStreaming}
            answer={lastAssistantMessage?.content ?? ""}
            answerStatus={lastAssistantMessage?.status ?? "complete"}
            answerError={lastAssistantMessage?.error}
            onRetry={
              lastAssistantMessage
                ? () => void retryMessage(lastAssistantMessage.id)
                : undefined
            }
          />
        ) : (
          <div className="message-stack" role="log" aria-live="polite">
            {messages.map((message) => (
              <article
                className={`message-row message-row--${message.role}`}
                key={message.id}
              >
                <div className="message-meta">
                  <span>{message.role === "user" ? "YOU" : "MASTER"}</span>
                  {message.status === "streaming" ? <span>STREAMING</span> : null}
                  {message.status === "stopped" ? <span>STOPPED</span> : null}
                </div>

                <div className="message-content">
                  {message.content ? (
                    message.role === "assistant" ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {message.content}
                      </ReactMarkdown>
                    ) : (
                      message.content
                    )
                  ) : message.status === "streaming" ? (
                    <span className="typing-indicator" aria-label="Generating">
                      <span />
                      <span />
                      <span />
                    </span>
                  ) : message.status === "stopped" ? (
                    <span className="message-muted">Generation stopped.</span>
                  ) : null}
                </div>

                {message.error ? (
                  <div className="message-error" role="alert">
                    <span>{message.error}</span>
                    <button
                      className="button button--secondary"
                      type="button"
                      onClick={() => void retryMessage(message.id)}
                      disabled={isStreaming}
                    >
                      Retry
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
        </section>

        {chatMode === "team" || runActivities.length > 0 ? (
          <RunRail
            activities={runActivities}
            runId={activeRunId}
            isStreaming={isStreaming}
            onClear={() => {
              setRunActivities([]);
              setActiveRunId(undefined);
            }}
          />
        ) : null}

        {customizerLayout.visible ? (
          <div id="layout-customizer">
            <LayoutCustomizer
              layout={layoutController.layout}
              canUndo={layoutController.canUndo}
              savedLayouts={layoutController.savedLayouts}
              onApplyOperations={layoutController.applyOperations}
              onUndo={layoutController.undo}
              onReset={layoutController.reset}
              onSave={layoutController.saveCurrent}
              onApplySaved={layoutController.applySaved}
              onDeleteSaved={layoutController.deleteSaved}
              communicationProfile={communicationProfile}
              communicationLoading={communicationLoading}
              communicationError={communicationError}
              onRefreshCommunication={() => void loadCommunicationProfile()}
              onSubmitCommunicationFeedback={recordCommunicationFeedback}
            />
          </div>
        ) : null}
      </div>
      ) : (
        <FeatureWorkspace
          workspace={activeWorkspace}
          onReturnToCommand={() => setActiveWorkspace("chat")}
          selectedProjectId={selectedProjectId}
          onSelectProject={setSelectedProjectId}
          onProjectsChange={setBinderProjects}
        />
      )}

      {activeWorkspace === "chat" ? (
      <footer className="composer-shell">
        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            ref={composerRef}
            value={composer}
            rows={1}
            onChange={handleComposerChange}
            onKeyDown={handleComposerKeyDown}
            placeholder={composerPlaceholder}
            aria-label="Message MASTER"
            disabled={connectionState !== "ready" || isStreaming}
          />
          <button
            className="button button--primary send-button"
            type="submit"
            disabled={!canSend}
          >
            Send
          </button>
        </form>
        <div className="composer-hint">
          <span
            className={`composer-safety ${allowMutations ? "is-enabled" : ""}`}
            role="status"
          >
            CHAT TOOLS:{" "}
            {allowMutations ? "PROJECT CHANGES ALLOWED" : "READ ONLY"}
          </span>
          <span>{chatMode === "team" ? "All compatible models · one tool lead" : "Single model"}</span>
          <span>{selectedProjectId ? "Project Binder attached" : "No Binder context"}</span>
          <span>Enter to send</span>
          <span>Shift+Enter for a new line</span>
          <span>{contextLength.toLocaleString()} token context</span>
        </div>
      </footer>
      ) : (
        <footer className="workspace-footer">
          <span>LOCAL-FIRST</span>
          <span>Not configured means no external connection or simulated data.</span>
        </footer>
      )}
    </main>
  );
}

export default App;
