/**
 * Both canvases draw node status through one component (#195).
 *
 * The two pages had parallel components -- `StatusMark` and `StatusDot` -- with
 * different palettes, different labels and a pulse only one of them stopped for
 * `prefers-reduced-motion`. Asserting that neither page rolls its own again is
 * the point: two lists kept in sync by hand is how they drifted the first time.
 */
import { describe, expect, it } from "vitest";

import GUIDED_SOURCE from "./GuidedPipeline.tsx?raw";
import NODE_STATUS_SOURCE from "./NodeStatus.tsx?raw";
import UNDERSTANDING_SOURCE from "./UnderstandingWorkspace.tsx?raw";

describe("the shared node status presentation", () => {
  it("gives both canvas nodes the same header", () => {
    expect(GUIDED_SOURCE).toContain("<NodeStatusHeader status={status} />");
    expect(UNDERSTANDING_SOURCE).toContain("<NodeStatusHeader status={status} />");
  });

  it("leaves neither page a status mark of its own", () => {
    expect(GUIDED_SOURCE).not.toContain("function StatusDot");
    expect(UNDERSTANDING_SOURCE).not.toContain("function StatusMark");
    for (const source of [GUIDED_SOURCE, UNDERSTANDING_SOURCE]) {
      expect(source).not.toContain("border-ok-300 bg-ok-50 text-ok-700");
    }
  });

  it("never renders the lowercase mid-sentence label in a badge", () => {
    expect(GUIDED_SOURCE).not.toContain("<Badge tone={isSucceeded(status)");
    expect(GUIDED_SOURCE).not.toContain("{statusLabel(activeStatus)}");
    // StageRow's "status · elapsed" is genuinely mid-sentence and keeps it.
    expect(GUIDED_SOURCE).toContain("{statusLabel(node.status)}");
  });

  it("guards the working pulse for readers who asked for less motion", () => {
    // The understanding page did this and the ML pipeline did not; the shared
    // component is where that can stop being a per-page decision.
    expect(NODE_STATUS_SOURCE).toContain("motion-reduce:animate-none");
    expect(NODE_STATUS_SOURCE.match(/animate-ping/g)).toHaveLength(1);
  });
});
