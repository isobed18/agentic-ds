import { describe, expect, it } from "vitest";

import SOURCE from "./ProjectWorkspace.tsx?raw";
import { projectHref } from "./ProjectWorkspace";

describe("project library rows are real links (#405)", () => {
  it("points each row at the project's own URL", () => {
    expect(projectHref("proj_1")).toBe("/projects?project=proj_1");
    // An id is opaque, so it is escaped rather than trusted to be URL-safe.
    expect(projectHref("a b&c")).toBe("/projects?project=a%20b%26c");
  });

  it("renders the row as an anchor so the browser can open it in a new tab", () => {
    // A bare <button> gives middle-click and ctrl/cmd-click nothing to act on.
    expect(SOURCE).toContain("<Link to={projectHref(project.project_id)}");
    expect(SOURCE).not.toMatch(/<button type="button" onClick=\{\(\) => onOpen\(project\.project_id\)\}/);
  });

  it("keeps the same-tab fast path for a plain left click", () => {
    expect(SOURCE).toContain("if (opensElsewhere(event)) return; event.preventDefault(); onOpen(project.project_id);");
    expect(SOURCE).toContain("event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey");
  });
});
