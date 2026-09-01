/**
 * Side-panel openers in the ML workspace (#188).
 *
 * Each opener was a one-way `set…(true)`, so clicking it while its panel was
 * already open did nothing and the panel could only be dismissed with its own
 * ×. The close paths were all present -- only the opening side was missing the
 * other half.
 *
 * These are source assertions rather than renders: what went wrong is the
 * shape of the handler, and a render test would have to drive four separate
 * canvas-mounted components to reach the same statement.
 */
import { describe, expect, it } from "vitest";

import GUIDED_SOURCE from "./GuidedPipeline.tsx?raw";
import BUILDER_SOURCE from "./PipelineBuilder.tsx?raw";

/** The openers named in #188, each with the state its panel reads. */
const OPENERS = [
  { source: GUIDED_SOURCE, where: "GuidedPipeline: Review plan", label: 'Review plan' },
  { source: GUIDED_SOURCE, where: "GuidedPipeline: Chat with Planner", label: 'Chat with Planner' },
  { source: BUILDER_SOURCE, where: "PipelineBuilder: Add component", label: 'Add component' },
  { source: BUILDER_SOURCE, where: "PipelineBuilder: Planner", label: 'Planner' },
];

/** The whole <button> element whose visible label is `label`. */
function opener(source: string, label: string): string {
  const at = source.indexOf(`{t("${label}")}</button>`);
  expect(at, `no opener labelled ${label}`).toBeGreaterThan(-1);
  const begins = source.lastIndexOf("<button", at);
  return source.slice(begins, at);
}

describe("side-panel openers toggle (#188)", () => {
  it("no opener sets its panel open one way any more", () => {
    for (const { source, where, label } of OPENERS) {
      const button = opener(source, label);
      expect(button, where).not.toMatch(/set\w+\((?:true|"summary")\)/);
    }
  });

  it("every opener flips the state it reads", () => {
    for (const { source, where, label } of OPENERS) {
      const button = opener(source, label);
      // Either the boolean flip or the "same value closes it" form, whether that
    // value is a literal or the panel this phase's opener targets (#214).
      expect(button, where).toMatch(/\(\w+\) => !\w+|current === (\w+|"summary") \? null : (?:\1)/);
    }
  });

  it("every opener reports its panel's state to assistive tech", () => {
    for (const { source, where, label } of OPENERS) {
      expect(opener(source, label), where).toContain("aria-expanded=");
    }
  });

  it("leaves the × close paths alone", () => {
    // The panels' own dismissal is what still worked; it must keep working.
    expect(GUIDED_SOURCE).toContain("setPlannerOpen(false)");
    expect(BUILDER_SOURCE).toContain("setPlannerOpen(false)");
  });
});
