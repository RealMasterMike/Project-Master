import { useEffect, useRef, useState } from "react";
import type { Update } from "@tauri-apps/plugin-updater";
import {
  checkForAppUpdate,
  installAppUpdate,
  type UpdateDownloadProgress,
} from "../lib/autoUpdater";

const STARTUP_UPDATE_DELAY_MS = 8_000;

interface UpdateNoticeProps {
  isBusy: boolean;
}

export function UpdateNotice({ isBusy }: UpdateNoticeProps) {
  const [update, setUpdate] = useState<Update | null>(null);
  const [progress, setProgress] = useState<UpdateDownloadProgress | null>(null);
  const [isInstalling, setIsInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const activeUpdateRef = useRef<Update | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void checkForAppUpdate()
        .then((result) => {
          if (result.status !== "available") return;
          if (cancelled) {
            void result.update.close();
            return;
          }
          activeUpdateRef.current = result.update;
          setUpdate(result.update);
        })
        .catch((error: unknown) => {
          // Automatic update failures stay quiet. A later scheduled check will
          // retry, while the rest of the local-first application remains usable.
          console.warn("Project Master update check failed", error);
        });
    }, STARTUP_UPDATE_DELAY_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      const activeUpdate = activeUpdateRef.current;
      if (activeUpdate) void activeUpdate.close();
    };
  }, []);

  if (!update) return <div className="update-notice-slot" />;

  const dismiss = (): void => {
    activeUpdateRef.current = null;
    setUpdate(null);
    void update.close();
  };

  const install = async (): Promise<void> => {
    setIsInstalling(true);
    setInstallError(null);
    setProgress({ downloadedBytes: 0 });
    try {
      await installAppUpdate(update, setProgress);
    } catch (error) {
      setInstallError(
        error instanceof Error ? error.message : "The signed update could not be installed.",
      );
      setIsInstalling(false);
    }
  };

  const progressText =
    progress?.percent !== undefined
      ? `${progress.percent}% downloaded`
      : isInstalling
        ? "Downloading signed update…"
        : null;

  return (
    <div className="update-notice-slot">
      <aside className="update-notice" role="status" aria-live="polite">
      <div className="update-notice__copy">
        <span className="update-notice__eyebrow">SIGNED UPDATE AVAILABLE</span>
        <strong>Project Master v{update.version} is ready.</strong>
        <p>
          {installError ??
            progressText ??
            "Download it now and Project Master will restart to finish the update."}
        </p>
        {isInstalling && progress?.percent !== undefined ? (
          <progress max={100} value={progress.percent}>
            {progress.percent}%
          </progress>
        ) : null}
      </div>
      <div className="update-notice__actions">
        <button
          className="button button--secondary"
          type="button"
          disabled={isInstalling}
          onClick={dismiss}
        >
          Later
        </button>
        <button
          className="button button--primary"
          type="button"
          disabled={isInstalling || isBusy}
          title={isBusy ? "Finish the current response before updating" : undefined}
          onClick={() => void install()}
        >
          {isInstalling ? "Updating…" : "Update and restart"}
        </button>
      </div>
      </aside>
    </div>
  );
}
