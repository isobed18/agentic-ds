import { describe, expect, it, vi } from "vitest";

import { CANVAS_PAN_BLOCKERS, isCanvasPanBlocked, releaseCanvasPointer } from "./canvasPan";

describe("canvas pan gesture ownership", () => {
  it("leaves panels, dialogs, controls, and explicitly protected overlays alone", () => {
    expect(CANVAS_PAN_BLOCKERS).toContain("aside");
    expect(CANVAS_PAN_BLOCKERS).toContain("[data-no-pan]");
    const closest = vi.fn(() => ({ tagName: "ASIDE" }));

    expect(isCanvasPanBlocked({ closest } as unknown as EventTarget)).toBe(true);
    expect(closest).toHaveBeenCalledWith(CANVAS_PAN_BLOCKERS);
  });

  it("allows empty canvas targets to begin a pan", () => {
    expect(isCanvasPanBlocked({ closest: () => null } as unknown as EventTarget)).toBe(false);
  });

  it("only releases pointer capture while the canvas still owns it", () => {
    const releasePointerCapture = vi.fn();
    releaseCanvasPointer(
      { hasPointerCapture: () => false, releasePointerCapture } as unknown as Element,
      7,
    );
    expect(releasePointerCapture).not.toHaveBeenCalled();

    releaseCanvasPointer(
      { hasPointerCapture: () => true, releasePointerCapture } as unknown as Element,
      7,
    );
    expect(releasePointerCapture).toHaveBeenCalledWith(7);
  });
});
