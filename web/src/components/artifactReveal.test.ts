import { describe, expect, it } from "vitest";

import NODES_SOURCE from "./ArtifactNodes.tsx?raw";
import PIPELINE_SOURCE from "./GuidedPipeline.tsx?raw";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import { keepRevealed, nextToReveal, revealDelay, revealPending, uniqueIds } from "./artifactReveal";

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


describe("a duplicate id cannot stall the reveal (#446)", () => {
  it("settles on the full list instead of one short of it", () => {
    // Artifact ids are content-addressed, so a stage retried into identical
    // content emits the *same id*, and several id lists flatten across attempts
    // with no dedupe. A repeat can never be revealed -- `nextToReveal` skips
    // what is shown and the hook's own `includes` guard would refuse it -- so
    // `shown` settled at ["a"] against a two-long list, the "one more is
    // coming" placeholder rendered on every frame, and no timer was ever
    // scheduled again. Not slow: stopped.
    const reported = uniqueIds(["a", "a"]);
    let shown: string[] = [];
    for (let tick = 0; tick < 4; tick += 1) {
      const next = nextToReveal(shown, reported);
      if (next === undefined) break;
      shown = [...shown, next];
    }

    expect(shown).toEqual(["a"]);
    expect(revealPending(shown, reported)).toBe(false);
  });

  it("keeps the arrival order rather than a set's order", () => {
    // These lists are rendered in sequence, and the sequence is a record of
    // when work happened. Deduping must not reorder it.
    expect(uniqueIds(["c", "a", "c", "b", "a"])).toEqual(["c", "a", "b"]);
  });

  it("hands back the same array when there is nothing to remove", () => {
    // A fresh array every poll re-runs the reveal timer forever, which is the
    // failure `keepRevealed` already guards against for its own result.
    const ids = ["a", "b"];
    expect(uniqueIds(ids)).toBe(ids);
  });

  it("asks whether anything is left rather than comparing two lengths", () => {
    // The component compared `shown.length` against the *unfiltered* `ids`.
    // After #431 that is a second, independent stall: diagnostics are filtered
    // out of what is revealed, so a node holding one pulsed forever whenever
    // diagnostics were hidden -- no duplicate needed.
    expect(revealPending(["a"], ["a", "b"])).toBe(true);
    expect(revealPending(["a", "b"], ["a", "b"])).toBe(false);
    expect(revealPending(["a"], ["a"])).toBe(false);
    expect(NODES_SOURCE).toContain("revealPending(shown, visibleIds)");
    expect(NODES_SOURCE).not.toContain("shown.length < ids.length");
  });

  it("dedupes inside the hook, not only at its callers", () => {
    // Every caller is cleaned up below, but a rendering artifact that outlives
    // its data is worse than a duplicate row -- the hook has to be unable to
    // stall on whatever it is handed.
    expect(NODES_SOURCE).toContain("const reported = useMemo(() => uniqueIds(ids), [key])");
    expect(NODES_SOURCE).toContain("nextToReveal(shown, reported)");
  });

  it("counts what the node holds, not how far the reveal has got", () => {
    // A stalled list reported one fewer artifact than the node had, for good.
    expect(NODES_SOURCE).toContain('t("Artifacts ({count})", { count: visibleIds.length })');
  });

  it("stops the placeholder pretending to be an artifact", () => {
    // It borrowed the numbered circle's geometry and sat inside the ordered
    // list, so even while it was behaving correctly for a second it read as an
    // artifact that had failed to load rather than as "more are coming".
    expect(NODES_SOURCE).not.toContain(
      '<span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-dashed border-line">',
    );
    expect(NODES_SOURCE).toContain('className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-300"');
  });

  it("also dedupes where the lists are concatenated", () => {
    // Fixing only the hook would leave the ids themselves wrong: the pill count
    // and every reader of these lists would still double-count an artifact.
    expect(WORKSPACE_SOURCE).toContain("const measuredIds = uniqueIds([");
    expect(WORKSPACE_SOURCE).toContain('uniqueIds([...measuredIds, ...outputIds(["structured-brief"])])');
    // `artifactIdsByStage` already deduped within one stage; this flattens
    // across the stages of a group, so the guard stopped one level short.
    expect(PIPELINE_SOURCE).toContain(
      "const groupArtifactIds = uniqueIds(group.stages.flatMap((stage) => artifactIdsByStage.get(stage) ?? []));",
    );
  });
});
