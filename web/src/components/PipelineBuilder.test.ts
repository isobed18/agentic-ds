import { describe, expect, it } from "vitest";
import type { PipelineBlueprint, PipelineComponent } from "../lib/api";
import { displayEdges, WORKFLOW_INTERACTION } from "./PipelineBuilder";

const text = (value: string) => ({ en: value, tr: value });

function component(id: string, branchId: string | null = null): PipelineComponent {
  return {
    id,
    kind: "report",
    title: text(id),
    description: text(id),
    inputs: [{ id: "in", label: text("in"), data_type: "table_asset", required: true, multiple: false }],
    outputs: [{ id: "out", label: text("out"), data_type: "table_asset", required: true, multiple: false }],
    settings: {},
    control: { execution: "auto", gate_handler: "planner", max_retries: null },
    enabled: true,
    optional: false,
    evidence_layer: "agent_proposal",
    configured_by: "system",
    branch_id: branchId,
    group_id: null,
  };
}

const blueprint: PipelineBlueprint = {
  version: "1",
  name: text("branch test"),
  components: [component("source"), component("branch-a", "candidate-1"), component("branch-b", "candidate-1"), component("report")],
  connections: [
    { id: "e1", source_component: "source", source_port: "out", target_component: "branch-a", target_port: "in" },
    { id: "e2", source_component: "branch-a", source_port: "out", target_component: "branch-b", target_port: "in" },
    { id: "e3", source_component: "branch-b", source_port: "out", target_component: "report", target_port: "in" },
  ],
};

describe("workflow canvas presentation", () => {
  it("collapses internal branch edges without changing the semantic blueprint", () => {
    const before = structuredClone(blueprint);
    expect(displayEdges(blueprint, new Set(["candidate-1"]))).toMatchObject([
      { source: "source", target: "branch:candidate-1" },
      { source: "branch:candidate-1", target: "report" },
    ]);
    expect(blueprint).toEqual(before);
  });

  it("uses scroll for panning and reserves wheel zoom for an explicit modifier or pinch", () => {
    expect(WORKFLOW_INTERACTION).toMatchObject({
      panOnScroll: true,
      zoomOnScroll: false,
      zoomOnPinch: true,
      minZoom: 0.25,
      maxZoom: 1.8,
    });
  });
});
