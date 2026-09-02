/**
 * The "Proposed plan" panel that explained nothing (#385).
 *
 * `RoutingInspector` gated the whole panel on `onAdvanced` being passed. The
 * guided canvas passes it; the understanding canvas passes none of the plan
 * handlers, so a run the Planner had already declined showed the generic
 * "Plan not ready yet" empty state -- next to a node correctly reading
 * "Blocked — no plan was created". The explanation is what the panel is for;
 * only the actions depend on having somewhere to go.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RoutingInspector } from "./UnderstandingWorkspace";
import type { SourceProfile, StagingWorkspace } from "../lib/api";
import type { StagingRoutingState } from "./stagingRoutingState";

const profile = { source_id: "s", privacy: "safe", source_files: [], tables: [], documents: [] } as unknown as SourceProfile;
const routing = {} as unknown as StagingRoutingState;

/** A run the Planner finished and declined -- the reported case exactly. */
const declined = {
  recommended_plan: {
    pipeline_recommendation: "no_pipeline",
    decision_summary: { en: "No target column survives the join.", tr: "Birleştirmeden sonra hedef sütun kalmıyor." },
    rationale: [{ en: "Every candidate label is constant.", tr: "Her aday etiket sabit." }],
    checkpoint_stages: [],
    configuration: {},
  },
} as unknown as StagingWorkspace;

function render(workspace: StagingWorkspace | null, handlers: Record<string, () => void> = {}) {
  return renderToStaticMarkup(createElement(RoutingInspector, {
    selection: "proposal" as const,
    profile,
    workspace,
    routing,
    onClose: () => undefined,
    onOpenArtifact: () => undefined,
    ...handlers,
  }));
}

describe("the declined-plan explanation (#385)", () => {
  it("renders without any of the plan action handlers", () => {
    // This is the understanding canvas: no onAdvanced, no onAccept, no
    // onOpenPlanner.
    const markup = render(declined);

    expect(markup).toContain("Birleştirmeden sonra hedef sütun kalmıyor");
    expect(markup).toContain("Her aday etiket sabit");
    // The badge and heading the guided canvas already showed.
    expect(markup).toMatch(/ML işlem hattı önerilmiyor|No ML pipeline recommended/);
    expect(markup).not.toMatch(/Plan not ready yet|Plan henüz hazır değil/);
  });

  it("hides the actions it cannot perform rather than the explanation", () => {
    const markup = render(declined);

    expect(markup).not.toMatch(/Advanced editor|Gelişmiş düzenleyici/);
    // No action buttons at all, and so no empty toolbar rule above them.
    expect(markup).not.toContain("btn-primary");
    expect(markup).not.toContain("border-t border-line pt-4");
  });

  it("still offers them where they work", () => {
    const markup = render(declined, { onAdvanced: () => undefined, onOpenPlanner: () => undefined });

    expect(markup).toMatch(/Advanced editor|Gelişmiş düzenleyici/);
  });

  it("keeps the empty state for a run that really has no plan yet", () => {
    // "Plan not ready yet" now means what it says, rather than standing in for
    // a decision the Planner has already made and explained.
    expect(render({} as unknown as StagingWorkspace)).toMatch(/Plan not ready yet|Plan henüz hazır değil/);
    expect(render(null)).toMatch(/Plan not ready yet|Plan henüz hazır değil/);
  });
});
