import { describe, expect, it } from "vitest";

import {
  APP_PREFERENCES_SCHEMA_VERSION,
  APP_PREFERENCES_STORAGE_KEY,
  DEFAULT_APP_PREFERENCES,
  loadAppPreferences,
  parseAppPreferences,
  persistAppPreferences,
  type PreferencesStorage,
} from "./appPreferences";

function memoryStorage(initial?: string): PreferencesStorage & {
  value: string | null;
} {
  return {
    value: initial ?? null,
    getItem(key) {
      return key === APP_PREFERENCES_STORAGE_KEY ? this.value : null;
    },
    setItem(key, value) {
      if (key === APP_PREFERENCES_STORAGE_KEY) this.value = value;
    },
  };
}

describe("application preferences", () => {
  it("uses safe defaults when storage is empty or malformed", () => {
    expect(loadAppPreferences(memoryStorage())).toEqual(
      DEFAULT_APP_PREFERENCES,
    );
    expect(loadAppPreferences(memoryStorage("{bad json"))).toEqual(
      DEFAULT_APP_PREFERENCES,
    );
    expect(parseAppPreferences({ schemaVersion: 99 })).toEqual(
      DEFAULT_APP_PREFERENCES,
    );
  });

  it("keeps valid values and repairs unsupported fields independently", () => {
    expect(
      parseAppPreferences({
        schemaVersion: APP_PREFERENCES_SCHEMA_VERSION,
        interfaceDensity: "compact",
        textScale: "enormous",
        motion: "reduced",
        autoLoadMediaPreviews: false,
        creatorGenerationDefault: "image",
        preferredVisionModel: "  local-vision:latest  ",
        imageAssetIds: ["must-not-persist"],
      }),
    ).toEqual({
      ...DEFAULT_APP_PREFERENCES,
      interfaceDensity: "compact",
      motion: "reduced",
      autoLoadMediaPreviews: false,
      creatorGenerationDefault: "image",
      preferredVisionModel: "local-vision:latest",
    });
  });

  it("persists only the validated preference document", () => {
    const storage = memoryStorage();
    persistAppPreferences(
      {
        ...DEFAULT_APP_PREFERENCES,
        interfaceDensity: "compact",
        textScale: "large",
      },
      storage,
    );

    expect(loadAppPreferences(storage)).toEqual({
      ...DEFAULT_APP_PREFERENCES,
      interfaceDensity: "compact",
      textScale: "large",
    });
  });
});
