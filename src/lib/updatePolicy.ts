export type ReleaseStage = "alpha" | "beta" | "stable";

export const CURRENT_RELEASE_STAGE: ReleaseStage = "alpha";
export const DAILY_UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1_000;
export const WEEKLY_UPDATE_CHECK_INTERVAL_MS = 7 * DAILY_UPDATE_CHECK_INTERVAL_MS;

export function getUpdateCheckIntervalMs(stage: ReleaseStage): number {
  return stage === "alpha"
    ? DAILY_UPDATE_CHECK_INTERVAL_MS
    : WEEKLY_UPDATE_CHECK_INTERVAL_MS;
}

interface UpdateCheckSchedule {
  now: number;
  lastAttemptAt: number | null;
  stage?: ReleaseStage;
}

export function shouldCheckForUpdates({
  now,
  lastAttemptAt,
  stage = CURRENT_RELEASE_STAGE,
}: UpdateCheckSchedule): boolean {
  if (
    lastAttemptAt === null ||
    !Number.isFinite(lastAttemptAt) ||
    lastAttemptAt < 0
  ) {
    return true;
  }

  // Recover instead of suppressing updates indefinitely when the system clock
  // is moved backwards after a check was recorded.
  if (lastAttemptAt > now) {
    return true;
  }

  return now - lastAttemptAt >= getUpdateCheckIntervalMs(stage);
}
