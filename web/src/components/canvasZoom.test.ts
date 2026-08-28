import { describe, expect, it } from "vitest";

import {
  CANVAS_BASE_HEIGHT,
  CANVAS_BASE_WIDTH,
  CANVAS_MAX_STEP,
  CANVAS_MIN_STEP,
  clampStep,
  isZoomGesture,
  scaledBox,
  stepForZoom,
  zoomForStep,
  zoomPercent,
} from "./canvasZoom";

describe("zooming in and back out", () => {
  it("returns to exactly 100% after equal steps each way", () => {
    // The regression this file exists for (#57). Holding zoom as a float and
    // multiplying by 1.2 looks like an inverse and is not one: once a step
    // saturates at the limit the multiplication is lost, and three scrolls in
    // followed by three out landed at 92.6%.
    let step = 0;
    for (let i = 0; i < 3; i += 1) step = clampStep(step + 1);
    for (let i = 0; i < 3; i += 1) step = clampStep(step - 1);
    expect(step).toBe(0);
    expect(zoomPercent(step)).toBe(100);
  });

  it("comes back to 100% even after pushing well past the ceiling", () => {
    // Going one past the end has to cost nothing, or the trip back is short.
    let step = 0;
    for (let i = 0; i < 20; i += 1) step = clampStep(step + 1);
    expect(step).toBe(CANVAS_MAX_STEP);
    for (let i = 0; i < 20; i += 1) step = clampStep(step - 1);
    expect(step).toBe(CANVAS_MIN_STEP);
  });

  it("puts the default at exactly 1, not a rounded approximation of it", () => {
    expect(zoomForStep(0)).toBe(1);
    expect(zoomPercent(0)).toBe(100);
  });

  it("holds at each limit", () => {
    expect(clampStep(CANVAS_MAX_STEP + 5)).toBe(CANVAS_MAX_STEP);
    expect(clampStep(CANVAS_MIN_STEP - 5)).toBe(CANVAS_MIN_STEP);
  });

  it("survives a non-finite step rather than blanking the canvas", () => {
    // A NaN scale renders nothing at all, with no error to explain it.
    expect(clampStep(Number.NaN)).toBe(0);
    expect(zoomForStep(Number.NaN)).toBe(1);
  });
});

describe("choosing a level directly", () => {
  it("maps a scale back to the nearest step", () => {
    expect(stepForZoom(zoomForStep(2))).toBe(2);
    expect(stepForZoom(zoomForStep(-3))).toBe(-3);
  });

  it("clamps a level outside the range instead of inventing one", () => {
    expect(stepForZoom(100)).toBe(CANVAS_MAX_STEP);
    expect(stepForZoom(0.001)).toBe(CANVAS_MIN_STEP);
  });

  it("treats a nonsense level as the default", () => {
    expect(stepForZoom(0)).toBe(0);
    expect(stepForZoom(-1)).toBe(0);
  });

  it("reports one rounded percentage, so label and transform cannot disagree", () => {
    for (let step = CANVAS_MIN_STEP; step <= CANVAS_MAX_STEP; step += 1) {
      expect(zoomPercent(step)).toBe(Math.round(zoomForStep(step) * 100));
    }
  });
});

describe("the scrollable box", () => {
  it("grows with the content", () => {
    // Left at the unzoomed size, zooming in crops the graph at the old bounds
    // and the right-hand nodes become unreachable.
    const box = scaledBox(2);
    expect(box.width).toBeCloseTo(CANVAS_BASE_WIDTH * zoomForStep(2), 6);
    expect(box.height).toBeCloseTo(CANVAS_BASE_HEIGHT * zoomForStep(2), 6);
  });

  it("shrinks when zoomed out, so the graph is not stranded in whitespace", () => {
    expect(scaledBox(-3).width).toBeLessThan(CANVAS_BASE_WIDTH);
  });

  it("is exactly the base box at the default", () => {
    expect(scaledBox(0)).toEqual({ width: CANVAS_BASE_WIDTH, height: CANVAS_BASE_HEIGHT });
  });
});

describe("telling zoom from scroll", () => {
  it("treats ctrl and cmd as zoom", () => {
    expect(isZoomGesture({ ctrlKey: true, metaKey: false })).toBe(true);
    expect(isZoomGesture({ ctrlKey: false, metaKey: true })).toBe(true);
  });

  it("leaves a plain wheel alone", () => {
    // Two-finger trackpad panning is how this canvas has always worked;
    // capturing it would take away a gesture people already have.
    expect(isZoomGesture({ ctrlKey: false, metaKey: false })).toBe(false);
  });
});
