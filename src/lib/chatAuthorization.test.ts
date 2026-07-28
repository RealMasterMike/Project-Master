import { describe, expect, it } from "vitest";
import { withCurrentMutationAuthorization } from "./chatAuthorization";

describe("chat retry authorization", () => {
  it("uses the current mutation gate instead of stale consent from the failed request", () => {
    const failedRequest = {
      message: "change the project",
      allowMutations: true,
    };

    expect(withCurrentMutationAuthorization(failedRequest, false)).toEqual({
      message: "change the project",
      allowMutations: false,
    });
    expect(failedRequest.allowMutations).toBe(true);
  });
});
