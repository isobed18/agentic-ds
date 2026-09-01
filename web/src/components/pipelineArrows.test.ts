import { describe, expect, it } from "vitest";

import type { WorkflowNode } from "../lib/api";
import { activeArrows } from "./pipelineArrows";

type Status = WorkflowNode["status"];

/** What the old inter-group rule computed, kept so the regression is explicit. */
function previousRule(statuses: Status[]): boolean[] {
  return [
    statuses[0] === "running",
    ...statuses.slice(0, -1).map(
      (status, index) =>
        status === "running" || status === "retry" || statuses[index + 1] === "running",
    ),
  ];
}

describe("which connector arrows pulse", () => {
  it("pulses exactly one arrow while a middle group runs", () => {
    // The bug: a running group lit the arrow into it AND the arrow out of it,
    // so the canvas animated two edges for one piece of work.
    const statuses: Status[] = ["succeeded", "running", "pending", "pending"];
    expect(activeArrows(statuses)).toEqual([false, true, false, false]);
    expect(previousRule(statuses).filter(Boolean)).toHaveLength(2);
  });

  it("pulses the edge out of the plan while the first group runs", () => {
    // The one arrow that was always right, and the rule the rest now follow.
    expect(activeArrows(["running", "pending", "pending"])).toEqual([true, false, false]);
  });

  it("pulses the incoming arrow of a retrying group too", () => {
    const statuses: Status[] = ["succeeded", "retry", "pending"];
    expect(activeArrows(statuses)).toEqual([false, true, false]);
    expect(previousRule(statuses).filter(Boolean)).toHaveLength(1);
  });

  it("never pulses more than one arrow, whatever the row looks like", () => {
    // A group's status is derived from its nodes, so two groups can briefly
    // report activity at once; the property that must hold is that an arrow
    // answers only for the group ahead of it.
    for (const statuses of [
      ["pending", "pending", "pending"],
      ["succeeded", "succeeded", "succeeded"],
      ["failed", "pending", "pending"],
      ["succeeded", "blocked", "pending"],
    ] as Status[][]) {
      expect(activeArrows(statuses).filter(Boolean)).toHaveLength(0);
    }
  });

  it("returns one flag per group, including the leading edge", () => {
    expect(activeArrows(["pending", "pending", "pending", "pending"])).toHaveLength(4);
    expect(activeArrows([])).toEqual([]);
  });
});
