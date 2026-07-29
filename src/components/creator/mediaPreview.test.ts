import { describe, expect, it } from "vitest";

import {
  mediaPreviewKind,
  previewSizeMismatch,
  shouldAutoLoadPreview,
} from "./mediaPreview";

describe("Creator media preview policy", () => {
  it("recognizes browser-previewable media types without case sensitivity", () => {
    expect(mediaPreviewKind("image/png")).toBe("image");
    expect(mediaPreviewKind(" VIDEO/MP4 ")).toBe("video");
    expect(mediaPreviewKind("Audio/WAV")).toBe("audio");
    expect(mediaPreviewKind("application/pdf")).toBeUndefined();
    expect(mediaPreviewKind("")).toBeUndefined();
  });

  it("automatically loads only verified, previewable media when enabled", () => {
    expect(shouldAutoLoadPreview("image", true)).toBe(true);
    expect(shouldAutoLoadPreview("video", false)).toBe(false);
    expect(shouldAutoLoadPreview(undefined, true)).toBe(false);
    expect(shouldAutoLoadPreview("audio", true, false)).toBe(false);
  });

  it("requires downloaded bytes to match the verified manifest", () => {
    expect(previewSizeMismatch(4096, 4096)).toBe(false);
    expect(previewSizeMismatch(4095, 4096)).toBe(true);
  });
});
