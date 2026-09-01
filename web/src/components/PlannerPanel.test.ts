import { describe, expect, it } from "vitest";

import PANEL_SOURCE from "./PlannerPanel.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";

/**
 * #246: the planner would say in chat that it was moving the run on to the ML
 * stage, and nothing happened -- no ML nodes, no gate, no error. One cause named
 * in the report: #231 made the runner refuse a planner graph edit that would
 * leave the pipeline unable to run, returning the reason in `graph_edit_rejected`
 * rather than applying it. The panel ignored that field, so the edit that would
 * have carried the run forward was dropped silently while the reply's prose
 * still claimed the move. The refusal must be shown, never swallowed.
 */
describe("planner reports a refused graph edit instead of dropping it (#246)", () => {
  it("reads graph_edit_rejected from the planner reply", () => {
    expect(PANEL_SOURCE).toContain("res.graph_edit_rejected");
    expect(PANEL_SOURCE).toContain("setGraphEditRejected");
  });

  it("renders the refusal as a visible alert, not a console log or silent drop", () => {
    expect(PANEL_SOURCE).toContain('role="alert"');
    expect(PANEL_SOURCE).toContain('t("The planner\'s pipeline change was not applied")');
    // The reason string itself is shown so the person learns why it did not move.
    expect(PANEL_SOURCE).toContain("{graphEditRejected}");
  });

  it("carries a Turkish entry for the new notice (catalogue test only sees literals)", () => {
    expect(CATALOGUE).toContain("The planner's pipeline change was not applied");
    expect(CATALOGUE).toContain("Planlayıcının boru hattı değişikliği uygulanmadı");
  });
});

/**
 * #260: once shown, the card had no way to close it -- `graphEditRejected` is
 * only cleared when the run changes or the next planner message overwrites
 * it, so it stayed pinned in the chat until one of those happened on its own.
 */
describe("the refused-graph-edit card can be dismissed (#260)", () => {
  it("has a close control that clears graphEditRejected", () => {
    expect(PANEL_SOURCE).toContain("onClick={() => setGraphEditRejected(null)}");
  });

  it("carries a Turkish entry for the dismiss control", () => {
    expect(CATALOGUE).toContain('"Dismiss": "Kapat"');
  });
});
