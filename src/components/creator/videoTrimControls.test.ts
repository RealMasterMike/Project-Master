import { describe, expect, it } from "vitest";

import {
  TRIM_NUDGE_SECONDS,
  nudgeTrimPoint,
  setTrimPointFromPlayhead,
  validateTrimBounds,
  type TrimRange,
} from "./videoTrimControls";

const DURATION_SECONDS = 10;
const RANGE: TrimRange = {
  startSeconds: 2,
  endSeconds: 8,
};

describe("video trim bounds", () => {
  it("reports each authoritative bounds failure", () => {
    expect(validateTrimBounds(0, 1, undefined)).toBe("duration_required");
    expect(validateTrimBounds(0, 1, Number.POSITIVE_INFINITY)).toBe(
      "duration_required",
    );
    expect(validateTrimBounds(-0.01, 1, DURATION_SECONDS)).toBe(
      "start_nonnegative",
    );
    expect(validateTrimBounds(Number.NaN, 1, DURATION_SECONDS)).toBe(
      "start_nonnegative",
    );
    expect(validateTrimBounds(2, 2, DURATION_SECONDS)).toBe(
      "end_after_start",
    );
    expect(validateTrimBounds(2, 10.01, DURATION_SECONDS)).toBe(
      "end_after_duration",
    );
    expect(validateTrimBounds(2, 2.005, DURATION_SECONDS)).toBe(
      "span_too_short",
    );
    expect(validateTrimBounds(2, 8, DURATION_SECONDS)).toBeNull();
  });
});

describe("video trim playhead controls", () => {
  it("sets each point while preserving its valid opposite point", () => {
    expect(
      setTrimPointFromPlayhead("start", 4.25, RANGE, DURATION_SECONDS),
    ).toEqual({
      startSeconds: 4.25,
      endSeconds: 8,
    });
    expect(
      setTrimPointFromPlayhead("end", 6.75, RANGE, DURATION_SECONDS),
    ).toEqual({
      startSeconds: 2,
      endSeconds: 6.75,
    });
    expect(RANGE).toEqual({ startSeconds: 2, endSeconds: 8 });
  });

  it("clamps playhead points to the duration and current opposite point", () => {
    const clampedStart = setTrimPointFromPlayhead(
      "start",
      99,
      RANGE,
      DURATION_SECONDS,
    );
    const clampedEnd = setTrimPointFromPlayhead(
      "end",
      -99,
      RANGE,
      DURATION_SECONDS,
    );

    expect(clampedStart).toEqual({
      startSeconds: 7.99,
      endSeconds: 8,
    });
    expect(clampedEnd).toEqual({
      startSeconds: 2,
      endSeconds: 2.01,
    });
    expect(
      validateTrimBounds(
        clampedStart?.startSeconds,
        clampedStart?.endSeconds,
        DURATION_SECONDS,
      ),
    ).toBeNull();
    expect(
      validateTrimBounds(
        clampedEnd?.startSeconds,
        clampedEnd?.endSeconds,
        DURATION_SECONDS,
      ),
    ).toBeNull();
  });

  it("refuses non-finite playheads and invalid existing ranges", () => {
    expect(
      setTrimPointFromPlayhead(
        "start",
        Number.NaN,
        RANGE,
        DURATION_SECONDS,
      ),
    ).toBeUndefined();
    expect(
      setTrimPointFromPlayhead(
        "end",
        4,
        { startSeconds: 8, endSeconds: 2 },
        DURATION_SECONDS,
      ),
    ).toBeUndefined();
  });
});

describe("video trim nudges", () => {
  it("nudges either point by the small configured interval", () => {
    expect(
      nudgeTrimPoint(
        "start",
        TRIM_NUDGE_SECONDS,
        RANGE,
        DURATION_SECONDS,
      ),
    ).toEqual({
      startSeconds: 2.1,
      endSeconds: 8,
    });
    expect(
      nudgeTrimPoint(
        "end",
        -TRIM_NUDGE_SECONDS,
        RANGE,
        DURATION_SECONDS,
      ),
    ).toEqual({
      startSeconds: 2,
      endSeconds: 7.9,
    });
  });

  it("bounds nudges without crossing the opposite point or duration", () => {
    const earliestStart = nudgeTrimPoint(
      "start",
      -99,
      RANGE,
      DURATION_SECONDS,
    );
    const latestStart = nudgeTrimPoint(
      "start",
      99,
      RANGE,
      DURATION_SECONDS,
    );
    const earliestEnd = nudgeTrimPoint(
      "end",
      -99,
      RANGE,
      DURATION_SECONDS,
    );
    const latestEnd = nudgeTrimPoint(
      "end",
      99,
      RANGE,
      DURATION_SECONDS,
    );

    expect(earliestStart?.startSeconds).toBe(0);
    expect(latestStart?.startSeconds).toBe(7.99);
    expect(earliestEnd?.endSeconds).toBe(2.01);
    expect(latestEnd?.endSeconds).toBe(DURATION_SECONDS);
    for (const adjusted of [
      earliestStart,
      latestStart,
      earliestEnd,
      latestEnd,
    ]) {
      expect(
        validateTrimBounds(
          adjusted?.startSeconds,
          adjusted?.endSeconds,
          DURATION_SECONDS,
        ),
      ).toBeNull();
    }
  });

  it("refuses invalid deltas and existing ranges", () => {
    expect(
      nudgeTrimPoint("start", Number.POSITIVE_INFINITY, RANGE, DURATION_SECONDS),
    ).toBeUndefined();
    expect(
      nudgeTrimPoint(
        "end",
        TRIM_NUDGE_SECONDS,
        { startSeconds: 8, endSeconds: 2 },
        DURATION_SECONDS,
      ),
    ).toBeUndefined();
  });
});
