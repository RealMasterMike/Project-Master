import type { ProjectMasterConversation } from "../lib/projectMasterApi";

interface ConversationLibraryProps {
  conversations: ProjectMasterConversation[];
  activeConversationId?: string;
  isBusy: boolean;
  isLoading: boolean;
  error: string | null;
  onNewSession: () => void;
  onOpenConversation: (conversationId: string) => void;
  onRetry: () => void;
}

function conversationLabel(conversation: ProjectMasterConversation): string {
  const title = conversation.title?.trim();
  return title || "Untitled session";
}

function conversationMeta(conversation: ProjectMasterConversation): string {
  const messages = `${conversation.messageCount} message${conversation.messageCount === 1 ? "" : "s"}`;
  const started = new Date(conversation.startedAt);
  if (Number.isNaN(started.getTime())) return messages;
  return `${messages} · ${started.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export function ConversationLibrary({
  conversations,
  activeConversationId,
  isBusy,
  isLoading,
  error,
  onNewSession,
  onOpenConversation,
  onRetry,
}: ConversationLibraryProps) {
  return (
    <aside className="conversation-library" aria-label="Conversation library">
      <div className="conversation-library__header">
        <div>
          <span className="conversation-library__kicker">WORKSPACE</span>
          <h2>Sessions</h2>
        </div>
        <button
          className="button button--primary conversation-library__new"
          type="button"
          onClick={onNewSession}
          disabled={isBusy}
        >
          New
        </button>
      </div>

      {error ? (
        <div className="conversation-library__notice" role="alert">
          <span>{error}</span>
          <button className="button button--secondary" type="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : null}

      {isLoading ? <p className="conversation-library__status">Loading sessions…</p> : null}

      {!isLoading && !error && conversations.length === 0 ? (
        <p className="conversation-library__status">
          Your conversations will appear here after the first reply.
        </p>
      ) : null}

      <nav className="conversation-library__list" aria-label="Saved conversations">
        {conversations.map((conversation) => (
          <button
            className={`conversation-library__item${
              conversation.id === activeConversationId ? " is-active" : ""
            }`}
            type="button"
            key={conversation.id}
            onClick={() => onOpenConversation(conversation.id)}
            disabled={isBusy}
            aria-current={conversation.id === activeConversationId ? "page" : undefined}
          >
            <strong>{conversationLabel(conversation)}</strong>
            <span>{conversationMeta(conversation)}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
