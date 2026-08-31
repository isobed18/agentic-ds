import { describe, expect, it } from "vitest";

import SOURCE from "./GuidedPipeline.tsx?raw";
import BUILDER_SOURCE from "./PipelineBuilder.tsx?raw";
import PANEL_SOURCE from "./PlannerPanel.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";

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
    // #188: this test always called the control a toggle, but the handler it
    // pinned only ever opened the panel. Now it flips, so the assertion says so.
    expect(SOURCE).toContain('onClick={() => setPlannerOpen((open) => !open)}');
    expect(SOURCE).toContain('t("Chat with Planner")');
    expect(SOURCE).toContain("plannerOpen &&");
  });

  it("gives every ML planner mount the source and data-first prompts (#190)", () => {
    expect(SOURCE).toContain("sourceId={profile.source_id}");
    expect(SOURCE).toContain('t("What columns are in this data?")');
    expect(SOURCE).toContain('t("Rank the best target columns and ML problems.")');
    expect(SOURCE).not.toContain('t("What is this gate asking?")');
    expect(BUILDER_SOURCE).toContain("sourceId?: string | null");
    expect(BUILDER_SOURCE).toContain("<PlannerPanel runId={runId} sourceId={sourceId}");
    expect(PANEL_SOURCE).toContain("problem_recommendations");
    expect(PANEL_SOURCE).toContain('t("Ranked ML opportunities")');
  });
});

describe("the run control (#197)", () => {
  it("docks the control top-centre instead of floating at the bottom edge", () => {
    // Bottom-middle placement was easy to miss, so the whole workspace read as
    // stuck (#194). Top-centre is where the eye lands on the canvas.
    expect(SOURCE).toContain("fixed top-[70px] left-1/2");
    expect(SOURCE).not.toContain("fixed bottom-4 left-1/2");
  });

  it("carries state in one Run/Pause primary button, not a button that becomes loose text", () => {
    // Run and Continue while a run can start; Pause while it is live -- both are
    // the same primary control with a glyph, and the separate ghost pause button
    // is gone. No lowercase status label stands in where a button used to be.
    expect(SOURCE).toContain('<span aria-hidden="true">▶</span>');
    expect(SOURCE).toContain('<span aria-hidden="true">⏸</span>');
    expect(SOURCE).toContain('t(currentStage ? "Continue" : "Run")');
    expect(SOURCE).toContain("{active && <button");
    expect(SOURCE).not.toContain('t("Pause after current stage")');
  });

  it("reacts to its own click without a reload", () => {
    // The poll had parked itself and stale progress outranked the fresh prop, so
    // the button only changed after a remount. Re-arm on runStatus, and trust a
    // non-staged polled status over the pre-click "staged".
    expect(SOURCE).toContain("}, [runId, runStatus]);");
    expect(SOURCE).toContain('polledStatus && polledStatus !== "staged" ? polledStatus : String(runStatus');
  });
});

describe("live progress on the canvas (#194)", () => {
  it("drives the current stage from the run's own signal, not only reported nodes", () => {
    // A live run whose current stage had not yet reported a workflow node used
    // to fall through to "pending" and read as idle. groupStatus now takes the
    // run's current_stage/active and marks that group running.
    expect(SOURCE).toContain("stages.includes(currentStage) || (!currentStage && groupIndex === 0)");
    expect(SOURCE).toContain("const groupStatuses = groups.map(");
  });

  it("distinguishes a pipeline waiting to be started from one that is running", () => {
    // The accepted-but-unstarted first group reads "waiting to start" and points
    // at the Run control instead of sharing the neutral "pending" look.
    expect(SOURCE).toContain("const waiting = canStart &&");
    expect(SOURCE).toContain('t("Waiting to start")');
    expect(SOURCE).toContain('t("Press Run above to start")');
  });

  it("animates the arrows into and out of the running stage", () => {
    // The arrow after "Data understood" was hardcoded inert.
    expect(SOURCE).not.toContain("<Arrow active={false} complete />");
    expect(SOURCE).toContain('<Arrow active={groupStatuses[0] === "running"} complete />');
    expect(SOURCE).toContain('groupStatuses[index + 1] === "running"');
  });

  it("translates the new waiting strings", () => {
    for (const key of ["Waiting to start", "Press Run above to start"]) {
      expect(CATALOGUE.includes(`"${key}":`), `no Turkish entry for ${key}`).toBe(true);
    }
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
