import { getVersion } from "@tauri-apps/api/app";
import type { Update } from "@tauri-apps/plugin-updater";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  checkForAppUpdate,
  installAppUpdate,
  isAppUpdateRuntime,
  type UpdateDownloadProgress,
} from "../lib/autoUpdater";
import {
  resetAppPreferences,
  updateAppPreferences,
} from "../lib/appPreferences";
import {
  formatProjectMasterError,
  getToolStatus,
  isCuratedUncensoredVisionModel,
  isVisionCapableModel,
  type ProjectMasterModel,
} from "../lib/projectMasterApi";
import {
  CURRENT_RELEASE_STAGE,
  DAILY_UPDATE_CHECK_INTERVAL_MS,
  getReleaseChannelLabel,
  getUpdateCheckIntervalMs,
} from "../lib/updatePolicy";
import {
  presentWebToolStatus,
  type WebToolStatusSnapshot,
} from "../lib/toolStatusPresentation";
import { useAppPreferences } from "../hooks/useAppPreferences";

type UpdatePhase =
  | "idle"
  | "unavailable"
  | "checking"
  | "current"
  | "available"
  | "installing"
  | "error";

interface SettingsWorkspaceProps {
  isBusy: boolean;
  models: ProjectMasterModel[];
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The signed update service could not be reached.";
}

