import { describe, expect, it } from "vitest";

import SOURCE from "./GuidedPipeline.tsx?raw";

describe("planner access in the guided pipeline (#97)", () => {
  it("mounts the planner chat, not just a read-only rationale block", () => {
    // The "Chat with Planner" entry point lived only in UnderstandingAndProposal
    // and was unmounted once a plan was accepted -- so once the pipeline was
    // running, and at the exact moment a gate escalated for human input, there
    // was no way to consult the planner. The running view now imports and
    // renders the same PlannerPanel the staging view uses.
    expect(SOURCE).toContain('import { PlannerPanel } from "./PlannerPanel"');
    expect(SOURCE).toContain("<PlannerPanel");
    expect(SOURCE).toContain("runId={runId}");
  });

  it("exposes a toggle in the run toolbar that opens the panel", () => {
    // Reachable from the fixed run-control toolbar, gated on its own open state
    // so it does not permanently occupy the canvas.
    expect(SOURCE).toContain('onClick={() => setPlannerOpen(true)}');
    expect(SOURCE).toContain('t("Chat with Planner")');
    expect(SOURCE).toContain("plannerOpen &&");
  });
});

describe("guided pipeline node descriptions (#168)", () => {
  it("uses one explanatory sentence per group instead of joining bare stage names", () => {
    // Joining `node.label` values produced fragments such as "integration" and
    // "problem discovery" that repeated the title without explaining the work.
    const groupsSource = SOURCE.slice(
      SOURCE.indexOf("export const GROUPS"),
      SOURCE.indexOf("function local"),
    );
    const descriptions = [...groupsSource.matchAll(/description: "([^"]+)"/g)].map(
      (match) => match[1],
    );

    expect(descriptions).toHaveLength(6);
    expect(descriptions.every((description) => description.endsWith("."))).toBe(true);
    expect(SOURCE).toContain("subtitle={t(group.description)}");
    expect(SOURCE).not.toContain("group.nodes.map((node) => t(node.label");
  });
});
