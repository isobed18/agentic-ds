import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import SOURCE from "./panelResize.tsx?raw";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import {
  PANEL_DEFAULT_WIDTH,
  PANEL_MAX_WIDTH,
  PANEL_MIN_WIDTH,
  clampPanelWidth,
  readStoredPanelWidth,
} from "./panelResize";

const WIDE = 1920;

describe("resizable docked panel (#407)", () => {
  it("starts at the width the column used to be pinned at", () => {
    // 27.5rem. Nothing about the default changes; what changes is that it is
    // now a starting point rather than the only value.
    expect(PANEL_DEFAULT_WIDTH).toBe(440);
    expect(clampPanelWidth(PANEL_DEFAULT_WIDTH, WIDE)).toBe(PANEL_DEFAULT_WIDTH);
  });

  it("keeps the panel readable and the canvas present", () => {
    expect(clampPanelWidth(10, WIDE)).toBe(PANEL_MIN_WIDTH);
    expect(clampPanelWidth(99_999, WIDE)).toBe(PANEL_MAX_WIDTH);
    // A drag that runs past the pointer's own edge cannot produce a negative
    // or non-finite width.
    expect(clampPanelWidth(-500, WIDE)).toBe(PANEL_MIN_WIDTH);
    expect(clampPanelWidth(Number.NaN, WIDE)).toBe(PANEL_DEFAULT_WIDTH);
  });

  it("gives a narrow viewport the 94vw ceiling the fixed column had", () => {
    // On a phone the panel is allowed nearly the whole width, because the
    // alternative is a panel too narrow to read.
    expect(clampPanelWidth(PANEL_MAX_WIDTH, 400)).toBe(376);
    // But never so narrow that the panel itself breaks up.
    expect(clampPanelWidth(PANEL_MAX_WIDTH, 200)).toBe(PANEL_MIN_WIDTH);
  });

  it("survives a browser with no session storage at all", () => {
    // A private window can throw on the accessor itself, not just return null.
    expect(readStoredPanelWidth()).toBeNull();
  });

  it("drives the canvas grid column from the dragged width", () => {
    expect(WORKSPACE_SOURCE).toContain("`minmax(0, 1fr) ${panelResize.width}px`");
    expect(WORKSPACE_SOURCE).not.toContain("minmax(0, 1fr) min(27.5rem, 94vw)");
    // The handle is on the panel's own left edge, and the panel is positioned
    // so it can be.
    expect(WORKSPACE_SOURCE).toContain('className="relative z-20 flex h-full min-h-0 w-full flex-col');
    expect(WORKSPACE_SOURCE).toContain("<PanelResizeHandle />");
  });

  it("does not pan the canvas or dismiss the panel while dragging", () => {
    // `data-no-pan` keeps the surface out of it; `stopPropagation` keeps the
    // press from being read as anything but a resize.
    expect(SOURCE).toContain("data-no-pan");
    expect(SOURCE).toContain("event.stopPropagation()");
    expect(SOURCE).toContain("setPointerCapture(event.pointerId)");
  });

  it("is reachable from the keyboard and announced as a separator", () => {
    expect(SOURCE).toContain('role="separator"');
    expect(SOURCE).toContain('aria-orientation="vertical"');
    expect(SOURCE).toContain("tabIndex={0}");
    expect(SOURCE).toContain('event.key === "ArrowLeft"');
    expect(SOURCE).toContain('event.key === "ArrowRight"');
    expect(CATALOGUE).toContain('"Resize panel": "Paneli yeniden boyutlandır"');
  });
});
