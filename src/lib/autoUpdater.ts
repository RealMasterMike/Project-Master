import { relaunch } from "@tauri-apps/plugin-process";
import {
  check,
  type DownloadEvent,
  type Update,
} from "@tauri-apps/plugin-updater";
import {
  CURRENT_RELEASE_STAGE,
  shouldCheckForUpdates,
} from "./updatePolicy";

const LAST_UPDATE_ATTEMPT_KEY = "master.update.lastAttemptAt.v1";
const UPDATE_REQUEST_TIMEOUT_MS = 15_000;

export interface UpdateDownloadProgress {
  downloadedBytes: number;
  totalBytes?: number;
  percent?: number;
}

export type UpdateCheckResult =
  | { status: "unavailable" }
  | { status: "skipped" }
  | { status: "current" }
  | { status: "available"; update: Update };

function isTauriRuntime(): boolean {
  return (
    typeof window !== "undefined" &&
    "__TAURI_INTERNALS__" in window
  );
}

function readLastAttemptAt(): number | null {
  try {
    const rawValue = window.localStorage.getItem(LAST_UPDATE_ATTEMPT_KEY);
    if (rawValue === null) return null;
    const value = Number(rawValue);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function recordAttemptAt(now: number): void {
  try {
    window.localStorage.setItem(LAST_UPDATE_ATTEMPT_KEY, String(now));
  } catch {
    // A disabled storage backend should not prevent a signed update check.
  }
}

export async function checkForAppUpdate(
  force = false,
  now = Date.now(),
): Promise<UpdateCheckResult> {
  if (!isTauriRuntime()) return { status: "unavailable" };

  if (
    !force &&
    !shouldCheckForUpdates({
      now,
      lastAttemptAt: readLastAttemptAt(),
      stage: CURRENT_RELEASE_STAGE,
    })
  ) {
    return { status: "skipped" };
  }

  // Record attempts before network I/O so an offline machine does not retry on
  // every launch. The next scheduled attempt will happen at the normal cadence.
  recordAttemptAt(now);
  const update = await check({ timeout: UPDATE_REQUEST_TIMEOUT_MS });
  return update ? { status: "available", update } : { status: "current" };
}

export async function installAppUpdate(
  update: Update,
  onProgress: (progress: UpdateDownloadProgress) => void,
): Promise<void> {
  let downloadedBytes = 0;
  let totalBytes: number | undefined;

  const reportProgress = (event: DownloadEvent): void => {
    if (event.event === "Started") {
      totalBytes = event.data.contentLength;
      downloadedBytes = 0;
    } else if (event.event === "Progress") {
      downloadedBytes += event.data.chunkLength;
    } else if (totalBytes !== undefined) {
      downloadedBytes = totalBytes;
    }

    onProgress({
      downloadedBytes,
      totalBytes,
      percent:
        totalBytes && totalBytes > 0
          ? Math.min(100, Math.round((downloadedBytes / totalBytes) * 100))
          : undefined,
    });
  };

  await update.downloadAndInstall(reportProgress, {
    timeout: UPDATE_REQUEST_TIMEOUT_MS,
  });
  await relaunch();
}
