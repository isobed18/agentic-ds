import { describe, expect, it } from "vitest";

import { keepRevealed, nextToReveal, revealDelay } from "./artifactReveal";

describe("revealing artifacts one at a time", () => {
  it("reveals in the order the run reports them", () => {
    expect(nextToReveal([], ["a", "b", "c"])).toBe("a");
    expect(nextToReveal(["a"], ["a", "b", "c"])).toBe("b");
    expect(nextToReveal(["a", "b"], ["a", "b", "c"])).toBe("c");
  });

  it("stops once everything reported is on screen", () => {
    expect(nextToReveal(["a", "b"], ["a", "b"])).toBeUndefined();
  });

  it("reveals one per tick even when a poll brings three at once", () => {
    // The runner writes several artifacts inside one 1.5s poll. Without this
    // they land in the same frame and the graph reads as a refresh rather than
    // as work happening, which is the only reason to watch a live run.
    const reported = ["a", "b", "c"];
    let shown: string[] = [];
    const order: string[] = [];
    for (let tick = 0; tick < 3; tick += 1) {
      const next = nextToReveal(shown, reported);
      expect(next).toBeDefined();
      order.push(next as string);
      shown = [...shown, next as string];
    }
    expect(order).toEqual(["a", "b", "c"]);
    expect(nextToReveal(shown, reported)).toBeUndefined();
  });

  it("shows the first one immediately and spaces the rest", () => {
    // Opening a run that finished hours ago should not replay a stagger.
    expect(revealDelay(0, 420)).toBe(0);
    expect(revealDelay(1, 420)).toBe(420);
    expect(revealDelay(9, 420)).toBe(420);
  });
});

describe("keeping what is already on screen", () => {
  it("drops artifacts the run no longer reports", () => {
    // A retry discards artifacts, so the reported set can shrink.
    expect(keepRevealed(["a", "b", "c"], ["a", "c"])).toEqual(["a", "c"]);
  });

  it("never reorders what the viewer already watched arrive", () => {
    // The sequence is a record of when work happened. Sorting it to match a
    // later poll would make earlier work look like it happened later.
    expect(keepRevealed(["c", "a", "b"], ["a", "b", "c"])).toEqual(["c", "a", "b"]);
  });

  it("returns the same array when nothing was dropped", () => {
    // Identity matters: a fresh array every poll re-runs the reveal timer, and
    // the stagger would restart every 1.5 seconds forever.
    const current = ["a", "b"];
    expect(keepRevealed(current, ["a", "b", "c"])).toBe(current);
  });

  it("empties out when the run reports nothing", () => {
    expect(keepRevealed(["a"], [])).toEqual([]);
  });

  it("does not re-add something that was dropped and reported again", () => {
    // It reappears through the normal reveal path, not by resurrection here.
    const afterRetry = keepRevealed(["a", "b"], ["a"]);
    expect(afterRetry).toEqual(["a"]);
    expect(nextToReveal(afterRetry, ["a", "b"])).toBe("b");
  });
});