export function SettingsWorkspace({ isBusy, models }: SettingsWorkspaceProps) {
  const preferences = useAppPreferences();
  const visionModels = models.filter(isVisionCapableModel);
  const preferredVisionModelInstalled = visionModels.some(
    (model) =>
      model.name.toLocaleLowerCase() ===
      preferences.preferredVisionModel.toLocaleLowerCase(),
  );
  const updaterAvailable = isAppUpdateRuntime();
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [versionUnavailable, setVersionUnavailable] = useState(!updaterAvailable);
  const [phase, setPhase] = useState<UpdatePhase>(
    updaterAvailable ? "idle" : "unavailable",
  );
  const [update, setUpdate] = useState<Update | null>(null);
  const [progress, setProgress] = useState<UpdateDownloadProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [webTools, setWebTools] = useState<WebToolStatusSnapshot | null>(null);
  const [webToolsLoading, setWebToolsLoading] = useState(false);
  const [webToolsError, setWebToolsError] = useState<string | null>(null);
  const activeUpdateRef = useRef<Update | null>(null);
  const webToolsControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const installingRef = useRef(false);
  const webToolPresentation = presentWebToolStatus(
    webTools,
    webToolsLoading,
    webToolsError,
  );

  const loadWebToolStatus = useCallback(async () => {
    webToolsControllerRef.current?.abort();
    const controller = new AbortController();
    webToolsControllerRef.current = controller;
    setWebToolsLoading(true);
    setWebTools(null);
    setWebToolsError(null);
    try {
      const status = await getToolStatus(controller.signal);
      if (controller.signal.aborted) return;
      const tools = new Map(
        status.tools.map((tool) => [tool.name, tool.enabled] as const),
      );
      setWebTools({
        fetchEnabled: tools.get("web_fetch") === true,
        searchEnabled: tools.get("web_search") === true,
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        setWebToolsError(formatProjectMasterError(error));
      }
    } finally {
      if (webToolsControllerRef.current === controller) {
        webToolsControllerRef.current = null;
        setWebToolsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadWebToolStatus();
    return () => webToolsControllerRef.current?.abort();
  }, [loadWebToolStatus]);

  useEffect(() => {
    let cancelled = false;
    mountedRef.current = true;

    if (updaterAvailable) {
      void getVersion()
        .then((version) => {
          if (!cancelled) setAppVersion(version);
        })
        .catch(() => {
          if (!cancelled) setVersionUnavailable(true);
        });
    }

    return () => {
      cancelled = true;
      mountedRef.current = false;
      const activeUpdate = activeUpdateRef.current;
      if (activeUpdate && !installingRef.current) {
        activeUpdateRef.current = null;
        void activeUpdate.close();
      }
    };
  }, [updaterAvailable]);

  const discardActiveUpdate = async (): Promise<void> => {
    const activeUpdate = activeUpdateRef.current;
    activeUpdateRef.current = null;
    setUpdate(null);
    if (activeUpdate) {
      try {
        await activeUpdate.close();
      } catch {
        // The updater resource may already have closed during an app relaunch.
      }
    }
  };

  const checkNow = async (): Promise<void> => {
    await discardActiveUpdate();
    setPhase("checking");
    setProgress(null);
    setUpdateError(null);

    try {
      const result = await checkForAppUpdate(true);
      if (!mountedRef.current) {
        if (result.status === "available") void result.update.close();
        return;
      }
      if (result.status === "available") {
        activeUpdateRef.current = result.update;
        setUpdate(result.update);
        setPhase("available");
      } else if (result.status === "unavailable") {
        setPhase("unavailable");
      } else {
        setPhase("current");
      }
    } catch (error) {
      if (!mountedRef.current) return;
      setUpdateError(errorMessage(error));
      setPhase("error");
    }
  };

  const install = async (): Promise<void> => {
    if (!update) return;

    setPhase("installing");
    setProgress({ downloadedBytes: 0 });
    setUpdateError(null);
    installingRef.current = true;
    try {
      await installAppUpdate(update, (nextProgress) => {
        if (mountedRef.current) setProgress(nextProgress);
      });
    } catch (error) {
      if (mountedRef.current) {
        setUpdateError(errorMessage(error));
        setPhase("error");
      }
    } finally {
      installingRef.current = false;
      if (
        !mountedRef.current &&
        activeUpdateRef.current === update
      ) {
        activeUpdateRef.current = null;
        void update.close();
      }
    }
  };

  const automaticCheckDays =
    getUpdateCheckIntervalMs(CURRENT_RELEASE_STAGE) /
    DAILY_UPDATE_CHECK_INTERVAL_MS;
  const progressText =
    progress?.percent !== undefined
      ? `${progress.percent}% downloaded`
      : "Downloading and verifying the signed update…";

  let statusTitle = "Ready to check";
  let statusDescription =
    "Run a manual check against the configured Project Master release feed.";
  if (phase === "unavailable") {
    statusTitle = "Updater unavailable";
    statusDescription =
      "Update checks are available in the installed desktop app, not the browser preview.";
  } else if (phase === "checking") {
    statusTitle = "Checking for updates…";
    statusDescription = "Contacting the signed Project Master release feed.";
  } else if (phase === "current") {
    statusTitle = "Project Master is up to date";
    statusDescription = appVersion
      ? `Version ${appVersion} is the newest ${getReleaseChannelLabel(
          CURRENT_RELEASE_STAGE,
        ).toLowerCase()} release.`
      : "No newer signed release is available.";
  } else if (phase === "available" && update) {
    statusTitle = `Version ${update.version} is available`;
    statusDescription =
      "Project Master will verify the signature, install the release, and restart only after you approve.";
  } else if (phase === "installing") {
    statusTitle = "Installing signed update";
    statusDescription = progressText;
  } else if (phase === "error") {
    statusTitle = update ? "The update could not be installed" : "Update check failed";
    statusDescription =
      updateError ?? "The signed update service could not complete the request.";
  }

  const checking = phase === "checking";
  const installing = phase === "installing";

  return (
    <section className="settings-workspace">
      <div className="settings-workspace__content">
        <header className="settings-workspace__header">
          <span className="panel-kicker">SETTINGS</span>
          <h1>Application settings</h1>
          <p>
            Personalize the interface and Creator defaults, then review this
            installation and its signed update channel.
          </p>
        </header>

        <div className="settings-card">
          <header className="settings-card__header">
            <div>
              <span className="panel-kicker">INTERFACE</span>
              <h2>Appearance and motion</h2>
            </div>
            <button
              className="button button--secondary settings-reset"
              type="button"
              onClick={() => resetAppPreferences()}
            >
              Reset defaults
            </button>
          </header>

          <div className="settings-preference-list">
            <label className="settings-preference">
              <span>
                <strong>Interface density</strong>
                <small>
                  Compact mode reduces spacing while keeping controls usable.
                </small>
              </span>
              <select
                value={preferences.interfaceDensity}
                onChange={(event) =>
                  updateAppPreferences({
                    interfaceDensity: event.currentTarget.value as
                      | "comfortable"
                      | "compact",
                  })
                }
              >
                <option value="comfortable">Comfortable</option>
                <option value="compact">Compact</option>
              </select>
            </label>

            <label className="settings-preference">
              <span>
                <strong>Text size</strong>
                <small>Adjust interface text without changing media scale.</small>
              </span>
              <select
                value={preferences.textScale}
                onChange={(event) =>
                  updateAppPreferences({
                    textScale: event.currentTarget.value as
                      | "small"
                      | "medium"
                      | "large",
                  })
                }
              >
                <option value="small">Small</option>
                <option value="medium">Standard</option>
                <option value="large">Large</option>
              </select>
            </label>

            <label className="settings-preference">
              <span>
                <strong>Animation</strong>
                <small>
                  Reduced motion also disables smooth scrolling and pulse
                  effects.
                </small>
              </span>
              <select
                value={preferences.motion}
                onChange={(event) =>
                  updateAppPreferences({
                    motion: event.currentTarget.value as "system" | "reduced",
                  })
                }
              >
                <option value="system">Follow system</option>
                <option value="reduced">Reduce motion</option>
              </select>
            </label>

          </div>
        </div>

        <div className="settings-card">
          <header className="settings-card__header">
            <div>
              <span className="panel-kicker">CREATOR</span>
              <h2>Studio defaults</h2>
            </div>
            <span className="settings-update-state">LOCAL</span>
          </header>

          <div className="settings-preference-list">
            <label className="settings-preference settings-preference--toggle">
              <span>
                <strong>Automatic media previews</strong>
                <small>
                  Load verified previews as cards enter view. Large off-screen
                  assets stay unloaded.
                </small>
              </span>
              <input
                type="checkbox"
                checked={preferences.autoLoadMediaPreviews}
                onChange={(event) =>
                  updateAppPreferences({
                    autoLoadMediaPreviews: event.currentTarget.checked,
                  })
                }
              />
            </label>

            <label className="settings-preference">
              <span>
                <strong>Default generation type</strong>
                <small>
                  Creator still keeps image and video workflows in separate
                  lists.
                </small>
              </span>
              <select
                value={preferences.creatorGenerationDefault}
                onChange={(event) =>
                  updateAppPreferences({
                    creatorGenerationDefault: event.currentTarget.value as
                      | "image"
                      | "video",
                  })
                }
              >
                <option value="image">Image</option>
                <option value="video">Video</option>
              </select>
            </label>

            <label className="settings-preference">
              <span>
                <strong>Image analysis model</strong>
                <small>
                  Automatic uses the documented uncensored vision model only
                  when its exact tag and tested manifest are installed. Other
                  listed models are manual / unverified and require your
                  explicit choice.
                </small>
              </span>
              <select
                value={preferences.preferredVisionModel}
                onChange={(event) =>
                  updateAppPreferences({
                    preferredVisionModel: event.currentTarget.value,
                  })
                }
              >
                <option value="">Automatic · uncensored default only</option>
                {preferences.preferredVisionModel &&
                !preferredVisionModelInstalled ? (
                  <option value={preferences.preferredVisionModel} disabled>
                    {preferences.preferredVisionModel} · manual / unverified ·
                    not installed
                  </option>
                ) : null}
                {visionModels.map((model) => (
                  <option key={model.name} value={model.name}>
                    {model.name} ·{" "}
                    {isCuratedUncensoredVisionModel(model)
                      ? "curated uncensored default"
                      : "manual / unverified"}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p className="settings-update-security">
            Chat permissions remain session-only. Project changes and outbound
            web access are never enabled here or remembered automatically.
          </p>
        </div>

        <div className="settings-card">
          <header className="settings-card__header">
            <div>
              <span className="panel-kicker">WEB TOOLS</span>
              <h2>Search and page reading</h2>
            </div>
            <span className="settings-update-state">
              {webToolPresentation.headline}
            </span>
          </header>

          <dl className="settings-metadata">
            <div>
              <dt>Public page reading</dt>
              <dd>{webToolPresentation.pageReading}</dd>
            </div>
            <div>
              <dt>Web search</dt>
              <dd>{webToolPresentation.webSearch}</dd>
            </div>
            <div>
              <dt>Search filtering</dt>
              <dd>{webToolPresentation.searchFiltering}</dd>
            </div>
          </dl>

          {webToolsError ? (
            <div
              className="settings-update-result settings-update-result--error"
              role="alert"
            >
              <span
                className="settings-update-result__dot"
                aria-hidden="true"
              />
              <div>
                <strong>Tool status could not be loaded</strong>
                <p>{webToolsError}</p>
              </div>
            </div>
          ) : (
            <p className="settings-update-security">
              Page reading works without a search provider. Full search uses
              the optional <code>MASTER_SEARXNG_URL</code> backend setting.
              Either tool still requires the conversation-only{" "}
              <strong>Allow web access</strong> switch.
            </p>
          )}

          <div className="settings-update-actions">
            <button
              className="button button--secondary"
              type="button"
              disabled={webToolsLoading || isBusy}
              onClick={() => void loadWebToolStatus()}
            >
              {webToolsLoading ? "Checking…" : "Recheck web tools"}
            </button>
          </div>
        </div>

        <div className="settings-card">
          <header className="settings-card__header">
            <div>
              <span className="panel-kicker">APPLICATION</span>
              <h2>Updates</h2>
            </div>
            <span
              className={`settings-update-state settings-update-state--${phase}`}
            >
              {phase === "available"
                ? "UPDATE AVAILABLE"
                : phase === "current"
                  ? "CURRENT"
                  : phase === "error"
                    ? "ACTION NEEDED"
                    : phase === "checking" || phase === "installing"
                      ? "WORKING"
                      : updaterAvailable
                        ? "MANUAL"
                        : "UNAVAILABLE"}
            </span>
          </header>

          <dl className="settings-metadata">
            <div>
              <dt>Installed version</dt>
              <dd>
                {appVersion
                  ? `v${appVersion}`
                  : versionUnavailable
                    ? updaterAvailable
                      ? "Could not read app version"
                      : "Unavailable outside the packaged app"
                    : "Reading app version…"}
              </dd>
            </div>
            <div>
              <dt>Release channel</dt>
              <dd>{getReleaseChannelLabel(CURRENT_RELEASE_STAGE)}</dd>
            </div>
            <div>
              <dt>Automatic check</dt>
              <dd>
                Every {automaticCheckDays} day
                {automaticCheckDays === 1 ? "" : "s"}
              </dd>
            </div>
          </dl>

          <div
            className={`settings-update-result settings-update-result--${phase}`}
            role={phase === "error" ? "alert" : "status"}
            aria-busy={checking || installing}
            aria-live={phase === "error" ? "assertive" : "polite"}
          >
            <span className="settings-update-result__dot" aria-hidden="true" />
            <div>
              <strong>{statusTitle}</strong>
              <p>{statusDescription}</p>
              {installing && progress?.percent !== undefined ? (
                <progress
                  aria-label="Signed update download progress"
                  max={100}
                  value={progress.percent}
                >
                  {progress.percent}%
                </progress>
              ) : null}
            </div>
          </div>

          <div className="settings-update-actions">
            <button
              className="button button--secondary"
              type="button"
              disabled={!updaterAvailable || checking || installing}
              onClick={() => void checkNow()}
            >
              {checking ? "Checking…" : "Check for updates"}
            </button>
            {update ? (
              <button
                className="button button--primary"
                type="button"
                disabled={installing || isBusy}
                title={
                  isBusy
                    ? "Finish the current response before installing an update"
                    : undefined
                }
                onClick={() => void install()}
              >
                {installing ? "Updating…" : "Update and restart"}
              </button>
            ) : null}
          </div>

          <p className="settings-update-security">
            Updates are never installed silently. The existing updater verifies
            the release signature before installation, then restarts Project
            Master to finish.
          </p>
        </div>
      </div>
    </section>
  );
}
