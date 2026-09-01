import { describe, expect, it } from "vitest";

import type { GateDecision, HomeOutput, RunSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { itemsFrom, outputHref } from "./notificationItems";

const run = (over: Partial<RunSummary> = {}): RunSummary => ({
  run_id: "run-1",
  status: "completed",
  label: "Churn",
  last_activity: "2026-01-01T10:00:00Z",
  ...over,
});

const output = (over: Partial<HomeOutput> = {}): HomeOutput => ({
  kind: "model",
  project_id: "proj-1",
  project_name: "Retention",
  automation_id: "auto-1",
  automation_name: "Nightly churn",
  run_id: "run-1",
  artifact_id: "art-1",
  label: "GradientBoosting",
  created_at: "2026-01-01T10:05:00Z",
  ...over,
});

const question: GateDecision = {
  stage_id: "plan",
  attempt: 1,
  verdict: "escalate",
  reason_code: "human_decision_required",
  triggered_rules: [],
};

describe("notification items", () => {
  it("announces a model a run produced", () => {
    const items = itemsFrom([], [output()]);
    expect(items).toHaveLength(1);
    expect(items[0].id).toBe("art-1:model");
    expect(items[0].title).toBe(t("Model generated"));
    expect(items[0].tone).toBe("ok");
  });

  it("announces a report a run produced", () => {
    const items = itemsFrom([], [output({ kind: "report", artifact_id: "art-2", label: "Evaluation" })]);
    expect(items[0].id).toBe("art-2:report");
    expect(items[0].title).toBe(t("Report generated"));
  });

  it("names the automation that produced it", () => {
    // The artifact name alone does not say which pipeline made it, and someone
    // with several automations cannot tell two models apart without it.
    expect(itemsFrom([], [output()])[0].detail).toBe("GradientBoosting — Nightly churn");
    expect(itemsFrom([], [output({ automation_name: null })])[0].detail).toBe("GradientBoosting");
  });

  it("links a model to its automation's Models tab", () => {
    expect(outputHref(output())).toBe("/projects?project=proj-1&automation=auto-1&view=models");
  });

  it("links a report to its automation's Reports tab", () => {
    expect(outputHref(output({ kind: "report" }))).toBe(
      "/projects?project=proj-1&automation=auto-1&view=reports",
    );
  });

  it("falls back to the project when the automation is unknown", () => {
    expect(outputHref(output({ automation_id: null }))).toBe("/projects?project=proj-1&view=models");
  });

  it("still produces a link when the owning project could not be resolved", () => {
    expect(outputHref(output({ project_id: null, automation_id: null }))).toBe("/projects");
  });

  it("does not also say 'Run finished' for a run that produced an artifact", () => {
    // Two rows for one event is how a panel becomes noise. The artifact is the
    // more useful of the two, so it is the one that survives.
    expect(itemsFrom([run()], [output()]).map((i) => i.id)).toEqual(["art-1:model"]);
  });

  it("still says 'Run finished' when the run produced nothing", () => {
    expect(itemsFrom([run()], []).map((i) => i.id)).toEqual(["run-1:done"]);
  });

  it("keeps a pending question and a failure even when the run has artifacts", () => {
    // Suppression is only about the redundant "finished" line. A question is
    // still blocking and a failure is still a failure.
    const items = itemsFrom(
      [
        run({ run_id: "run-2", status: "awaiting_human", pending_question: question }),
        run({ run_id: "run-3", status: "failed" }),
      ],
      [output({ run_id: "run-2", artifact_id: "art-3" }), output({ run_id: "run-3", artifact_id: "art-4" })],
    );
    expect(items.filter((i) => i.tone === "stop")).toHaveLength(1);
    expect(items.filter((i) => i.tone === "warn")).toHaveLength(1);
  });

  it("orders everything by time, newest first, across both sources", () => {
    const items = itemsFrom(
      [run({ run_id: "run-9", last_activity: "2026-01-01T09:00:00Z" })],
      [
        output({ created_at: "2026-01-01T12:00:00Z" }),
        output({ artifact_id: "art-5", created_at: "2026-01-01T08:00:00Z" }),
      ],
    );
    expect(items.map((i) => i.at)).toEqual([
      "2026-01-01T12:00:00Z",
      "2026-01-01T09:00:00Z",
      "2026-01-01T08:00:00Z",
    ]);
  });
});
