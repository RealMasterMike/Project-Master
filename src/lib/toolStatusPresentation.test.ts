import { describe, expect, it } from "vitest";

import { presentWebToolStatus } from "./toolStatusPresentation";

describe("web tool status presentation", () => {
  it("does not expose stale values while loading or after an error", () => {
    const stale = { fetchEnabled: true, searchEnabled: true };

    expect(presentWebToolStatus(stale, true, null)).toEqual({
      headline: "CHECKING",
      pageReading: "Checking…",
      webSearch: "Checking…",
      searchFiltering: "Checking…",
    });
    expect(presentWebToolStatus(stale, false, "offline")).toEqual({
      headline: "UNAVAILABLE",
      pageReading: "Unknown",
      webSearch: "Unknown",
      searchFiltering: "Unknown",
    });
  });

  it("distinguishes complete and page-reading-only availability", () => {
    expect(
      presentWebToolStatus(
        { fetchEnabled: true, searchEnabled: true },
        false,
        null,
      ),
    ).toEqual({
      headline: "READY",
      pageReading: "Available",
      webSearch: "SearXNG configured",
      searchFiltering: "SafeSearch disabled by policy",
    });
    expect(
      presentWebToolStatus(
        { fetchEnabled: true, searchEnabled: false },
        false,
        null,
      ),
    ).toEqual({
      headline: "PARTIAL",
      pageReading: "Available",
      webSearch: "Needs a SearXNG endpoint",
      searchFiltering: "SafeSearch disabled when search is enabled",
    });
  });
});
