/**
 * The branch node's file chips at the width the node actually has (#186).
 */
import { describe, expect, it } from "vitest";

import UNDERSTANDING_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import { MIN_CHIPS, visibleFileChips } from "./branchNodeChips";

const DEFAULT_WIDTH = 290;
const MAX_WIDTH = 520;

/** Names of a realistic length, all the same, so only the count varies. */
const many = (count: number) => Array.from({ length: count }, (_, i) => `orders_${i}.csv`);

describe("how many file chips a branch node shows (#186)", () => {
  it("shows every name when there are few enough to not need cutting", () => {
    expect(visibleFileChips([], DEFAULT_WIDTH)).toBe(0);
    expect(visibleFileChips(many(1), DEFAULT_WIDTH)).toBe(1);
    expect(visibleFileChips(many(3), DEFAULT_WIDTH)).toBe(3);
  });

  it("never shows fewer than the flat three it replaced", () => {
    // The old cut was slice(0, 3) at any width. A narrow node must not lose
    // names it used to show; the chip row wraps, so three always have a place.
    for (const width of [120, 200, DEFAULT_WIDTH]) {
      expect(visibleFileChips(many(9), width)).toBeGreaterThanOrEqual(MIN_CHIPS);
    }
  });

  it("shows more names as the node is widened", () => {
    const atDefault = visibleFileChips(many(12), DEFAULT_WIDTH);
    const atMax = visibleFileChips(many(12), MAX_WIDTH);
    expect(atMax).toBeGreaterThan(atDefault);
  });

  it("grows monotonically -- widening a node never takes a name away", () => {
    const files = many(12);
    let previous = 0;
    for (let width = 120; width <= MAX_WIDTH; width += 20) {
      const shown = visibleFileChips(files, width);
      expect(shown, `${width}px shows fewer than a narrower node`).toBeGreaterThanOrEqual(previous);
      previous = shown;
    }
  });

  it("stops at the number of files there are, so +N disappears", () => {
    // The point of the issue: a wide node should stop saying "+N" about names
    // it now has room to print.
    expect(visibleFileChips(many(4), MAX_WIDTH)).toBe(4);
    expect(visibleFileChips(many(200), MAX_WIDTH)).toBeLessThan(200);
  });

  it("fits fewer long names than short ones at the same width", () => {
    const short = ["a.csv", "b.csv", "c.csv", "d.csv", "e.csv", "f.csv", "g.csv"];
    const long = short.map((name) => `quarterly_regional_${name}`);
    expect(visibleFileChips(short, MAX_WIDTH)).toBeGreaterThan(visibleFileChips(long, MAX_WIDTH));
  });
});

describe("the branch node's layout at a resized width", () => {
  it("cuts the chip row at the measured count rather than a flat three", () => {
    expect(UNDERSTANDING_SOURCE).toContain("files.slice(0, shown)");
    expect(UNDERSTANDING_SOURCE).toContain("files.length > shown");
    expect(UNDERSTANDING_SOURCE).not.toContain("files.slice(0, 3)");
  });

  it("takes its width from the node that is drawing it", () => {
    expect(UNDERSTANDING_SOURCE).toContain("visibleFileChips(files.map((file) => file.name), width)");
  });

  it("docks the substep column to the node's right edge", () => {
    // grid-cols-2 makes two 1fr columns, so the right-hand pair stays glued to
    // the 50% mark with a growing gap after it as the node widens. Content-
    // sized columns pushed apart keep the second one against the right edge.
    expect(UNDERSTANDING_SOURCE).toContain('grid grid-cols-[auto_auto] justify-between');
    expect(UNDERSTANDING_SOURCE).not.toContain('<ol className="mt-3 grid grid-cols-2');
  });
});
