import { describe, expect, it } from "vitest";
import { deriveMissionState } from "./mission";
import type { ProjectMasterRunActivity } from "./projectMasterApi";

function activity(
  kind: string,
  overrides: Partial<ProjectMasterRunActivity> = {},
): ProjectMasterRunActivity {
  return { kind, message: `${kind} message`, ...overrides };
}

describe("deriveMissionState", () => {
  it("stands by with no activities and no stream", () => {
    const state = deriveMissionState([], false);
    expect(state.phase).toBe("idle");
    expect(state.statusLine).toBe("Standing by");
    expect(state.progress).toBe(0);
  });

  it("assembles the council when streaming starts before events arrive", () => {
    const state = deriveMissionState([], true);
    expect(state.phase).toBe("council");
    expect(state.statusLine).toBe("Assembling council…");
  });

  it("tracks an active specialist by role", () => {
    const state = deriveMissionState(
      [
        activity("council_started"),
        activity("worker_started", { role: "builder", model: "dolphin-96k" }),
      ],
      true,
    );
    expect(state.phase).toBe("workers");
    expect(state.statusLine).toBe("builder working…");
    expect(state.progress).toBeGreaterThan(0.1);
    expect(state.progress).toBeLessThan(0.85);
  });

  it("counts completed and failed specialists as decisions", () => {
    const state = deriveMissionState(
      [
        activity("council_started"),
        activity("worker_started", { role: "builder" }),
        activity("worker_completed", { role: "builder" }),
        activity("worker_started", { role: "verifier" }),
        activity("worker_failed", { role: "verifier" }),
      ],
      true,
    );
    expect(state.workersCompleted).toBe(1);
    expect(state.workersFailed).toBe(1);
    expect(state.decisions.map((decision) => decision.label)).toEqual([
      "builder completed",
      "verifier failed",
    ]);
    expect(state.decisions[1].failed).toBe(true);
  });

  it("moves to synthesis and then delivery", () => {
    const base = [
      activity("council_started"),
      activity("worker_started", { role: "builder" }),
      activity("worker_completed", { role: "builder" }),
      activity("synthesis_started"),
    ];
    const synth = deriveMissionState(base, true);
    expect(synth.phase).toBe("synthesis");
    expect(synth.statusLine).toBe("Lead synthesizing…");
    expect(synth.progress).toBe(0.85);

    const delivered = deriveMissionState(
      [
        ...base,
        activity("synthesis_completed"),
        activity("council_completed"),
        activity("lead_started"),
        activity("delivery_completed"),
      ],
      false,
    );
    expect(delivered.phase).toBe("delivered");
    expect(delivered.statusLine).toBe("Delivered");
    expect(delivered.progress).toBe(1);
  });

  it("reports a cancelled run", () => {
    const state = deriveMissionState(
      [
        activity("council_started"),
        activity("worker_started", { role: "builder" }),
        activity("worker_cancelled", { role: "builder" }),
        activity("council_cancelled"),
      ],
      false,
    );
    expect(state.phase).toBe("cancelled");
    expect(state.statusLine).toBe("Cancelled");
    expect(state.decisions[state.decisions.length - 1]?.label).toBe(
      "run cancelled",
    );
  });

  it("collects tool events with success flags", () => {
    const state = deriveMissionState(
      [
        activity("tool_completed", {
          tool: "workspace_read",
          ok: true,
          outcome: "success",
        }),
        activity("tool_unavailable", {
          tool: "terminal",
          ok: false,
          outcome: "unavailable",
        }),
      ],
      true,
    );
    expect(state.toolEvents).toEqual([
      { tool: "workspace_read", outcome: "success" },
      { tool: "terminal", outcome: "unavailable" },
    ]);
  });

  it("does not call the mission delivered before the lead finishes", () => {
    const councilReady = deriveMissionState(
      [
        activity("council_started"),
        activity("synthesis_started"),
        activity("synthesis_completed"),
        activity("council_completed"),
        activity("lead_started"),
      ],
      true,
    );

    expect(councilReady.phase).toBe("lead");
    expect(councilReady.statusLine).toBe("Lead completing the response…");
  });

  it("never surfaces private reasoning fields, only bounded messages", () => {
    const state = deriveMissionState(
      [activity("worker_completed", { role: "critic", message: "Critic requested rewrite" })],
      false,
    );
    expect(state.decisions[0].detail).toBe("Critic requested rewrite");
  });
});
