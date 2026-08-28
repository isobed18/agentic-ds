import { describe, expect, it } from "vitest";

import {
  CANVAS_BASE_HEIGHT,
  CANVAS_BASE_WIDTH,
  CANVAS_MAX_ZOOM,
  CANVAS_MIN_ZOOM,
  clampZoom,
  isZoomGesture,
  scaledBox,
  steppedZoom,
} from "./canvasZoom";

describe("zoom bounds", () => {
  it("holds at the floor no matter how many times you click out", () => {
    let zoom = 1;
    for (let click = 0; click < 12; click += 1) zoom = steppedZoom(zoom, -1);
    expect(zoom).toBe(CANVAS_MIN_ZOOM);
  });

  it("holds at the ceiling no matter how many times you click in", () => {
    let zoom = 1;
    for (let click = 0; click < 12; click += 1) zoom = steppedZoom(zoom, 1);
    expect(zoom).toBe(CANVAS_MAX_ZOOM);
  });

  it("returns to exactly 1 after stepping out and back", () => {
    // Drift here shows up as a control that never re-enables at 100%.
    expect(steppedZoom(steppedZoom(1, -1), 1)).toBeCloseTo(1, 10);
  });

  it("survives a non-finite value rather than blanking the canvas", () => {
    // A NaN scale renders nothing at all, with no error to explain it.
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(Number.POSITIVE_INFINITY)).toBe(CANVAS_MAX_ZOOM);
  });
});

describe("the scrollable box", () => {
  it("grows with the content", () => {
    // Left at the unzoomed size, zooming in crops the graph at the old bounds
    // and the right-hand nodes become unreachable.
    const box = scaledBox(1.5);
    expect(box.width).toBe(CANVAS_BASE_WIDTH * 1.5);
    expect(box.height).toBe(CANVAS_BASE_HEIGHT * 1.5);
  });

  it("shrinks when zoomed out, so the graph is not stranded in whitespace", () => {
    expect(scaledBox(0.5).width).toBeLessThan(CANVAS_BASE_WIDTH);
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
