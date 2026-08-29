import { describe, expect, it } from "vitest";

import { resizeFromEdge, type ResizeBounds } from "./nodeResize";

const bounds: ResizeBounds = { minWidth: 120, maxWidth: 400, minHeight: 60, maxHeight: 300 };
const start = { width: 200, height: 150 };

describe("resizing a node from an edge", () => {
  it("the right edge changes width only", () => {
    expect(resizeFromEdge("e", start, { dx: 40, dy: 99 }, bounds)).toEqual({ width: 240, height: 150 });
  });

  it("the bottom edge changes height only", () => {
    expect(resizeFromEdge("s", start, { dx: 99, dy: 30 }, bounds)).toEqual({ width: 200, height: 180 });
  });

  it("the corner changes both axes", () => {
    expect(resizeFromEdge("se", start, { dx: 25, dy: -20 }, bounds)).toEqual({ width: 225, height: 130 });
  });

  it("clamps so a node cannot collapse below its minimum", () => {
    expect(resizeFromEdge("se", start, { dx: -500, dy: -500 }, bounds)).toEqual({ width: 120, height: 60 });
  });

  it("clamps so one node cannot grow past its maximum and shove neighbours away", () => {
    expect(resizeFromEdge("se", start, { dx: 999, dy: 999 }, bounds)).toEqual({ width: 400, height: 300 });
  });
});
