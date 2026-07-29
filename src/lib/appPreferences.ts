export const APP_PREFERENCES_SCHEMA_VERSION = 1 as const;
export const APP_PREFERENCES_STORAGE_KEY = "project-master.preferences.v1";

export type InterfaceDensity = "comfortable" | "compact";
export type InterfaceTextScale = "small" | "medium" | "large";
export type MotionPreference = "system" | "reduced";
export type CreatorGenerationDefault = "image" | "video";

export interface AppPreferences {
  schemaVersion: typeof APP_PREFERENCES_SCHEMA_VERSION;
  interfaceDensity: InterfaceDensity;
  textScale: InterfaceTextScale;
  motion: MotionPreference;
  autoLoadMediaPreviews: boolean;
  creatorGenerationDefault: CreatorGenerationDefault;
  preferredVisionModel: string;
}

export interface PreferencesStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const DEFAULT_APP_PREFERENCES: AppPreferences = {
  schemaVersion: APP_PREFERENCES_SCHEMA_VERSION,
  interfaceDensity: "comfortable",
  textScale: "medium",
  motion: "system",
  autoLoadMediaPreviews: true,
  creatorGenerationDefault: "video",
  preferredVisionModel: "",
};

function browserStorage(): PreferencesStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T,
): T {
  return typeof value === "string" && allowed.includes(value as T)
    ? (value as T)
    : fallback;
}

function modelTag(value: unknown): string {
  if (typeof value !== "string") return "";
  const normalized = value.trim();
  return normalized.length <= 500 ? normalized : "";
}

export function parseAppPreferences(value: unknown): AppPreferences {
  if (
    !isRecord(value) ||
    value.schemaVersion !== APP_PREFERENCES_SCHEMA_VERSION
  ) {
    return { ...DEFAULT_APP_PREFERENCES };
  }

  return {
    schemaVersion: APP_PREFERENCES_SCHEMA_VERSION,
    interfaceDensity: oneOf(
      value.interfaceDensity,
      ["comfortable", "compact"],
      DEFAULT_APP_PREFERENCES.interfaceDensity,
    ),
    textScale: oneOf(
      value.textScale,
      ["small", "medium", "large"],
      DEFAULT_APP_PREFERENCES.textScale,
    ),
    motion: oneOf(
      value.motion,
      ["system", "reduced"],
      DEFAULT_APP_PREFERENCES.motion,
    ),
    autoLoadMediaPreviews:
      typeof value.autoLoadMediaPreviews === "boolean"
        ? value.autoLoadMediaPreviews
        : DEFAULT_APP_PREFERENCES.autoLoadMediaPreviews,
    creatorGenerationDefault: oneOf(
      value.creatorGenerationDefault,
      ["image", "video"],
      DEFAULT_APP_PREFERENCES.creatorGenerationDefault,
    ),
    preferredVisionModel: modelTag(value.preferredVisionModel),
  };
}

export function loadAppPreferences(
  storage: PreferencesStorage | null = browserStorage(),
): AppPreferences {
  if (!storage) return { ...DEFAULT_APP_PREFERENCES };
  try {
    const stored = storage.getItem(APP_PREFERENCES_STORAGE_KEY);
    return stored
      ? parseAppPreferences(JSON.parse(stored) as unknown)
      : { ...DEFAULT_APP_PREFERENCES };
  } catch {
    return { ...DEFAULT_APP_PREFERENCES };
  }
}

export function persistAppPreferences(
  preferences: AppPreferences,
  storage: PreferencesStorage | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(
      APP_PREFERENCES_STORAGE_KEY,
      JSON.stringify(parseAppPreferences(preferences)),
    );
  } catch {
    // Settings remain usable when browser storage is unavailable or full.
  }
}

let currentPreferences: AppPreferences | undefined;
const preferenceListeners = new Set<() => void>();

export function getAppPreferencesSnapshot(): AppPreferences {
  currentPreferences ??= loadAppPreferences();
  return currentPreferences;
}

export function subscribeToAppPreferences(listener: () => void): () => void {
  preferenceListeners.add(listener);
  return () => preferenceListeners.delete(listener);
}

function publishPreferences(preferences: AppPreferences): AppPreferences {
  currentPreferences = parseAppPreferences(preferences);
  persistAppPreferences(currentPreferences);
  preferenceListeners.forEach((listener) => listener());
  return currentPreferences;
}

export function updateAppPreferences(
  update: Partial<Omit<AppPreferences, "schemaVersion">>,
): AppPreferences {
  return publishPreferences({
    ...getAppPreferencesSnapshot(),
    ...update,
    schemaVersion: APP_PREFERENCES_SCHEMA_VERSION,
  });
}

export function resetAppPreferences(): AppPreferences {
  return publishPreferences({ ...DEFAULT_APP_PREFERENCES });
}
