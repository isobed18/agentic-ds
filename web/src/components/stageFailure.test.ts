import { describe, expect, it } from "vitest";

import type { StageAttempt, StageDetail } from "../lib/api";
import { checkText, runErrorText, stageFailure } from "./stageFailure";

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
      checks: [{ id: "schema.plan_trial_passed", evidence: "trial raised" }],
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
