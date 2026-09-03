import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import SOURCE from "./PlannerPanel.tsx?raw";

/**
 * #449 reports that `checkpoint_stages` is never displayed, and points at
 * `PlannerPanel.tsx:109-125` mapping only `configuration_patch`,
 * `stage_directives` and `max_retries_by_stage`.
 *
 * That is not the code on main: `overrideItems` maps `checkpoint_stages` and
 * `auto_proceed_stages` too, and has since #356. What the report gets right is
 * the experience -- a person who asks for a checkpoint gets no confirmation
 * they can check their request against. Two reasons, both real:
 *
 * 1. The row read "eda → İnsan onayı": an arrow between a raw stage id and a
 *    label, sitting among rows whose right-hand side is JSON.
 * 2. Applying the override cleared the list and left the word "applied". The
 *    moment the checkpoint took effect was the moment every trace of what it
 *    was disappeared.
 */
describe("the planner confirms a requested checkpoint (#449)", () => {
  it("says what will happen, in a sentence", () => {
    expect(SOURCE).toContain('t("Review checkpoint after {stage}", { stage: stageName(stage) })');
    expect(SOURCE).not.toContain('`${stage.replaceAll("_", " ")} → ${t("Human approval")}`');
    expect(CATALOGUE).toContain('"Review checkpoint after {stage}": "{stage} sonrasında inceleme durağı"');
  });

  it("names the stage the way the rest of the product names it", () => {
    // `problem_discovery`.replaceAll("_", " ") is "problem discovery", which is
    // not what any other surface calls that stage.
    expect(SOURCE).toContain('import { stageName } from "./PipelineRail";');
    expect(SOURCE).toContain("`${stageName(stage)} → ${value}`");
  });

  it("gives the other two supervision rows the same treatment", () => {
    // They had the same shape and the same problem; leaving them would make
    // one row in three a sentence.
    expect(SOURCE).toContain('t("Proceed automatically after {stage}"');
    expect(SOURCE).toContain('t("Retry {stage} up to {count} times"');
    expect(CATALOGUE).toContain('"Applied to this run": "Bu koşuya uygulandı"');
  });

  it("keeps what was applied on screen instead of one transient word", () => {
    // `resolveOverride("apply")` set `recommendations` from the *new* pending
    // override -- which is null -- so the list emptied at the exact moment the
    // change took effect.
    expect(SOURCE).toContain("const settled = overrideItems(pendingOverride);");
    expect(SOURCE).toContain('setApplied(action === "apply" ? settled : []);');
    expect(SOURCE).toContain('<p className="mb-2 text-2xs font-semibold text-ok-700">{t("Applied to this run")}</p>');
  });

  it("does not show the applied record beside a newer proposal", () => {
    // Two lists of pipeline changes side by side, one of them stale, is worse
    // than the single stale word it replaces.
    expect(SOURCE).toContain("{!pendingOverride && applied.length > 0 && (");
    expect(SOURCE).toContain("if (pending) setApplied([]);");
  });

  it("clears it when the panel changes run", () => {
    expect(SOURCE).toContain("setMessages([]); setRecommendations([]); setApplied([]);");
  });

  it("shows nothing applied after a discard", () => {
    // A discarded proposal changed nothing, so there is nothing to report.
    const resolve = SOURCE.slice(SOURCE.indexOf('async function resolveOverride('));
    expect(resolve).toContain('action === "apply" ? settled : []');
  });
});
