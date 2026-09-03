import { describe, expect, it } from "vitest";

import WORKSPACE_PAGE from "../pages/AutomationWorkspace.tsx?raw";
import SOURCE from "./GuidedPipeline.tsx?raw";

/** The body of `afterPin`, so a match cannot come from elsewhere. */
function pinBody(): string {
  const start = SOURCE.indexOf("async function afterPin(");
  expect(start).toBeGreaterThan(-1);
  return SOURCE.slice(start, SOURCE.indexOf("async function inspectStage("));
}

describe("pinning a target looks like the run restarting (#465)", () => {
  it("tells the page, which owns the status both polls read", () => {
    // `afterPin` cleared only its own local `error` and `detail`. There was
    // no callback up to `AutomationWorkspace`, so the parent's `error` kept the
    // stale text and its `runStatus` stayed "failed" -- while the server had
    // already cleared `runtime.error`, set the run `resuming`, and re-entered
    // the graph at `problem_discovery`.
    expect(pinBody()).toContain("if (onReframed) onReframed();");
    expect(SOURCE).toContain("onReframed?: () => void;");
    expect(WORKSPACE_PAGE).toContain("onReframed={() => void refreshRun()}");
  });

  it("stops storing one snapshot and calling it done", () => {
    // `setProgress(await api.runProgress(runId))` stored a single reading
    // without restarting the timer: this component's poll re-arms only from
    // inside its own chain or when the `runStatus` prop changes, and that prop
    // is the page's -- which nothing was updating. So the client sat on one
    // post-pin reading forever.
    const body = pinBody();
    expect(body.indexOf("if (onReframed) onReframed();")).toBeLessThan(
      body.indexOf("else setProgress(await api.runProgress(runId));"),
    );
    expect(SOURCE).toContain("}, [runId, runStatus, accepted]);");
  });

  it("keeps its single snapshot for a caller that supplies no callback", () => {
    expect(pinBody()).toContain("else setProgress(await api.runProgress(runId));");
  });

  it("shares one implementation with the promotion re-entry", () => {
    // #462 is the same defect from the other direction: a server-side re-entry
    // the client never noticed. Two names for one operation would drift.
    expect(WORKSPACE_PAGE).toContain("const refreshRun = useCallback(");
    expect(WORKSPACE_PAGE).toContain("onPromoted={() => void refreshRun()}");
  });
});

describe("the red banner clears when the run's error does (#465)", () => {
  it("stops writing the banner only when there is something to write", () => {
    // Both feeds were `if (message) setError(message)`, so an error that had
    // *gone away* on the server could never clear the box -- only an explicit
    // `setError(null)` somewhere else could, and nothing called one.
    expect(WORKSPACE_PAGE).not.toContain("const message = runErrorText(progress.error); if (message) setError(message);");
    expect(WORKSPACE_PAGE).not.toContain("const message = runErrorText(progress?.error); if (message) setError(message);");
    expect(WORKSPACE_PAGE).toContain('applyRunError(runErrorText(progress.error) ?? "")');
    expect(WORKSPACE_PAGE).toContain('applyRunError(runErrorText(progress?.error) ?? "")');
  });

  it("clears only the run's own error, not a client-side one", () => {
    // A blanket `setError(message || null)` would wipe a refused pause or an
    // artifact that would not load, two seconds after it appeared -- the poll
    // runs every 2.2s while a run is active. That is the opposite defect.
    expect(WORKSPACE_PAGE).toContain("const shownRunError = useRef<string | null>(null);");
    expect(WORKSPACE_PAGE).toContain(
      "setError((current) => (message ? message : current === shownRunError.current ? null : current));",
    );
  });

  it("clears it on the re-read the pin triggers, not only on the next poll", () => {
    // The poll only starts once the status is picked up; the banner should be
    // gone in the same pass that picks it up.
    const start = WORKSPACE_PAGE.indexOf("const refreshRun = useCallback(");
    const body = WORKSPACE_PAGE.slice(start, WORKSPACE_PAGE.indexOf("const onGateAnswered = useCallback("));
    expect(body).toContain('applyRunError(runErrorText(progress.error) ?? "")');
  });

  it("leaves openExecution's blanket clear alone", () => {
    // #443 got this right for a run opened from Execution history, and it is
    // deliberately blunter: switching to a different run should also drop a
    // client-side error, because that error belongs to the run being left.
    expect(WORKSPACE_PAGE).toContain("setError(runErrorText(progress?.error) || null);");
  });
});
