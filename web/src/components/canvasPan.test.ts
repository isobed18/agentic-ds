import { describe, expect, it, vi } from "vitest";

import { CANVAS_PAN_BLOCKERS, isCanvasPanBlocked, releaseCanvasPointer } from "./canvasPan";
import GUIDED_SOURCE from "./GuidedPipeline.tsx?raw";

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

/**
 * The guided run toolbar's controls belong to the toolbar, not the canvas
 * (#189).
 *
 * "Approve at every stage" is a <label> wrapping a 14px checkbox, sitting in a
 * bar that is a DOM child of the pan surface. A press on the label's words --
 * anywhere but the small box -- began a pan, the viewport took pointer
 * capture, and the click that followed went there instead of to the checkbox.
 * The box only flipped when a press happened to land on the 14px target, which
 * is exactly what "I had to press several times, then could not switch it back
 * off" looks like.
 *
 * Two independent guards, because either alone would have fixed this one
 * checkbox and left the other half of the hole open.
 */
describe("controls inside a canvas keep their own presses (#189)", () => {
  it("treats a label as a control, not as canvas background", () => {
    // A whole selector, not a substring: "label" also occurs inside
    // "[aria-label]" and similar, and matching that would prove nothing.
    expect(CANVAS_PAN_BLOCKERS.split(",")).toContain("label");
  });

  it("excludes the whole guided run toolbar from the pan gesture", () => {
    // #197 moved the run control from the bottom edge to top-centre; it stays
    // marked data-no-pan so a drag on it never pans the canvas underneath.
    const opens = GUIDED_SOURCE.indexOf('<div data-no-pan className="fixed top-[4.375rem]');
    expect(opens, "the guided run toolbar is no longer marked data-no-pan").toBeGreaterThan(-1);

    // Everything up to the toolbar's own closing tag, at its indentation.
    const closes = GUIDED_SOURCE.indexOf(`
    </div>`, opens);
    const toolbar = GUIDED_SOURCE.slice(opens, closes);
    expect(toolbar).toContain('t("Approve at every stage")');
    expect(toolbar).toContain('type="checkbox"');
  });

  it("still lets the checkbox drive the run mode it is there to choose", () => {
    // The guard is only worth having because this control decides whether the
    // run stops at every gate.
    expect(GUIDED_SOURCE).toContain("setApproveEachStage(event.target.checked)");
    // #244/#198: the run mode now travels alongside the chosen target column.
    // #241: and the chosen problem kind, so a duplicate of this same invariant
    // in GuidedPipeline.test.ts and here both name the three-argument call.
    expect(GUIDED_SOURCE).toContain('onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind)');
  });
});
