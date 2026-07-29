import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  formatProjectMasterError,
  getMediaAssetContent,
  getMediaHealth,
  importProjectMediaAsset,
  isAbortError,
  listProjectMediaAssets,
  type MasterProject,
  type MediaAssetSummary,
  type MediaHealth,
} from "../../lib/projectMasterApi";
import {
  countMediaSearchMatches,
  mediaSourceLabel,
  selectMediaAssets,
  type MediaLibraryFilter,
  type MediaLibrarySort,
} from "../../lib/mediaLibrary";
import { useAppPreferences } from "../../hooks/useAppPreferences";
import { shouldAutoLoadPreview } from "./mediaPreview";
import { useViewportMediaPreview } from "./useViewportMediaPreview";

const MEDIA_FILTERS: Array<{ id: MediaLibraryFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "image", label: "Images" },
  { id: "video", label: "Video" },
  { id: "audio", label: "Audio" },
];

const RECOGNIZED_MEDIA_EXTENSION =
  /\.(?:aac|avif|bmp|flac|gif|jpe?g|m4a|mka|mkv|mov|mp3|mp4|mpeg|mpg|oga|ogg|png|tiff?|wav|weba|webm|webp)$/i;

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

function formatDuration(value?: number): string | undefined {
  if (value === undefined) return undefined;
  const minutes = Math.floor(value / 60);
  const seconds = value - minutes * 60;
  return minutes
    ? `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`
    : `${seconds.toFixed(1)}s`;
}

function safeDownloadName(name: string, assetId: string): string {
  const parts = name.split(/[/\\]/).filter(Boolean);
  const basename = parts[parts.length - 1] ?? "";
  return (
    basename.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 180) ||
    `${assetId}.media`
  );
}

function MediaAssetCard({
  asset,
  sourceAssetName,
}: {
  asset: MediaAssetSummary;
  sourceAssetName?: string;
}) {
  const preferences = useAppPreferences();
  const {
    cardRef,
    url,
    loading,
    error,
    load,
    release,
    reportDecodeError,
  } = useViewportMediaPreview({
    autoLoad: shouldAutoLoadPreview(
      asset.kind,
      true,
      preferences.autoLoadMediaPreviews,
    ),
    expectedSize: asset.sizeBytes,
    loadBlob: (signal) => getMediaAssetContent(asset.id, signal),
    sizeMismatchMessage:
      "Downloaded media size did not match its verified manifest.",
  });

  const duration = formatDuration(asset.durationSeconds);
  const dimensions =
    asset.width !== undefined && asset.height !== undefined
      ? `${asset.width} × ${asset.height}`
      : undefined;
  const created = new Date(asset.createdAt);

  return (
    <article className="media-library__card" ref={cardRef}>
      <header>
        <div>
          <span className={`media-library__kind is-${asset.kind}`}>
            {asset.kind}
          </span>
          <h3 title={asset.name}>{asset.name}</h3>
        </div>
        <strong>VERIFIED</strong>
      </header>

      {loading ? (
        <div
          className={`creator-media-preview-state is-${asset.kind}`}
          role="status"
        >
          Loading verified {asset.kind} preview…
        </div>
      ) : null}
      {url && asset.kind === "image" ? (
        <img
          className="media-library__preview"
          src={url}
          alt={asset.name}
          loading="lazy"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this image preview.",
            )
          }
        />
      ) : null}
      {url && asset.kind === "video" ? (
        <video
          className="media-library__preview"
          src={url}
          controls
          preload="metadata"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this video preview.",
            )
          }
        />
      ) : null}
      {url && asset.kind === "audio" ? (
        <audio
          className="media-library__preview media-library__preview--audio"
          src={url}
          controls
          preload="metadata"
          onError={() =>
            reportDecodeError(
              "The browser could not decode this audio preview.",
            )
          }
        />
      ) : null}

      <dl>
        <div>
          <dt>Format</dt>
          <dd>{asset.mediaType}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{formatBytes(asset.sizeBytes)}</dd>
        </div>
        {duration ? (
          <div>
            <dt>Duration</dt>
            <dd>{duration}</dd>
          </div>
        ) : null}
        {dimensions ? (
          <div>
            <dt>Frame</dt>
            <dd>{dimensions}</dd>
          </div>
        ) : null}
        <div>
          <dt>Source</dt>
          <dd>{mediaSourceLabel(asset.source)}</dd>
        </div>
        {asset.derivation ? (
          <>
            <div>
              <dt>Derived from</dt>
              <dd title={sourceAssetName ?? asset.derivation.sourceAssetId}>
                {sourceAssetName ??
                  `${asset.derivation.sourceAssetId.slice(0, 12)}…`}
              </dd>
            </div>
            <div>
              <dt>Trim</dt>
              <dd>
                {formatDuration(asset.derivation.startSeconds)} →{" "}
                {formatDuration(asset.derivation.endSeconds)}
              </dd>
            </div>
            <div>
              <dt>Recipe</dt>
              <dd>H.264/AAC MP4</dd>
            </div>
          </>
        ) : null}
        <div>
          <dt>Added</dt>
          <dd>
            {Number.isNaN(created.valueOf())
              ? asset.createdAt
              : created.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>SHA-256</dt>
          <dd title={asset.sha256}>
            <code>{asset.sha256}</code>
          </dd>
        </div>
      </dl>

      <div className="media-library__card-actions">
        {!url ? (
          <button
            className="button button--secondary"
            type="button"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? "Loading…" : error ? "Retry preview" : "Load preview"}
          </button>
        ) : (
          <>
            <a
              className="button button--primary"
              href={url}
              download={safeDownloadName(asset.name, asset.id)}
            >
              Download
            </a>
            <button
              className="button button--secondary"
              type="button"
              onClick={release}
            >
              Release preview
            </button>
          </>
        )}
      </div>
      {error ? (
        <small className="media-library__card-error" role="alert">
          {error}
        </small>
      ) : null}
    </article>
  );
}

