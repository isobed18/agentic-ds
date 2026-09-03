/**
 * The section tabs in the project header (#306).
 *
 * The tabs are primary navigation but were styled `text-xs`, one step below the
 * body text and two below the page title, so they read as secondary metadata
 * rather than the way you move between a project's sections. The measured fix is
 * a larger type size and a taller hit area, while the active section stays
 * visually distinct and the centred layout is untouched so narrow screens are
 * unchanged.
 *
 * Pinned as source shape rather than a rendered pixel size, which no static
 * render can measure.
 */
import { describe, expect, it } from "vitest";

import PROJECT_SOURCE from "./ProjectWorkspace.tsx?raw";

/** The className string of the section-tab button, from its `.map(...)` call. */
function tabClass(): string {
  const match = /PROJECT_VIEWS\.map\([\s\S]*?className=\{cx\("([^"]+)"/.exec(PROJECT_SOURCE);
  expect(match, "the section-tab button was not found").not.toBeNull();
  return match![1];
}

describe("the project section tabs (#306)", () => {
  it("uses a larger type size than the secondary-metadata text-xs it replaced", () => {
    expect(tabClass()).toContain("text-sm");
    expect(tabClass()).not.toContain("text-xs");
  });

  it("gives the tab a taller hit area than the old py-1.5", () => {
    expect(tabClass()).toContain("py-2");
    expect(tabClass()).not.toContain("py-1.5");
  });

  it("keeps the active section visually distinct", () => {
    // The active tab still gets the raised surface; only the resting size grew.
    expect(PROJECT_SOURCE).toContain('view === item ? "bg-surface text-ink shadow-sm"');
  });

  it("leaves the tabs centred so narrow screens keep their layout", () => {
    // The nav is absolutely centred and outside the flex flow; #306 changes the
    // button size, not the strip's placement.
    expect(PROJECT_SOURCE).toContain('aria-label={t("Project sections")} className="absolute left-1/2');
  });
});
