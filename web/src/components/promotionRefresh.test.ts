import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import WORKSPACE_PAGE from "../pages/AutomationWorkspace.tsx?raw";
import PIPELINE_SOURCE from "./GuidedPipeline.tsx?raw";
import ROUTING_SOURCE from "./stagingRoutingState.ts?raw";
import UNDERSTANDING_SOURCE from "./UnderstandingWorkspace.tsx?raw";

/** The body of the page's run re-read, so a match cannot come from elsewhere.
 *
 * #465 renamed this from `onPromoted`: pinning a target is the same shape of
 * event -- the server re-enters the graph and the client has to pick the status
 * back up -- so the two share one implementation, named for what it does rather
 * than for one of the two things that cause it.
 */
function promotionCallback(): string {
  const start = WORKSPACE_PAGE.indexOf("const refreshRun = useCallback(");
  expect(start).toBeGreaterThan(-1);
  return WORKSPACE_PAGE.slice(start, WORKSPACE_PAGE.indexOf("const onGateAnswered = useCallback("));
}

describe("the canvas catches up after a promotion (#462)", () => {
  it("re-reads the run, not only the workspace", () => {
    // Promotion is not a synchronous state change. `_replan_after_promotion`
    // puts the run back into `staging` and re-enters the graph at `intake` on a
    // background worker, so the re-authored plan does not exist yet when the
    // promote call returns. Reading the workspace alone stores the
    // pre-promotion snapshot, which is what a page refresh was fixing.
    const body = promotionCallback();
    expect(body).toContain("api.runProgress(runId)");
    expect(body).toContain("setRunStatus(String(progress.status ?? \"\"))");
    expect(body).toContain("api.stagingWorkspace(runId)");
  });

  it("reads the run first, so a fast replan cannot leave a stale plan", () => {
    // Concurrent reads could take the workspace while the worker was still
    // re-profiling and the status after it finished: a stale plan with no poll
    // left to correct it. The worker writes the workspace before it flips the
    // status, so a `staged` answer read first proves the workspace behind it is
    // the new one.
    const body = promotionCallback();
    expect(body.indexOf("api.runProgress(runId)")).toBeLessThan(body.indexOf("api.stagingWorkspace(runId)"));
    expect(body).not.toContain("Promise.all");
  });

  it("relies on the run's own status rather than the replan string", () => {
    // The promote response says `replanning` / `plan_accepted` / `run_active` /
    // `nothing_promoted` / `unavailable`. A client that reasons about that
    // string breaks the moment a sixth outcome is added; the status is the
    // truth, and it is already `staging` when the response lands because the
    // server flips it under its lock before starting the worker.
    expect(promotionCallback()).not.toContain("replan");
  });

  it("resumes the poll that was gated off, without a new one", () => {
    // `staging` is an active status, so the existing effect restarts on its own
    // once `runStatus` is picked up -- and the terminal outcomes start no
    // worker, so the status comes back unchanged and nothing polls.
    expect(WORKSPACE_PAGE).toContain("if (!runId || !isRunActive(runStatus)) return;");
    expect(WORKSPACE_PAGE).toContain("}, [runId, runStatus]);");
  });

  it("reaches every promotion callback, including the one that did nothing", () => {
    // `UnderstandingProgress`'s was a literal no-op on the stated assumption
    // that the parent polls while understanding is live. It does not: the poll
    // is gated on `isRunActive`, and by the time this notice is on screen the
    // run is `staged` or `awaiting_human`.
    expect(UNDERSTANDING_SOURCE).not.toContain("const afterPromotion = () => undefined;");
    expect(UNDERSTANDING_SOURCE).toContain("if (onPromoted) { onPromoted(); return; }");
    expect(WORKSPACE_PAGE).toContain("<UnderstandingProgress");
    expect(WORKSPACE_PAGE).toContain("onPromoted={() => void refreshRun()}");
  });

  it("threads the same callback to the panels that did refresh, but too early", () => {
    // `handlePromoted`, `reloadWorkspace` and `PlanSummary`'s inline callback
    // each did one immediate `stagingWorkspace` read while the worker was still
    // running, so they stored the old workspace and never looked again.
    const handlers = UNDERSTANDING_SOURCE.match(/if \(onPromoted\) \{ onPromoted\(\); return; \}/g) ?? [];
    expect(handlers.length).toBe(3);
    expect(PIPELINE_SOURCE).toContain("if (onPromoted) { onPromoted(); return; }");
  });

  it("keeps working for a caller that supplies no run refresh", () => {
    // Each handler still falls back to the single workspace read it did before,
    // so a panel mounted outside the page keeps its old behaviour rather than
    // silently refreshing nothing.
    expect(UNDERSTANDING_SOURCE).toContain("void api.stagingWorkspace(runId).then(onWorkspaceUpdated).catch(() => undefined);");
    expect(UNDERSTANDING_SOURCE).toContain("onPromoted?: () => void");
  });
});

describe("the plan node while it is being re-authored (#462)", () => {
  it("knows a plan is being rewritten", () => {
    // Understanding running again over a workspace that already carries a plan
    // means that plan is being rewritten -- the only way to reach that is a
    // promotion re-entering the graph.
    expect(ROUTING_SOURCE).toContain(
      'const replanning = isRunActive(String(progress?.status ?? "")) && Boolean(workspace?.recommended_plan);',
    );
    expect(ROUTING_SOURCE).toContain("replanning: boolean;");
  });

  it("stops reporting the outcome the promotion is answering", () => {
    // The outcome describes a settled run. Mid-replan the run is not settled,
    // and the outcome on hand is the previous one -- "Blocked — no plan was
    // created" over the promotion that is un-blocking it.
    expect(ROUTING_SOURCE).toContain("outcome: replanning ? undefined : outcome,");
  });

  it("gives the node the state it had no way to be in", () => {
    // Its four states were pending, ready, accepted and blocked, so mid-replan
    // it either claimed a finished proposal or reported the old deferral.
    expect(UNDERSTANDING_SOURCE).toContain('| "blocked" | "working"');
    expect(UNDERSTANDING_SOURCE).toContain('proposal === "working" ? t("Re-authoring after the promoted tables…")');
    expect(CATALOGUE).toContain('"Re-authoring after the promoted tables…"');
  });

  it("is used by both canvases that draw the node", () => {
    expect(UNDERSTANDING_SOURCE).toContain('proposal={routing.replanning ? "working" :');
    expect(PIPELINE_SOURCE).toContain('accepted ? "accepted" : routing.replanning ? "working" : "ready"');
  });

  it("never overrides an accepted plan", () => {
    // An accepted plan is a decision; the ML half of the canvas runs it, and a
    // promotion cannot re-author it (`_replan_after_promotion` returns
    // `plan_accepted` and changes nothing).
    expect(PIPELINE_SOURCE).toContain('accepted ? "accepted" : routing.replanning');
  });
});