export function MediaLibrary({ project }: { project?: MasterProject }) {
  const [health, setHealth] = useState<MediaHealth | null>(null);
  const [assets, setAssets] = useState<MediaAssetSummary[]>([]);
  const [filter, setFilter] = useState<MediaLibraryFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sort, setSort] = useState<MediaLibrarySort>("newest");
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const importControllerRef = useRef<AbortController | null>(null);
  const refreshButtonRef = useRef<HTMLButtonElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const projectId = project?.id;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!projectId) {
        setHealth(null);
        setAssets([]);
        setError("");
        setLoading(false);
        return;
      }
      setLoading(true);
      setError("");
      try {
        const [nextHealth, nextAssets] = await Promise.all([
          getMediaHealth(signal),
          listProjectMediaAssets(projectId, signal),
        ]);
        if (signal?.aborted) return;
        setHealth(nextHealth);
        setAssets(nextAssets);
      } catch (caught) {
        if (signal?.aborted) return;
        setError(formatProjectMasterError(caught));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [projectId],
  );

  useEffect(() => {
    const controller = new AbortController();
    importControllerRef.current?.abort();
    importControllerRef.current = null;
    setImporting(false);
    setHealth(null);
    setAssets([]);
    setFilter("all");
    setSearchQuery("");
    setSort("newest");
    void load(controller.signal);
    return () => {
      controller.abort();
      importControllerRef.current?.abort();
    };
  }, [load]);

  async function importFile(file: File) {
    if (!projectId) return;
    const hasSupportedType = /^(image|video|audio)\//.test(file.type);
    const hasSupportedExtension = RECOGNIZED_MEDIA_EXTENSION.test(file.name);
    if (
      (file.type && !hasSupportedType) ||
      (!file.type && !hasSupportedExtension)
    ) {
      setError("Choose a local image, video, or audio file.");
      return;
    }
    if (health?.maxUploadBytes && file.size > health.maxUploadBytes) {
      setError(
        `${file.name} is larger than the ${formatBytes(health.maxUploadBytes)} import limit.`,
      );
      return;
    }
    importControllerRef.current?.abort();
    const controller = new AbortController();
    importControllerRef.current = controller;
    setImporting(true);
    setError("");
    try {
      const imported = await importProjectMediaAsset(
        projectId,
        file,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setAssets((current) => [
        imported,
        ...current.filter((asset) => asset.id !== imported.id),
      ]);
    } catch (caught) {
      if (!controller.signal.aborted && !isAbortError(caught)) {
        setError(formatProjectMasterError(caught));
      }
    } finally {
      if (importControllerRef.current === controller) {
        importControllerRef.current = null;
        if (!controller.signal.aborted) setImporting(false);
      }
    }
  }

  if (!project) {
    return (
      <section className="media-library media-library--empty">
        <span className="media-library__eyebrow">PROJECT MEDIA</span>
        <h2>Choose a Creator project</h2>
        <p>
          Media is stored and verified inside a project. Select or create a
          Creator project before importing files.
        </p>
      </section>
    );
  }

  const visibleAssets = selectMediaAssets(assets, {
    filter,
    query: searchQuery,
    sort,
  });
  const assetNamesById = new Map(
    assets.map((asset) => [asset.id, asset.name] as const),
  );
  const filterCounts = countMediaSearchMatches(assets, searchQuery);
  const hasActiveBrowseFilter = filter !== "all" || Boolean(searchQuery.trim());
  const acceptedTypes =
    health?.supportedMediaTypes.join(",") || "image/*,video/*,audio/*";

  return (
    <section
      className="media-library"
      aria-labelledby="creator-media-library-title"
      aria-busy={loading || importing}
    >
      <header className="media-library__header">
        <div>
          <span className="media-library__eyebrow">PROJECT MEDIA</span>
          <h2 id="creator-media-library-title">Media library</h2>
          <p>
            Images, video, and audio for <strong>{project.name}</strong>.
            Imports stay local and retain checksum metadata.
          </p>
        </div>
        <div className="media-library__actions">
          <label
            className={`button button--primary media-library__import ${
              health?.available === false ? "is-disabled" : ""
            }`}
          >
            {importing ? "Importing…" : "Import local media"}
            <input
              type="file"
              accept={acceptedTypes}
              disabled={importing || loading || health?.available === false}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file) void importFile(file);
              }}
            />
          </label>
          <button
            ref={refreshButtonRef}
            className="button button--secondary"
            type="button"
            disabled={loading || importing}
            onClick={() => void load()}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      <div className="media-library__status">
        <span className={health?.available ? "is-ready" : undefined}>
          {loading && health === null
            ? "CHECKING LOCAL STORE"
            : health?.available
              ? "LOCAL STORE READY"
              : "MEDIA STORE UNAVAILABLE"}
        </span>
        <span>
          {assets.length} project asset{assets.length === 1 ? "" : "s"} ·{" "}
          {formatBytes(assets.reduce((total, asset) => total + asset.sizeBytes, 0))}
        </span>
      </div>

      <div className="media-library__browse-controls">
        <label className="media-library__search" htmlFor="creator-media-search">
          <span>Search project media</span>
          <input
            ref={searchInputRef}
            id="creator-media-search"
            type="search"
            value={searchQuery}
            autoComplete="off"
            placeholder="Name, format, source, or type"
            aria-describedby="creator-media-result-count"
            onChange={(event) => setSearchQuery(event.currentTarget.value)}
          />
        </label>
        <label className="media-library__sort" htmlFor="creator-media-sort">
          <span>Sort media</span>
          <select
            id="creator-media-sort"
            value={sort}
            onChange={(event) =>
              setSort(event.currentTarget.value as MediaLibrarySort)
            }
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="name">Name A–Z</option>
            <option value="size">Size, largest first</option>
          </select>
        </label>
      </div>

      <div
        className="media-library__filters"
        role="group"
        aria-label="Filter project media"
      >
        {MEDIA_FILTERS.map((item) => {
          const count = filterCounts[item.id];
          return (
            <button
              className={filter === item.id ? "is-active" : undefined}
              type="button"
              key={item.id}
              aria-pressed={filter === item.id}
              aria-label={`${item.label}, ${count} ${
                searchQuery.trim() ? "search " : ""
              }match${count === 1 ? "" : "es"}`}
              onClick={() => setFilter(item.id)}
            >
              {item.label} <span>{count}</span>
            </button>
          );
        })}
      </div>

      <p
        className="media-library__result-count"
        id="creator-media-result-count"
        role="status"
        aria-live="polite"
      >
        {loading && health === null ? (
          "Loading project media results."
        ) : (
          <>
            Showing {visibleAssets.length} of {assets.length} project asset
            {assets.length === 1 ? "" : "s"}
            {searchQuery.trim() ? " matching this search" : ""}
            {filter !== "all" ? ` · ${filter} only` : ""}.
          </>
        )}
      </p>

      {error ? (
        <div className="media-library__alert" role="alert">
          <strong>Media action needs attention</strong>
          <span>{error}</span>
          <button
            type="button"
            onClick={() => {
              void load().finally(() =>
                window.requestAnimationFrame(() =>
                  refreshButtonRef.current?.focus(),
                ),
              );
            }}
          >
            Retry
          </button>
        </div>
      ) : null}

      {loading && !assets.length ? (
        <div className="media-library__loading" role="status">
          Loading verified project media…
        </div>
      ) : visibleAssets.length ? (
        <div className="media-library__grid">
          {visibleAssets.map((asset) => (
            <MediaAssetCard
              asset={asset}
              key={asset.id}
              sourceAssetName={
                asset.derivation
                  ? assetNamesById.get(asset.derivation.sourceAssetId)
                  : undefined
              }
            />
          ))}
        </div>
      ) : (
        <div className="media-library__empty">
          <strong>
            {assets.length > 0 && hasActiveBrowseFilter
              ? searchQuery.trim()
                ? `No ${filter === "all" ? "media" : filter} matches this search`
                : `No ${filter} assets in this project`
              : "No project media yet"}
          </strong>
          <p>
            {assets.length > 0 && hasActiveBrowseFilter
              ? searchQuery.trim()
                ? "Try a different name or format, clear the search, or choose another media type."
                : "Choose another filter to see the rest of the library."
              : "Import a local image, video, or audio file to begin."}
          </p>
          {searchQuery.trim() ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => {
                setSearchQuery("");
                window.requestAnimationFrame(() =>
                  searchInputRef.current?.focus(),
                );
              }}
            >
              Clear search
            </button>
          ) : null}
        </div>
      )}
    </section>
  );
}
