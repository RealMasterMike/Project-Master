import { describe, expect, it } from "vitest";
import {
  DAILY_UPDATE_CHECK_INTERVAL_MS,
  getReleaseChannelLabel,
  getUpdateCheckIntervalMs,
  shouldCheckForUpdates,
  WEEKLY_UPDATE_CHECK_INTERVAL_MS,
} from "./updatePolicy";

describe("update check schedule", () => {
  it.each([
    ["alpha", "Alpha"],
    ["beta", "Beta"],
    ["stable", "Stable"],
  ] as const)("formats the %s release channel for settings", (stage, label) => {
    expect(getReleaseChannelLabel(stage)).toBe(label);
  });

  it("checks alpha builds once every 24 hours", () => {
    expect(getUpdateCheckIntervalMs("alpha")).toBe(
      DAILY_UPDATE_CHECK_INTERVAL_MS,
    );
    expect(
      shouldCheckForUpdates({
        now: DAILY_UPDATE_CHECK_INTERVAL_MS - 1,
        lastAttemptAt: 0,
        stage: "alpha",
      }),
    ).toBe(false);
    expect(
      shouldCheckForUpdates({
        now: DAILY_UPDATE_CHECK_INTERVAL_MS,
        lastAttemptAt: 0,
        stage: "alpha",
      }),
    ).toBe(true);
  });

  it.each(["beta", "stable"] as const)(
    "checks %s builds once every seven days",
    (stage) => {
      expect(getUpdateCheckIntervalMs(stage)).toBe(
        WEEKLY_UPDATE_CHECK_INTERVAL_MS,
      );
      expect(
        shouldCheckForUpdates({
          now: WEEKLY_UPDATE_CHECK_INTERVAL_MS - 1,
          lastAttemptAt: 0,
          stage,
        }),
      ).toBe(false);
      expect(
        shouldCheckForUpdates({
          now: WEEKLY_UPDATE_CHECK_INTERVAL_MS,
          lastAttemptAt: 0,
          stage,
        }),
      ).toBe(true);
    },
  );

  it("checks immediately without a valid prior attempt", () => {
    expect(shouldCheckForUpdates({ now: 1, lastAttemptAt: null })).toBe(true);
    expect(shouldCheckForUpdates({ now: 1, lastAttemptAt: Number.NaN })).toBe(
      true,
    );
  });

  it("recovers when the clock moves behind the stored attempt", () => {
    expect(shouldCheckForUpdates({ now: 100, lastAttemptAt: 101 })).toBe(true);
  });
});
