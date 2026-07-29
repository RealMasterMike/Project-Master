export type MediaPreviewKind = "image" | "video" | "audio";

export function mediaPreviewKind(
  mediaType: string,
): MediaPreviewKind | undefined {
  const normalized = mediaType.trim().toLowerCase();
  if (normalized.startsWith("image/")) return "image";
  if (normalized.startsWith("video/")) return "video";
  if (normalized.startsWith("audio/")) return "audio";
  return undefined;
}

export function previewSizeMismatch(
  actualSize: number,
  expectedSize: number,
): boolean {
  return actualSize !== expectedSize;
}

export function shouldAutoLoadPreview(
  kind: MediaPreviewKind | undefined,
  verified: boolean,
  enabled = true,
): boolean {
  return enabled && verified && kind !== undefined;
}
