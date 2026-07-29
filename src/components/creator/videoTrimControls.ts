export const TRIM_CONTROL_STEP_SECONDS = 0.01;
export const TRIM_NUDGE_SECONDS = 0.1;

const DURATION_TOLERANCE_SECONDS = 0.005;
const SPAN_TOLERANCE_SECONDS = 0.0001;

export type TrimBoundsIssue =
  | "duration_required"
  | "start_nonnegative"
  | "end_after_start"
  | "end_after_duration"
  | "span_too_short";

export interface TrimRange {
  startSeconds: number;
  endSeconds: number;
}

export type TrimPoint = "start" | "end";

export function finiteDuration(value?: number): number | undefined {
  return value !== undefined && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

export function parseTrimControlNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function validateTrimBounds(
  startSeconds: number | undefined,
  endSeconds: number | undefined,
  durationSeconds: number | undefined,
): TrimBoundsIssue | null {
  const duration = finiteDuration(durationSeconds);
  if (duration === undefined) return "duration_required";
  if (
    startSeconds === undefined ||
    !Number.isFinite(startSeconds) ||
    startSeconds < 0
  ) {
    return "start_nonnegative";
  }
  if (
    endSeconds === undefined ||
    !Number.isFinite(endSeconds) ||
    endSeconds <= startSeconds
  ) {
    return "end_after_start";
  }
  if (endSeconds > duration + DURATION_TOLERANCE_SECONDS) {
    return "end_after_duration";
  }
  if (
    endSeconds - startSeconds <
    TRIM_CONTROL_STEP_SECONDS - SPAN_TOLERANCE_SECONDS
  ) {
    return "span_too_short";
  }
  return null;
}

export function setTrimPointFromPlayhead(
  point: TrimPoint,
  playheadSeconds: number,
  range: TrimRange,
  durationSeconds: number,
): TrimRange | undefined {
  const duration = finiteDuration(durationSeconds);
  if (
    duration === undefined ||
    !Number.isFinite(playheadSeconds) ||
    validateTrimBounds(
      range.startSeconds,
      range.endSeconds,
      duration,
    ) !== null
  ) {
    return undefined;
  }

  if (point === "start") {
    return {
      startSeconds: clamp(
        playheadSeconds,
        0,
        range.endSeconds - TRIM_CONTROL_STEP_SECONDS,
      ),
      endSeconds: range.endSeconds,
    };
  }
  return {
    startSeconds: range.startSeconds,
    endSeconds: clamp(
      playheadSeconds,
      range.startSeconds + TRIM_CONTROL_STEP_SECONDS,
      duration,
    ),
  };
}

export function nudgeTrimPoint(
  point: TrimPoint,
  deltaSeconds: number,
  range: TrimRange,
  durationSeconds: number,
): TrimRange | undefined {
  const duration = finiteDuration(durationSeconds);
  if (
    duration === undefined ||
    !Number.isFinite(deltaSeconds) ||
    validateTrimBounds(
      range.startSeconds,
      range.endSeconds,
      duration,
    ) !== null
  ) {
    return undefined;
  }

  if (point === "start") {
    return {
      startSeconds: clamp(
        range.startSeconds + deltaSeconds,
        0,
        range.endSeconds - TRIM_CONTROL_STEP_SECONDS,
      ),
      endSeconds: range.endSeconds,
    };
  }
  return {
    startSeconds: range.startSeconds,
    endSeconds: clamp(
      range.endSeconds + deltaSeconds,
      range.startSeconds + TRIM_CONTROL_STEP_SECONDS,
      duration,
    ),
  };
}

export function clamp(
  value: number,
  minimum: number,
  maximum: number,
): number {
  return Math.min(maximum, Math.max(minimum, value));
}
