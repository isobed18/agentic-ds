import { describe, expect, it } from "vitest";

import type { StageAttempt, StageDetail } from "../lib/api";
import { CHECK_TEXT, checkText, runErrorText, stageFailure } from "./stageFailure";
import CATALOGUE from "../lib/i18n.ts?raw";

const attempt = (over: Partial<StageAttempt> = {}): StageAttempt => ({
  stage_id: "schema.plan",
  attempt: 1,
  artifact_ids: [],
  ...over,
});

const detail = (attempts: StageAttempt[]): StageDetail =>
  ({ attempts }) as unknown as StageDetail;

describe("what to say about a failed stage", () => {
  it("reports the attempt's own error", () => {
    expect(stageFailure(detail([attempt({ error: "boom" })]), null)).toEqual({
      error: "boom",
      checks: [],
    });
  });

  it("reports the mechanical checks the attempt did not meet", () => {
    // These are the specific answer -- "the plan failed a trial execution
    // against the real tables" says what to change, where a stack trace does
    // not -- and they were two clicks away inside the stage attempt history.
    const failure = stageFailure(
      detail([
        attempt({
          critique: {
            unmet_criteria: ["schema.plan_trial_passed"],
            findings: [{ check_id: "schema.plan_trial_passed", severity: "high", evidence: "trial raised" }],
          },
        }),
      ]),
      null,
    );
    expect(failure).toEqual({
      error: null,
      checks: [{ id: "schema.plan_trial_passed", evidence: "trial raised", measurements: [] }],
    });
  });

  it("leads with the last attempt that recorded something, not the first", () => {
    // A retried stage has a history. The earlier attempts are the ones that
    // were superseded, so leading with #1 explains a failure already moved past.
    const failure = stageFailure(
      detail([attempt({ attempt: 1, error: "first try" }), attempt({ attempt: 2, error: "still broken" })]),
      null,
    );
    expect(failure?.error).toBe("still broken");
  });

  it("skips attempts that recorded nothing", () => {
    const failure = stageFailure(
      detail([attempt({ attempt: 1, error: "first try" }), attempt({ attempt: 2, verdict: "running" })]),
      null,
    );
    expect(failure?.error).toBe("first try");
  });

  it("falls back to the run's own error when no stage reported an attempt", () => {
    // The case that produced the empty panel: a run that died in its first
    // group has no stage history at all, so this is the only evidence there is.
    expect(stageFailure(null, "planner crashed")).toEqual({ error: "planner crashed", checks: [] });
    expect(stageFailure(detail([]), "planner crashed")?.error).toBe("planner crashed");
  });

  it("prefers the stage's evidence over the run's summary", () => {
    expect(stageFailure(detail([attempt({ error: "boom" })]), "run failed")?.error).toBe("boom");
  });

  it("says nothing when there is nothing recorded anywhere", () => {
    expect(stageFailure(null, null)).toBeNull();
    expect(stageFailure(detail([attempt()]), null)).toBeNull();
  });
});

describe("the measured facts under a failed check (#427)", () => {
  it("carries the measurements the run recorded for the check", () => {
    // `compute_support` turns "this framing is not viable" into facts and
    // writes them onto every candidate. They used to stay in the artifact
    // while the panel showed only a sentence about the check.
    const failure = stageFailure(
      detail([
        attempt({
          critique: {
            unmet_criteria: ["problem.at_least_one_viable_candidate"],
            findings: [
              {
                check_id: "problem.at_least_one_viable_candidate",
                severity: "high",
                evidence: "None of the proposed ML problems is viable against this data.",
                measurements: [
                  "multiclass_classification on claim_description — too_many_classes: 'claim_description' has 97219 levels (limit 50)",
                  "regression on date_time_of_accident — task_target_mismatch: regression needs a numeric target",
                ],
              },
            ],
          },
        }),
      ]),
      null,
    );

    expect(failure?.checks[0].measurements).toHaveLength(2);
    expect(failure?.checks[0].measurements[0]).toContain("97219 levels");
  });

  it("defaults to none rather than undefined for an older record", () => {
    // A run recorded before this field existed has findings with no
    // `measurements`, and the panel maps over the array unconditionally.
    const failure = stageFailure(
      detail([
        attempt({
          critique: {
            unmet_criteria: ["problem.targets_exist"],
            findings: [{ check_id: "problem.targets_exist", severity: "high", evidence: "x" }],
          },
        }),
      ]),
      null,
    );

    expect(failure?.checks[0].measurements).toEqual([]);
  });

  it("has none for a check with no finding at all", () => {
    const failure = stageFailure(
      detail([attempt({ critique: { unmet_criteria: ["problem.targets_exist"], findings: [] } })]),
      null,
    );

    expect(failure?.checks).toEqual([
      { id: "problem.targets_exist", evidence: null, measurements: [] },
    ]);
  });
});

