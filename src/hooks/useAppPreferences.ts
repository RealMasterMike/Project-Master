import { useSyncExternalStore } from "react";

import {
  getAppPreferencesSnapshot,
  subscribeToAppPreferences,
} from "../lib/appPreferences";

export function useAppPreferences() {
  return useSyncExternalStore(
    subscribeToAppPreferences,
    getAppPreferencesSnapshot,
    getAppPreferencesSnapshot,
  );
}

