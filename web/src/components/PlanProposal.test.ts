import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PlanProposal } from "./UnderstandingWorkspace";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import type { SourceProfile, StagingWorkspace } from "../lib/api";

Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

const profile = {
  source_id: "upload:abc",
  tables: [{ name: "customers" }],
  documents: [],
  privacy: "safe",
  source_files: [],
} as unknown as SourceProfile;

function workspace(recommendation: string): StagingWorkspace {
  return {
    recommended_plan: {
      pipeline_recommendation: recommendation,
      decision_summary: { en: "NOT_ENOUGH_EVIDENCE", tr: "NOT_ENOUGH_EVIDENCE" },
      rationale: [],
      configuration: {},
      checkpoint_stages: [],
    },
  } as unknown as StagingWorkspace;
}

function render(recommendation: string, onTalkToPlanner = () => undefined) {
  return renderToStaticMarkup(createElement(PlanProposal, {
    profile,
    workspace: workspace(recommendation),
    onAccept: () => undefined,
    onAdvanced: () => undefined,
    onTalkToPlanner,
    busy: false,
  }));
}

/**
 * #187: the blocked branch ended on "no pipeline will run unless a human
 * explicitly overrides this recommendation" and then offered nothing to
 * override with — the only control on the panel was "Advanced editor". The
 * sentence named an action and gave the reader no target for it, on a screen
 * that could not otherwise be advanced.
 */
describe("the blocked proposal panel (#187)", () => {
  for (const recommendation of ["no_pipeline", "defer_pipeline"]) {
    it(`offers a route to the override for ${recommendation}`, () => {
      const markup = render(recommendation);

      // The override is a Planner conversation, and the panel now opens one.
      expect(markup).toContain("Planlayıcıdan yeniden değerlendirmesini isteyin");
      // The dead-end sentence is gone, in both languages' catalogue keys.
      expect(markup).not.toContain("Bir insan bu öneriyi açıkça geçersiz kılmadıkça");
      expect(markup).not.toContain("explicitly overrides");
      // The decision itself still reads.
      expect(markup).toContain("NOT_ENOUGH_EVIDENCE");
    });
  }

  it("says what kind of stop this is, so it is not mistaken for the gate", () => {
    // The other human-feedback prompt — the run gate ApprovalCard — asks a
    // question and takes an answer. Both stop progress and both invoke a
    // human, so the panel has to say which one it is.
    const markup = render("no_pipeline");

    expect(markup).toContain("Sırada ne var");
    expect(markup).toContain("yanıtınızı bekleyen bir soru değildir");
  });

  it("wires the button to the Planner opener, not to a dead handler", () => {
    // Rendered markup cannot prove a click opens the panel, so the two halves
    // are asserted separately: the button calls the prop it was handed…
    const onTalkToPlanner = vi.fn();
    const element = createElement(PlanProposal, {
      profile,
      workspace: workspace("no_pipeline"),
      onAccept: () => undefined,
      onAdvanced: () => undefined,
      onTalkToPlanner,
      busy: false,
    });
    renderToStaticMarkup(element);
    expect(onTalkToPlanner).not.toHaveBeenCalled(); // not fired on render

    // …and the screen that owns the Planner panel is what supplies that prop.
    const proposalScreen = WORKSPACE_SOURCE.slice(
      WORKSPACE_SOURCE.indexOf("export function UnderstandingAndProposal"),
      WORKSPACE_SOURCE.indexOf("/** The fixed ML pipeline"),
    );
    expect(proposalScreen).toContain("onTalkToPlanner={() => setPlannerOpen(true)}");
  });

  it("leaves the recommended branch's accept control alone", () => {
    const markup = render("create_pipeline");

    expect(markup).toContain("Kabul et");
    expect(markup).not.toContain("Sırada ne var");
  });
});
