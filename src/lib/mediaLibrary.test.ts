import { describe, expect, it } from "vitest";
import type { MediaAssetSummary } from "./projectMasterApi";
import {
  countMediaSearchMatches,
  mediaSourceLabel,
  mediaAssetMatchesQuery,
  selectMediaAssets,
  type MediaLibrarySelection,
} from "./mediaLibrary";

function asset(
  overrides: Partial<MediaAssetSummary> & Pick<MediaAssetSummary, "id" | "name">,
): MediaAssetSummary {
  return {
    projectIds: ["project-one"],
    kind: "image",
    source: "local_import",
    mediaType: "image/png",
    sha256: `secret-${overrides.id}`,
    sizeBytes: 100,
    createdAt: "2026-07-20T12:00:00Z",
    ...overrides,
  };
}

const DEFAULT_SELECTION: MediaLibrarySelection = {
  filter: "all",
  query: "",
  sort: "newest",
};

describe("media library selection", () => {
  it("searches visible metadata using case-insensitive, unordered terms", () => {
    const item = asset({
      id: "asset-one",
      name: "Launch Teaser.MP4",
      kind: "video",
      source: "comfyui",
      mediaType: "video/mp4",
    });

    expect(mediaAssetMatchesQuery(item, "VIDEO launch")).toBe(true);
    expect(mediaAssetMatchesQuery(item, "generation mp4")).toBe(true);
    expect(mediaAssetMatchesQuery(item, "podcast")).toBe(false);
  });

  it("uses the same readable source labels for cards and search", () => {
    expect(mediaSourceLabel("upload")).toBe("Local import");
    expect(mediaSourceLabel("trim")).toBe("Video trim");
    expect(mediaSourceLabel("comfyui")).toBe("ComfyUI generation");
    expect(
      mediaAssetMatchesQuery(
        asset({ id: "uploaded", name: "Still", source: "upload" }),
        "local import",
      ),
    ).toBe(true);
  });

  it("does not search private IDs, project membership, or checksums", () => {
    const item = asset({
      id: "internal-asset-id",
      name: "Public title",
      projectIds: ["private-project-id"],
      sha256: "private-checksum",
    });

    expect(mediaAssetMatchesQuery(item, "internal-asset-id")).toBe(false);
    expect(mediaAssetMatchesQuery(item, "private-project-id")).toBe(false);
    expect(mediaAssetMatchesQuery(item, "private-checksum")).toBe(false);
  });

  it("combines the type filter and search without mutating source order", () => {
    const assets = [
      asset({ id: "image", name: "Launch still" }),
      asset({
        id: "video",
        name: "Launch teaser",
        kind: "video",
        mediaType: "video/mp4",
      }),
      asset({
        id: "audio",
        name: "Theme",
        kind: "audio",
        mediaType: "audio/wav",
      }),
    ];

    expect(
      selectMediaAssets(assets, {
        filter: "video",
        query: "launch",
        sort: "name",
      }).map((item) => item.id),
    ).toEqual(["video"]);
    expect(assets.map((item) => item.id)).toEqual(["image", "video", "audio"]);
    expect(countMediaSearchMatches(assets, "launch")).toEqual({
      all: 2,
      image: 1,
      video: 1,
      audio: 0,
    });
  });

  it("sorts newest and oldest deterministically with unknown dates last", () => {
    const assets = [
      asset({ id: "middle", name: "Middle", createdAt: "2026-07-20T12:00:00Z" }),
      asset({ id: "new", name: "New", createdAt: "2026-07-21T12:00:00Z" }),
      asset({ id: "old", name: "Old", createdAt: "2026-07-19T12:00:00Z" }),
      asset({ id: "unknown", name: "Unknown", createdAt: "not-a-date" }),
    ];

    expect(
      selectMediaAssets(assets, DEFAULT_SELECTION).map((item) => item.id),
    ).toEqual(["new", "middle", "old", "unknown"]);
    expect(
      selectMediaAssets(assets, {
        ...DEFAULT_SELECTION,
        sort: "oldest",
      }).map((item) => item.id),
    ).toEqual(["old", "middle", "new", "unknown"]);
  });

  it("sorts names naturally and sizes largest-first with stable tie breakers", () => {
    const assets = [
      asset({ id: "clip-ten", name: "Clip 10", sizeBytes: 500 }),
      asset({ id: "clip-two-b", name: "clip 2", sizeBytes: 500 }),
      asset({ id: "clip-two-a", name: "Clip 2", sizeBytes: 900 }),
    ];

    expect(
      selectMediaAssets(assets, {
        ...DEFAULT_SELECTION,
        sort: "name",
      }).map((item) => item.id),
    ).toEqual(["clip-two-a", "clip-two-b", "clip-ten"]);
    expect(
      selectMediaAssets(assets, {
        ...DEFAULT_SELECTION,
        sort: "size",
      }).map((item) => item.id),
    ).toEqual(["clip-two-a", "clip-two-b", "clip-ten"]);
  });
});
