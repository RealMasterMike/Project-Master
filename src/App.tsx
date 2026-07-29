import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import { ChatImageAttachments } from "./components/ChatImageAttachments";
import { ChatSessionToolbar } from "./components/ChatSessionToolbar";
import { ConversationLibrary } from "./components/ConversationLibrary";
import { FeatureWorkspace } from "./components/FeatureWorkspace";
import { CommunicationProfilePanel } from "./components/CommunicationProfilePanel";
import { MissionView } from "./components/MissionView";
import { RunRail, TeamStrip } from "./components/TeamRunPanel";
import { SettingsWorkspace } from "./components/SettingsWorkspace";
import { UpdateNotice } from "./components/UpdateNotice";
import {
  WorkspaceNavigation,
  type MasterWorkspace,
} from "./components/WorkspaceNavigation";
import {
  SPEECH_SKIP_SECONDS,
  SPEECH_SPEEDS,
  useMessageSpeech,
} from "./hooks/useMessageSpeech";
import { useAppPreferences } from "./hooks/useAppPreferences";
import { withCurrentToolAuthorization } from "./lib/chatAuthorization";
import {
  cancelChat,
  ensureManagedBackend,
  formatProjectMasterError,
  getCommunicationProfile,
  getConversation,
  getModelStatus,
  isVisionCapableModel,
  isCuratedTeamModel,
  isAbortError,
  listConversations,
  listProjectMediaAssets,
  listProjects,
  resolveModelSelection,
  resolveVisionModelSelection,
  submitCommunicationFeedback,
  type CommunicationFeedbackCategory,
  type ProjectMasterChatMode,
  type ProjectMasterConversation,
  type ProjectMasterCommunicationProfile,
  ProjectMasterUnavailableError,
  type ProjectMasterRunActivity,
  type ProjectMasterTeamCatalogModel,
  type MasterProject,
  type MediaAssetSummary,
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
  allowWebSearch: boolean;
  imageAssetIds: string[];
  projectId?: string;
  conversationId?: string;
}

interface ActiveStream {
  controller: AbortController;
  requestId: string;
}

const FOLLOW_SCROLL_THRESHOLD_PX = 64;
const MAX_CHAT_IMAGE_TOTAL_BYTES = 40 * 1024 * 1024;

let nextMessageId = 0;

function createMessageId(role: UiMessage["role"]): string {
  nextMessageId += 1;
  return `${role}-${Date.now()}-${nextMessageId}`;
}

