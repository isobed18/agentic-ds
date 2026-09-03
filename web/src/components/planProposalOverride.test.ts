/**
 * The blocked plan panel told the reader a human must override, and gave them
 * nothing to override with (#187).
 *
 * The recommended branch of the same component has both an action and a
 * closing hint; the blocked branch kept the strongest claim about human
 * authority and dropped every affordance. These assertions read the component
 * source, as the other UI tests here do.
 */
import { describe, expect, it } from "vitest";
import SOURCE from "./UnderstandingWorkspace.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";

const BLOCKED = SOURCE.slice(
  SOURCE.indexOf("No ML pipeline recommended"),
  SOURCE.indexOf("Proposed — not executable yet"),
);

describe("the blocked plan panel (#187)", () => {
  it("offers the override it names", () => {
    expect(BLOCKED).toContain("Ask the Planner to reconsider");
    expect(BLOCKED).toContain("onClick={onOpenPlanner}");
  });

  it("says where the override is, not just that one exists", () => {
    // "A human must override" with no target sends the reader hunting for a
    // control that was not on the screen. #187 answered that by naming the
    // Planner; #429 found the answer was wrong -- the Planner is a chat box,
    // and pointing at it left the panel a dead end for a deferral whose target
    // was measurably viable. The override is on this panel now, so the same
    // requirement is met by the control itself rather than by a sentence
    // describing one somewhere else.
    expect(BLOCKED).toContain("<DeferredPlanOverride");
    expect(BLOCKED).not.toContain("The override is the Planner");
  });

  it("translates both new strings", () => {
    for (const key of [
      "Ask the Planner to reconsider",
      "The Planner can also be asked to reconsider: tell it what it is missing and it can propose a pipeline itself.",
    ]) {
      expect(CATALOGUE.includes(`"${key}":`), `no Turkish entry for ${key}`).toBe(true);
    }
  });

  it("still keeps the advanced editor as the second way out", () => {
    expect(BLOCKED).toContain("Advanced editor · Experimental");
  });
});
