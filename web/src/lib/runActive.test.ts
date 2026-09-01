/**
 * One answer to "is this run still progressing", not four hand-copied lists.
 *
 * The bug (#267): after answering a gate, the run enters `resuming` -- the
 * /answer endpoint returns exactly that status. The polling effect in
 * AutomationWorkspace listed only queued/staging/running, so it returned early
 * without re-arming its interval and nothing updated again until the page was
 * reloaded by hand. Three other places in the app answered the same question
 * and two of them already included `resuming`; this one had drifted.
 *
 * These read source rather than mounting components, as the other UI tests in
 * this project do. The point is that the lists are GONE, not that one of them
 * was corrected -- a fifth copy is how this recurs.
 */
import { describe, expect, it } from "vitest";
import AUTOMATION from "../pages/AutomationWorkspace.tsx?raw";
import UNDERSTANDING from "../components/UnderstandingWorkspace.tsx?raw";
import GUIDED from "../components/GuidedPipeline.tsx?raw";
import { isRunActive } from "./status";

describe("isRunActive", () => {
  it("counts resuming as active — it is the status a gate answer returns", () => {
    expect(isRunActive("resuming")).toBe(true);
  });

  it("covers every status a run passes through while still working", () => {
    for (const status of ["queued", "staging", "running", "resuming", "branches_running"]) {
      expect(isRunActive(status), status).toBe(true);
    }
  });

  it("stops for the states that are not progress", () => {
    // `interrupted` is excluded on purpose: polling a dead worker forever is
    // the bug that state exists to end.
    for (const status of ["awaiting_human", "completed", "failed", "interrupted", "staged", null]) {
      expect(isRunActive(status), String(status)).toBe(false);
    }
  });
});

describe("the polling call sites", () => {
  it("no longer keep their own copy of the list", () => {
    for (const [name, source] of [
      ["AutomationWorkspace", AUTOMATION],
      ["UnderstandingWorkspace", UNDERSTANDING],
      ["GuidedPipeline", GUIDED],
    ] as const) {
      expect(source.includes('"staging", "running"'), `${name} still inlines the list`).toBe(false);
      expect(source.includes("isRunActive"), `${name} does not use the helper`).toBe(true);
    }
    expect(GUIDED).toContain("const active = accepted && isRunActive(activeStatus)");
    expect(GUIDED).not.toContain('["running", "resuming"].includes(activeStatus)');
  });
});
