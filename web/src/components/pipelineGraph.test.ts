import { describe, expect, it } from "vitest";
import type { PipelineBlueprint, PipelineComponent } from "../lib/api";
import { pipelineConnectionFromHandles, pipelinePortsMatch } from "./pipelineGraph";

const text = (value: string) => ({ en: value, tr: value });
const component = (id: string, kind: PipelineComponent["kind"]): PipelineComponent => ({
  id,
  kind,
  title: text(id),
  description: text(id),
  inputs: kind === "intake" ? [{ id: "files", label: text("files"), data_type: "structured_files", required: true, multiple: false }] : [],
  outputs: kind === "data_source" ? [
    { id: "tables", label: text("tables"), data_type: "structured_files", required: true, multiple: false },
    { id: "docs", label: text("docs"), data_type: "documents", required: true, multiple: false },
  ] : [],
  settings: {},
  control: { execution: "auto", gate_handler: "planner", max_retries: null },
  enabled: true,
  optional: false,
  evidence_layer: "measured",
  configured_by: "system",
});
const blueprint: PipelineBlueprint = {
  version: "1",
  name: text("test"),
  components: [component("source", "data_source"), component("intake", "intake")],
  connections: [],
};

describe("pipeline graph adapter", () => {
  it("only connects equal output and input data types", () => {
    expect(pipelinePortsMatch(blueprint, "source", "out:tables", "intake", "in:files")).toBe(true);
    expect(pipelinePortsMatch(blueprint, "source", "out:docs", "intake", "in:files")).toBe(false);
  });

  it("preserves exact component and port identities when persisted", () => {
    expect(pipelineConnectionFromHandles({
      id: "human:1",
      source: "source",
      sourceHandle: "out:tables",
      target: "intake",
      targetHandle: "in:files",
    })).toEqual({
      id: "human:1",
      source_component: "source",
      source_port: "tables",
      target_component: "intake",
      target_port: "files",
    });
  });
});
