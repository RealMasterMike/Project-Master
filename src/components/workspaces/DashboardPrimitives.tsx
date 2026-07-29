import {
  useCallback,
  useState,
  type ReactNode,
} from "react";

import { formatProjectMasterError } from "../../lib/projectMasterApi";

interface DashboardFrameProps {
  eyebrow: string;
  title: string;
  description: string;
  status: string;
  children: ReactNode;
  error?: string | null;
  busy?: boolean;
  onRefresh: () => void;
}

export function DashboardFrame({
  eyebrow,
  title,
  description,
  status,
  children,
  error,
  busy,
  onRefresh,
}: DashboardFrameProps) {
  return (
    <section
      className="feature-workspace feature-workspace--dashboard"
      aria-busy={busy || undefined}
    >
      <div className="feature-workspace__copy">
        <span className="feature-workspace__eyebrow">{eyebrow}</span>
        <div className="feature-workspace__heading">
          <div>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          <button
            className="feature-status feature-status--ready"
            type="button"
            onClick={onRefresh}
            disabled={busy}
            aria-label={
              busy ? `${title} action in progress` : `${status} ${title}`
            }
          >
            <span aria-live="polite">{busy ? "Working…" : status}</span>
          </button>
        </div>
        {error ? (
          <div className="dashboard-alert" role="alert">
            {error}
          </div>
        ) : null}
        <div className="dashboard-grid">{children}</div>
      </div>
    </section>
  );
}

export function Panel({
  title,
  kicker,
  children,
  wide = false,
}: {
  title: string;
  kicker: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <section className={`dashboard-panel ${wide ? "dashboard-panel--wide" : ""}`}>
      <header>
        <span>{kicker}</span>
        <h2>{title}</h2>
      </header>
      <div className="dashboard-panel__body">{children}</div>
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="dashboard-empty">{children}</p>;
}

export function Stamp({ value }: { value?: string }) {
  if (!value) return null;
  const date = new Date(value);
  return (
    <time dateTime={Number.isNaN(date.valueOf()) ? undefined : date.toISOString()}>
      {Number.isNaN(date.valueOf()) ? value : date.toLocaleString()}
    </time>
  );
}

export function useBusyAction(refresh: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = useCallback(
    async (operation: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await operation();
        await refresh();
      } catch (caught) {
        setError(formatProjectMasterError(caught));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );
  return { busy, error, setError, act };
}
