import { describe, expect, it } from "vitest";

import PANEL from "./PlannerPanel.tsx?raw";
import PIPELINE from "./GuidedPipeline.tsx?raw";
import API from "../lib/api.ts?raw";
import I18N from "../lib/i18n.ts?raw";

describe("planner override confirmation and canvas feedback (#328)", () => {
  it("keeps a planner override pending until the person applies or cancels it", () => {
    expect(PANEL).toContain("pending_override");
    expect(PANEL).toContain('t("Apply override")');
    expect(PANEL).toContain('t("Cancel")');
    expect(PANEL).toContain("api.applyPlannerOverride");
    expect(PANEL).toContain("api.discardPlannerOverride");
    expect(API).toContain("planner-overrides");
  });

  it("marks a canvas node whose stage has a human checkpoint", () => {
    expect(PIPELINE).toContain("checkpointStages");
    expect(PIPELINE).toContain('t("Human approval")');
  });

  it("translates every new visible control", () => {
    expect(I18N).toContain('"Apply override": "Override\u2019ı uygula"');
    expect(I18N).toContain('"Human approval": "İnsan onayı"');
  });
});
