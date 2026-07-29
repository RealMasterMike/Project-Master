import { describe, expect, it } from "vitest";
import { withCurrentToolAuthorization } from "./chatAuthorization";

describe("chat retry authorization", () => {
  it("uses current tool gates instead of stale consent from the failed request", () => {
    const failedRequest = {
      message: "change the project",
      allowMutations: true,
      allowWebSearch: true,
      imageAssetIds: ["media-asset-0123456789abcdef0123456789abcdef"],
    };

    expect(
      withCurrentToolAuthorization(failedRequest, {
        allowMutations: false,
        allowWebSearch: false,
      }),
    ).toEqual({
      message: "change the project",
      allowMutations: false,
      allowWebSearch: false,
      imageAssetIds: ["media-asset-0123456789abcdef0123456789abcdef"],
    });
    expect(failedRequest.allowMutations).toBe(true);
    expect(failedRequest.allowWebSearch).toBe(true);
  });
});
