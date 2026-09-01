import { describe, expect, it } from "vitest";

import {
  dismissAll,
  dismissOne,
  pruneDismissed,
  visibleItems,
} from "./notificationDismissal";

const item = (id: string) => ({ id });

describe("dismissing notifications", () => {
  it("hides a dismissed item and leaves the rest alone", () => {
    const items = [item("a"), item("b"), item("c")];
    expect(visibleItems(items, dismissOne(new Set(), "b")).map((i) => i.id)).toEqual(["a", "c"]);
  });

  it("keeps an item dismissed across a poll that re-derives it", () => {
    // The panel has no server-side delete: every poll rebuilds the same item
    // from the same run. Without remembered ids the row reappears immediately.
    const dismissed = dismissOne(new Set(), "run-1:done");
    const poll = [item("run-1:done"), item("run-2:done")];
    expect(visibleItems(poll, dismissed).map((i) => i.id)).toEqual(["run-2:done"]);
  });

  it("clears everything on screen at once", () => {
    const items = [item("a"), item("b")];
    expect(visibleItems(items, dismissAll(new Set(), items))).toEqual([]);
  });

  it("does not pre-dismiss an item that arrives after the clear", () => {
    // A question raised between the click and the next poll is the one
    // notification the user must not lose.
    const onScreen = [item("a"), item("b")];
    const dismissed = dismissAll(new Set(), onScreen);
    const nextPoll = [...onScreen, item("run-9:ask:plan:1")];
    expect(visibleItems(nextPoll, dismissed).map((i) => i.id)).toEqual(["run-9:ask:plan:1"]);
  });

  it("forgets ids the run list no longer produces", () => {
    const dismissed = new Set(["gone", "still-here"]);
    expect([...pruneDismissed(dismissed, [item("still-here")])]).toEqual(["still-here"]);
  });

  it("returns the same set when nothing was pruned, so polling does not rerender", () => {
    const dismissed = new Set(["a"]);
    expect(pruneDismissed(dismissed, [item("a"), item("b")])).toBe(dismissed);
  });
});