function App() {
  const appPreferences = useAppPreferences();
  const [models, setModels] = useState<ProjectMasterModel[]>([]);
  const [binderProjects, setBinderProjects] = useState<MasterProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [teamCatalog, setTeamCatalog] = useState<ProjectMasterTeamCatalogModel[]>([]);
  const [chatMode, setChatMode] = useState<ProjectMasterChatMode>("team");
  // Deliberately session-only: never read from or write to persistent storage.
  const [allowMutations, setAllowMutations] = useState(false);
  const [allowWebSearch, setAllowWebSearch] = useState(false);
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
  const [contextLength, setContextLength] = useState(65536);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [composer, setComposer] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [followingOutput, setFollowingOutput] = useState(true);
  const [runRailOpen, setRunRailOpen] = useState(false);
  const [projectImages, setProjectImages] = useState<MediaAssetSummary[]>([]);
  const [selectedChatImages, setSelectedChatImages] = useState<
    MediaAssetSummary[]
  >([]);
  const [projectImagesLoading, setProjectImagesLoading] = useState(false);
  const [projectImagesError, setProjectImagesError] = useState<string | null>(
    null,
  );
  const [imageSelectionError, setImageSelectionError] = useState<string | null>(
    null,
  );

  const modelLoadControllerRef = useRef<AbortController | null>(null);
  const projectImagesControllerRef = useRef<AbortController | null>(null);
  const conversationListControllerRef = useRef<AbortController | null>(null);
  const conversationLoadControllerRef = useRef<AbortController | null>(null);
  const communicationLoadControllerRef = useRef<AbortController | null>(null);
  const streamControllerRef = useRef<ActiveStream | null>(null);
  const retryRequestsRef = useRef(new Map<string, RetryRequest>());
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const followOutputRef = useRef(true);
  const speech = useMessageSpeech(
    activeWorkspace === "chat" && connectionState === "ready",
  );
  const autoSpokenRef = useRef<string | null>(null);
  const speakRef = useRef(speech.speak);
  speakRef.current = speech.speak;
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const selectedProject = binderProjects.find(
    (project) => project.id === selectedProjectId,
  );
  const automaticVisionModel = resolveVisionModelSelection(
    models,
    appPreferences.preferredVisionModel,
  );
  const teamLeadModel =
    models.find(
      (model) =>
        model.name === selectedModel && isCuratedTeamModel(model),
    )?.name ??
    models.find(isCuratedTeamModel)?.name ??
    "";
  const activeChatModel =
    chatMode === "team" ? teamLeadModel : selectedModel;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.density = appPreferences.interfaceDensity;
    root.dataset.textScale = appPreferences.textScale;
    root.dataset.motion = appPreferences.motion;
    return () => {
      delete root.dataset.density;
      delete root.dataset.textScale;
      delete root.dataset.motion;
    };
  }, [
    appPreferences.interfaceDensity,
    appPreferences.motion,
    appPreferences.textScale,
  ]);

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
      setSelectedModel((currentModel) =>
        resolveModelSelection(
          availableModels,
          currentModel,
          status.recommendedModel,
        ),
      );
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

  const loadProjectImages = useCallback(async () => {
    projectImagesControllerRef.current?.abort();
    setProjectImages([]);
    setProjectImagesError(null);
    if (
      connectionState !== "ready" ||
      !selectedProject ||
      selectedProject.projectType !== "creator"
    ) {
      setProjectImagesLoading(false);
      return;
    }

    const controller = new AbortController();
    projectImagesControllerRef.current = controller;
    setProjectImagesLoading(true);
    try {
      const assets = await listProjectMediaAssets(
        selectedProject.id,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setProjectImages(
        assets.filter(
          (asset) =>
            asset.kind === "image" &&
            asset.width !== undefined &&
            asset.height !== undefined &&
            asset.sizeBytes <= 20 * 1024 * 1024,
        ),
      );
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) {
        setProjectImagesError(formatProjectMasterError(error));
      }
    } finally {
      if (projectImagesControllerRef.current === controller) {
        projectImagesControllerRef.current = null;
        setProjectImagesLoading(false);
      }
    }
  }, [connectionState, selectedProject]);

  useEffect(() => {
    void loadAvailableModels();

    return () => {
      modelLoadControllerRef.current?.abort();
      conversationListControllerRef.current?.abort();
      conversationLoadControllerRef.current?.abort();
      communicationLoadControllerRef.current?.abort();
      projectImagesControllerRef.current?.abort();
    };
  }, [loadAvailableModels]);

  useEffect(() => {
    setSelectedChatImages([]);
    setImageSelectionError(null);
  }, [selectedProjectId]);

  useEffect(() => {
    void loadProjectImages();
    return () => projectImagesControllerRef.current?.abort();
  }, [loadProjectImages]);

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

  // Follow new output only while the reader is already at the bottom, so
  // scrolling up to re-read earlier output is not yanked back mid-stream.
  useEffect(() => {
    const messageList = messageListRef.current;
    if (!messageList) return;
    const onScroll = () => {
      const distanceFromBottom =
        messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight;
      const shouldFollow =
        distanceFromBottom <= FOLLOW_SCROLL_THRESHOLD_PX;
      followOutputRef.current = shouldFollow;
      setFollowingOutput(shouldFollow);
    };
    messageList.addEventListener("scroll", onScroll, { passive: true });
    return () => messageList.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const messageList = messageListRef.current;
    if (messageList && messages.length > 0 && followOutputRef.current) {
      messageList.scrollTop = messageList.scrollHeight;
    }
  }, [messages, runActivities, teamView]);

  function scrollToLatest(): void {
    const messageList = messageListRef.current;
    if (!messageList) return;
    messageList.scrollTo({
      top: messageList.scrollHeight,
      behavior: "smooth",
    });
    followOutputRef.current = true;
    setFollowingOutput(true);
  }

  // Auto-speak fires once per finished response. Keyed on message id so
  // streaming tokens cannot retrigger it mid-answer.
  useEffect(() => {
    if (!speech.autoSpeak || !speech.available) return;
    const latest = messages[messages.length - 1];
    if (
      !latest ||
      latest.role !== "assistant" ||
      latest.status !== "complete" ||
      !latest.content.trim() ||
      autoSpokenRef.current === latest.id
    ) {
      return;
    }
    autoSpokenRef.current = latest.id;
    void speakRef.current(latest.id, latest.content);
    // speech.speak is read through a ref so this only reacts to new messages;
    // the hook returns a fresh object each render.
  }, [messages, speech.autoSpeak, speech.available]);

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
        allowWebSearch: request.allowWebSearch,
        imageAssetIds: request.imageAssetIds,
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

  function addChatImage(assetId: string): void {
    const asset = projectImages.find((item) => item.id === assetId);
    if (!asset || selectedChatImages.some((item) => item.id === assetId)) return;
    if (!automaticVisionModel) {
      setImageSelectionError(
        "Choose an installed vision model in Settings before attaching an image.",
      );
      return;
    }
    const totalBytes =
      selectedChatImages.reduce((total, item) => total + item.sizeBytes, 0) +
      asset.sizeBytes;
    if (totalBytes > MAX_CHAT_IMAGE_TOTAL_BYTES) {
      setImageSelectionError("Selected images must total 40 MiB or less.");
      return;
    }
    setImageSelectionError(null);
    setChatMode("direct");
    setSelectedModel(automaticVisionModel);
    setSelectedChatImages((current) => [...current, asset].slice(0, 3));
  }

  function removeChatImage(assetId: string): void {
    setImageSelectionError(null);
    setSelectedChatImages((current) =>
      current.filter((asset) => asset.id !== assetId),
    );
  }

  function submitMessage(): void {
    const content = composer.trim();
    if (!content || !activeChatModel || isStreaming) {
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
      model: activeChatModel,
      message: content,
      mode: chatMode,
      allowMutations,
      allowWebSearch,
      imageAssetIds: selectedChatImages.map((asset) => asset.id),
      projectId: selectedProjectId || undefined,
      conversationId,
    };

    setRunActivities([]);
    setActiveRunId(undefined);
    if (chatMode === "team") setTeamView("mission");
    followOutputRef.current = true;
    setFollowingOutput(true);
    setMessages((currentMessages) => [
      ...currentMessages,
      userMessage,
      assistantMessage,
    ]);
    setComposer("");
    setSelectedChatImages([]);
    setImageSelectionError(null);
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
    setSelectedChatImages([]);
    setImageSelectionError(null);
    setAllowMutations(false);
    setAllowWebSearch(false);
    followOutputRef.current = true;
    setFollowingOutput(true);
    resetComposerHeight();
  }

  async function openConversation(id: string): Promise<void> {
    if (isStreaming || id === conversationId) return;
    setAllowMutations(false);
    setAllowWebSearch(false);
    conversationLoadControllerRef.current?.abort();
    const controller = new AbortController();
    conversationLoadControllerRef.current = controller;
    setConversationLoading(true);
    setConversationError(null);

    try {
      const conversation = await getConversation(id, controller.signal);
      if (controller.signal.aborted) return;
      setConversationId(conversation.id);
      setSelectedChatImages([]);
      setImageSelectionError(null);
      setRunActivities([]);
      setActiveRunId(undefined);
      followOutputRef.current = true;
      setFollowingOutput(true);
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
          withCurrentToolAuthorization(request, {
            allowMutations,
            allowWebSearch,
          }),
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
    (model) => model.name === activeChatModel,
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
  const runRailAvailable =
    chatMode === "team" || runActivities.length > 0;
  const latestRunActivity = runActivities[runActivities.length - 1];
  const canSend = Boolean(
    composer.trim() &&
      activeChatModel &&
      selectedModelInfo?.conversational &&
      (selectedChatImages.length === 0 ||
        isVisionCapableModel(selectedModelInfo)) &&
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
  return (
    <main className="app-shell">
      <header className="app-header">
        <WorkspaceNavigation
          active={activeWorkspace}
          disabled={isStreaming}
          connectionState={connectionState}
          modelCount={models.length}
          onChange={setActiveWorkspace}
        />

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

        <div className="header-status">
          <span
            className={`connection-status connection-status--${connectionState}`}
            role="status"
          >
            <span className="connection-dot" aria-hidden="true" />
            {connectionLabel}
          </span>
        </div>
      </header>

      <UpdateNotice isBusy={isStreaming} />

      {activeWorkspace === "chat" ? (
      <div
        className={`workspace-shell ${runRailAvailable ? "workspace-shell--team" : ""}`}
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
        <div className="conversation-column">
          <ChatSessionToolbar
            mode={chatMode}
            onModeChange={setChatMode}
            teamAvailable={teamAvailable}
            models={models}
            selectedModel={activeChatModel}
            onModelChange={setSelectedModel}
            projects={binderProjects}
            selectedProjectId={selectedProjectId}
            onProjectChange={setSelectedProjectId}
            allowMutations={allowMutations}
            onAllowMutationsChange={setAllowMutations}
            allowWebSearch={allowWebSearch}
            onAllowWebSearchChange={setAllowWebSearch}
            requiresVision={selectedChatImages.length > 0}
            isBusy={isStreaming || connectionState !== "ready"}
            activityCount={runActivities.length}
            railAvailable={runRailAvailable}
            railOpen={runRailOpen}
            onToggleRail={() => setRunRailOpen((current) => !current)}
          />
          <div className="transcript-shell">
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
        {runRailAvailable ? (
          <span
            className="visually-hidden"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            {latestRunActivity
              ? `${latestRunActivity.kind.replace(/_/g, " ")}: ${latestRunActivity.message}`
              : isStreaming
                ? "Team run started."
                : "Team activity is ready."}
          </span>
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
              <span className="creator-mark__monogram" aria-hidden="true">
                MM
              </span>
              <span>CREATED BY MASTER MIKE</span>
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
            deliveryAction={
              lastAssistantMessage &&
              speech.available &&
              lastAssistantMessage.content &&
              lastAssistantMessage.status !== "streaming" ? (
                <button
                  className="message-speak"
                  type="button"
                  aria-label={
                    speech.pendingId === lastAssistantMessage.id
                      ? "Cancel speech rendering for this message"
                      : speech.speakingId === lastAssistantMessage.id
                      ? "Stop speaking this message"
                      : "Speak this message"
                  }
                  aria-pressed={
                    speech.speakingId === lastAssistantMessage.id ||
                    speech.pendingId === lastAssistantMessage.id
                  }
                  disabled={
                    speech.pendingId !== null &&
                    speech.pendingId !== lastAssistantMessage.id
                  }
                  onClick={() =>
                    speech.speakingId === lastAssistantMessage.id ||
                    speech.pendingId === lastAssistantMessage.id
                      ? speech.stop()
                      : void speech.speak(
                          lastAssistantMessage.id,
                          lastAssistantMessage.content,
                        )
                  }
                >
                  {speech.pendingId === lastAssistantMessage.id
                    ? "Cancel render"
                    : speech.speakingId === lastAssistantMessage.id
                      ? "■ Stop"
                      : "▶ Speak"}
                </button>
              ) : null
            }
          />
        ) : (
          <div
            className="message-stack"
            role="log"
            aria-live={isStreaming ? "off" : "polite"}
          >
            {messages.map((message) => (
              <article
                className={`message-row message-row--${message.role}`}
                key={message.id}
              >
                <div className="message-meta">
                  <span>{message.role === "user" ? "YOU" : "MASTER"}</span>
                  {message.status === "streaming" ? <span>STREAMING</span> : null}
                  {message.status === "stopped" ? <span>STOPPED</span> : null}
                  {message.role === "assistant" &&
                  speech.available &&
                  message.content &&
                  message.status !== "streaming" ? (
                    <button
                      className="message-speak"
                      type="button"
                      aria-label={
                        speech.pendingId === message.id
                          ? "Cancel speech rendering for this message"
                          : speech.speakingId === message.id
                          ? "Stop speaking this message"
                          : "Speak this message"
                      }
                      aria-pressed={
                        speech.speakingId === message.id ||
                        speech.pendingId === message.id
                      }
                      disabled={
                        speech.pendingId !== null &&
                        speech.pendingId !== message.id
                      }
                      onClick={() =>
                        speech.speakingId === message.id ||
                        speech.pendingId === message.id
                          ? speech.stop()
                          : void speech.speak(message.id, message.content)
                      }
                    >
                      {speech.pendingId === message.id
                        ? "Cancel render"
                        : speech.speakingId === message.id
                          ? "■ Stop"
                          : "▶ Speak"}
                    </button>
                  ) : null}
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
            {!followingOutput && messages.length ? (
              <button
                className="follow-output-button"
                type="button"
                onClick={scrollToLatest}
              >
                Jump to latest
                <span aria-hidden="true">↓</span>
              </button>
            ) : null}
          </div>

          <footer className="composer-shell">
            {selectedProject?.projectType === "creator" ? (
              <ChatImageAttachments
                availableImages={projectImages}
                selectedImages={selectedChatImages}
                isLoading={projectImagesLoading}
                error={projectImagesError}
                selectionError={imageSelectionError}
                disabled={isStreaming || connectionState !== "ready"}
                visionModelAvailable={Boolean(automaticVisionModel)}
                onAdd={addChatImage}
                onRemove={removeChatImage}
                onRetry={() => void loadProjectImages()}
              />
            ) : null}
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
              {isStreaming ? (
                <button
                  className="button button--stop send-button"
                  type="button"
                  onClick={stopStreaming}
                >
                  Stop
                </button>
              ) : (
                <button
                  className="button button--primary send-button"
                  type="submit"
                  disabled={!canSend}
                >
                  Send
                </button>
              )}
            </form>
            {speech.available ? (
              <div className="speech-bar">
                <span className="speech-bar__label">READ ALOUD</span>
                <label className="speech-voice" htmlFor="speech-voice-select">
                  <span>Voice</span>
                  <select
                    id="speech-voice-select"
                    value={speech.voiceId}
                    onChange={(event) =>
                      speech.setVoiceId(event.currentTarget.value)
                    }
                    title="Voice used to speak messages"
                  >
                    {speech.voices.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.name}
                        {voice.mode === "reference"
                          ? " — cloned"
                          : " — designed"}
                      </option>
                    ))}
                  </select>
                </label>
                <label
                  className={`speech-auto ${
                    speech.autoSpeak ? "is-enabled" : ""
                  }`}
                  title="Speak each response automatically as it finishes"
                >
                  <input
                    type="checkbox"
                    checked={speech.autoSpeak}
                    onChange={(event) =>
                      speech.setAutoSpeak(event.currentTarget.checked)
                    }
                  />
                  <span>
                    {speech.autoSpeak ? "Auto-speak on" : "Auto-speak off"}
                  </span>
                </label>
                <label className="speech-speed" htmlFor="speech-speed-select">
                  <span>Speed</span>
                  <select
                    id="speech-speed-select"
                    value={String(speech.speed)}
                    onChange={(event) =>
                      speech.setSpeed(Number(event.currentTarget.value))
                    }
                    title="Playback speed"
                  >
                    {SPEECH_SPEEDS.map((rate) => (
                      <option key={rate} value={String(rate)}>
                        {rate}×
                      </option>
                    ))}
                  </select>
                </label>
                {speech.speakingId ? (
                  <span className="speech-transport">
                    <button
                      type="button"
                      aria-label={`Rewind ${SPEECH_SKIP_SECONDS} seconds`}
                      onClick={() => speech.seekBy(-SPEECH_SKIP_SECONDS)}
                    >
                      −{SPEECH_SKIP_SECONDS}s
                    </button>
                    <button
                      type="button"
                      aria-label={`Skip forward ${SPEECH_SKIP_SECONDS} seconds`}
                      onClick={() => speech.seekBy(SPEECH_SKIP_SECONDS)}
                    >
                      +{SPEECH_SKIP_SECONDS}s
                    </button>
                  </span>
                ) : null}
                {speech.speakingId || speech.pendingId ? (
                  <button
                    className="speech-stop"
                    type="button"
                    onClick={speech.stop}
                  >
                    Stop audio
                  </button>
                ) : null}
                {speech.error ? (
                  <span className="speech-error" role="alert">
                    {speech.error}
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className="composer-hint">
              <span
                className={`composer-safety ${
                  allowMutations ? "is-enabled" : ""
                }`}
                role="status"
              >
                {allowMutations ? "CHANGES ALLOWED" : "READ ONLY"}
              </span>
              <span
                className={`composer-safety ${
                  allowWebSearch ? "is-enabled" : ""
                }`}
                role="status"
              >
                {allowWebSearch ? "ONLINE SEARCH ON" : "LOCAL ONLY"}
              </span>
              <span>
                {chatMode === "team"
                  ? "Local model team · one tool lead"
                  : "Direct model"}
              </span>
              <span>
                {selectedProjectId ? "Binder attached" : "No Binder"}
              </span>
              <span>Enter to send · Shift+Enter for a new line</span>
              <span>{contextLength.toLocaleString()} tokens</span>
            </div>
          </footer>
        </div>

        {runRailAvailable ? (
          <RunRail
            activities={runActivities}
            runId={activeRunId}
            isStreaming={isStreaming}
            onClear={() => {
              setRunActivities([]);
              setActiveRunId(undefined);
            }}
            open={runRailOpen}
            onClose={() => setRunRailOpen(false)}
          />
        ) : null}

      </div>
      ) : (
        <div className="workspace-page">
          {activeWorkspace === "communication" ? (
            <div className="communication-workspace">
              <header className="communication-workspace__header">
                <span className="panel-kicker">COMMUNICATION</span>
                <h1>Make every response fit the conversation.</h1>
                <p>
                  Review local communication preferences and record precise
                  corrections without turning subject matter into memory.
                </p>
              </header>
              <CommunicationProfilePanel
                profile={communicationProfile}
                isLoading={communicationLoading}
                error={communicationError}
                onRefresh={() => void loadCommunicationProfile()}
                onSubmitFeedback={recordCommunicationFeedback}
              />
            </div>
          ) : activeWorkspace === "settings" ? (
            <SettingsWorkspace isBusy={isStreaming} models={models} />
          ) : (
            <FeatureWorkspace
              workspace={activeWorkspace}
              selectedProjectId={selectedProjectId}
              onSelectProject={setSelectedProjectId}
              onProjectsChange={setBinderProjects}
            />
          )}
          <footer className="workspace-footer">
            <span>LOCAL-FIRST</span>
            <span>
              Not configured means no external connection or simulated data.
            </span>
          </footer>
        </div>
      )}
    </main>
  );
}

export default App;