describe("check labels", () => {
  it("translates a known check id", () => {
    expect(checkText("schema.plan_trial_passed")).toBe(
      "The plan failed a trial execution against the real tables.",
    );
  });

  it("falls back to recorded evidence, then to the raw id", () => {
    expect(checkText("schema.unheard_of", "the join exploded")).toBe("the join exploded");
    expect(checkText("schema.unheard_of")).toBe("schema.unheard_of");
  });

  it("labels every check the pipeline can fail, not just the schema ones", () => {
    // #427: the map held the three `schema.*` ids, so a failed problem
    // discovery fell through to the server's English evidence -- which was
    // itself `"Mechanical check failed: " + <the condition that should hold>`.
    // Two bullets that both read as assertions everything was fine.
    for (const id of [
      "integration.grain_preserved",
      "problem.at_least_one_viable_candidate",
      "problem.targets_exist",
      "validation.strategy_matches_detected_signals",
      "validation.strategy_trial_passed",
      "eda.target_distribution_reported",
      "eda.all_features_covered",
      "eda.missingness_quantified",
      "features.columns_accounted_for",
      "features.preprocessing_is_fold_local",
      "features.pipeline_is_fitted_object",
      "features.no_test_fold_statistics",
      "model_selection.baseline_included",
      "evaluation.holdout_metrics_reported",
      "evaluation.baseline_comparison_present",
    ]) {
      expect(CHECK_TEXT[id], `${id} has no reader-facing label`).toBeTruthy();
    }
  });

  it("says the check failed, never the condition that should have held", () => {
    // The exact regression: `"Mechanical check failed: At least one proposed
    // problem has measured viable support."` -- a success sentence with
    // "failed:" glued to the front.
    for (const [id, label] of Object.entries(CHECK_TEXT)) {
      expect(label, `${id} reads as a success sentence`).not.toContain("Mechanical check failed");
      expect(label.startsWith("At least one"), `${id} states the condition, not the failure`).toBe(false);
    }
  });

  it("translates every label, which the literal scanner cannot see", () => {
    // These reach t() through `CHECK_TEXT[id]`, so `i18n.test.ts` cannot find
    // them and an untranslated one would ship on a Turkish screen quietly --
    // which is exactly what happened to the problem.* lines.
    const untranslated = Object.values(CHECK_TEXT).filter(
      (label) => !CATALOGUE.includes(`"${label}":`),
    );
    expect(untranslated, "these render in English when the language is Turkish").toEqual([]);
  });
});

describe("reading a recorded run error", () => {
  it("reads a plain string, which is what older records hold", () => {
    expect(runErrorText("boom", false)).toBe("boom");
    expect(runErrorText("", false)).toBeNull();
  });

  it("picks the reader's half of a bilingual error", () => {
    const both = { en: "Planner failed", tr: "Planlayıcı başarısız oldu" };
    expect(runErrorText(both, true)).toBe("Planlayıcı başarısız oldu");
    expect(runErrorText(both, false)).toBe("Planner failed");
  });

  it("falls back to English when only that half was recorded", () => {
    expect(runErrorText({ en: "Planner failed" }, true)).toBe("Planner failed");
  });

  it("reports nothing for a run that recorded no error", () => {
    expect(runErrorText(null, true)).toBeNull();
    expect(runErrorText(undefined, false)).toBeNull();
  });
});
