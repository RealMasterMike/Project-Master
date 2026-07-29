import type {
  MediaAssetKind,
  MediaAssetSummary,
} from "./projectMasterApi";

export type MediaLibraryFilter = "all" | MediaAssetKind;
export type MediaLibrarySort = "newest" | "oldest" | "name" | "size";

export interface MediaLibrarySelection {
  filter: MediaLibraryFilter;
  query: string;
  sort: MediaLibrarySort;
}

const NAME_COLLATOR = new Intl.Collator("en", {
  numeric: true,
  sensitivity: "base",
});

function normalizedSearchText(value: string): string {
  return value.normalize("NFKC").toLocaleLowerCase("en-US");
}

export function mediaSourceLabel(source: string): string {
  if (source === "upload") return "Local import";
  if (source === "trim") return "Video trim";
  if (source === "comfyui") return "ComfyUI generation";
  return source.replace(/_/g, " ");
}

function compareIdentity(
  left: MediaAssetSummary,
  right: MediaAssetSummary,
): number {
  const nameOrder = NAME_COLLATOR.compare(left.name, right.name);
  if (nameOrder !== 0) return nameOrder;
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
}

function createdAtMillis(asset: MediaAssetSummary): number | undefined {
  const value = Date.parse(asset.createdAt);
  return Number.isFinite(value) ? value : undefined;
}

function compareCreatedAt(
  left: MediaAssetSummary,
  right: MediaAssetSummary,
  direction: "newest" | "oldest",
): number {
  const leftTime = createdAtMillis(left);
  const rightTime = createdAtMillis(right);

  // Unknown timestamps always sort after known timestamps instead of moving
  // between the top and bottom when the direction changes.
  if (leftTime === undefined && rightTime !== undefined) return 1;
  if (leftTime !== undefined && rightTime === undefined) return -1;
  if (
    leftTime !== undefined &&
    rightTime !== undefined &&
    leftTime !== rightTime
  ) {
    return direction === "newest"
      ? rightTime - leftTime
      : leftTime - rightTime;
  }
  return compareIdentity(left, right);
}

export function mediaAssetMatchesQuery(
  asset: MediaAssetSummary,
  query: string,
): boolean {
  const tokens = normalizedSearchText(query).trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return true;

  // Search only metadata already presented on the card. Internal IDs, project
  // membership, checksums, and storage details are deliberately excluded.
  const searchableText = normalizedSearchText(
    [
      asset.name,
      asset.kind,
      asset.mediaType,
      mediaSourceLabel(asset.source),
    ].join("\n"),
  );
  return tokens.every((token) => searchableText.includes(token));
}

export function selectMediaAssets(
  assets: readonly MediaAssetSummary[],
  selection: MediaLibrarySelection,
): MediaAssetSummary[] {
  const selected = assets.filter(
    (asset) =>
      (selection.filter === "all" || asset.kind === selection.filter) &&
      mediaAssetMatchesQuery(asset, selection.query),
  );

  return selected.sort((left, right) => {
    if (selection.sort === "newest" || selection.sort === "oldest") {
      return compareCreatedAt(left, right, selection.sort);
    }
    if (selection.sort === "name") return compareIdentity(left, right);
    if (left.sizeBytes !== right.sizeBytes) {
      return right.sizeBytes - left.sizeBytes;
    }
    return compareIdentity(left, right);
  });
}

export function countMediaSearchMatches(
  assets: readonly MediaAssetSummary[],
  query: string,
): Record<MediaLibraryFilter, number> {
  const matches = assets.filter((asset) => mediaAssetMatchesQuery(asset, query));
  return {
    all: matches.length,
    image: matches.filter((asset) => asset.kind === "image").length,
    video: matches.filter((asset) => asset.kind === "video").length,
    audio: matches.filter((asset) => asset.kind === "audio").length,
  };
}
