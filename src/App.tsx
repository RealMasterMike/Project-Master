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
import {
  ensureManagedBackend,
  formatProjectMasterError,
  getModelStatus,
  isAbortError,
  ProjectMasterUnavailableError,
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
  conversationId?: string;
}

let nextMessageId = 0;

function createMessageId(role: UiMessage["role"]): string {
  nextMessageId += 1;
  return `${role}-${Date.now()}-${nextMessageId}`;
}

function App() {
  const [models, setModels] = useState<ProjectMasterModel[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [conversationId, setConversationId] = useState<string>();
  const [contextLength, setContextLength] = useState(32768);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("checking");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [composer, setComposer] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const modelLoadControllerRef = useRef<AbortController | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const retryRequestsRef = useRef(new Map<string, RetryRequest>());
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

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
      if (controller.signal.aborted) {
        return;
      }

      setModels(availableModels);
      setSelectedModel((currentModel) => {
        if (availableModels.some((model) => model.name === currentModel)) {
          return currentModel;
        }
        return availableModels.some((model) => model.name === status.configuredModel)
          ? status.configuredModel
          : availableModels[0]?.name ?? "";
      });
      setContextLength(status.contextLength);
      setConnectionState(
        status.ollamaReachable && availableModels.length > 0
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
    };
  }, [loadAvailableModels]);

  useEffect(() => {
    return () => {
      streamControllerRef.current?.abort();
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
    streamControllerRef.current = controller;
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
        model: request.model,
        message: request.message,
        conversationId: request.conversationId,
        signal: controller.signal,
        onConversation: setConversationId,
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
      if (streamControllerRef.current === controller) {
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
      conversationId,
    };

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
    streamControllerRef.current?.abort();
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
        await runAssistantResponse(messageId, request);
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

  const canSend = Boolean(composer.trim() && selectedModel && !isStreaming);
  const connectionLabel =
    connectionState === "checking"
      ? "Checking backend"
      : connectionState === "ready"
        ? `${models.length} local model${models.length === 1 ? "" : "s"}`
        : connectionState === "empty"
          ? "No models installed"
          : "Backend offline";
  const composerPlaceholder = isStreaming
    ? "MASTER is responding…"
    : connectionState === "offline"
      ? "Project Master is offline — press Retry"
      : connectionState === "empty"
        ? "Install an Ollama model to begin"
        : "Message MASTER";

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
            <span className="brand-subtitle">LOCAL INTELLIGENCE · ALPHA v0.1.1</span>
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

          <label className="model-control" htmlFor="model-select">
            <span>Model</span>
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
                  <option key={model.name} value={model.name}>
                    {model.name}
                  </option>
                ))
              )}
            </select>
          </label>

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

      <section
        className="message-list"
        ref={messageListRef}
        aria-label="Conversation"
      >
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
              <strong>No Ollama models installed</strong>
              <p>
                Run <code>ollama pull &lt;model-name&gt;</code>, then retry.
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
          <span>Enter to send</span>
          <span>Shift+Enter for a new line</span>
          <span>{contextLength.toLocaleString()} token context</span>
        </div>
      </footer>
    </main>
  );
}

export default App;
