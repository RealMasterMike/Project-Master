export interface WebToolStatusSnapshot {
  fetchEnabled: boolean;
  searchEnabled: boolean;
}

export interface WebToolStatusPresentation {
  headline: "CHECKING" | "UNAVAILABLE" | "READY" | "PARTIAL";
  pageReading: string;
  webSearch: string;
  searchFiltering: string;
}

export function presentWebToolStatus(
  snapshot: WebToolStatusSnapshot | null,
  loading: boolean,
  error: string | null,
): WebToolStatusPresentation {
  if (loading) {
    return {
      headline: "CHECKING",
      pageReading: "Checking…",
      webSearch: "Checking…",
      searchFiltering: "Checking…",
    };
  }
  if (error || !snapshot) {
    return {
      headline: "UNAVAILABLE",
      pageReading: "Unknown",
      webSearch: "Unknown",
      searchFiltering: "Unknown",
    };
  }
  return {
    headline: snapshot.searchEnabled ? "READY" : "PARTIAL",
    pageReading: snapshot.fetchEnabled ? "Available" : "Unavailable",
    webSearch: snapshot.searchEnabled
      ? "SearXNG configured"
      : "Needs a SearXNG endpoint",
    searchFiltering: snapshot.searchEnabled
      ? "SafeSearch disabled by policy"
      : "SafeSearch disabled when search is enabled",
  };
}
