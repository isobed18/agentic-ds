import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactNodes } from "./ArtifactNodes";
import SOURCE from "./ArtifactNodes.tsx?raw";
import GUIDED from "./GuidedPipeline.tsx?raw";
import UNDERSTANDING from "./UnderstandingWorkspace.tsx?raw";

// Force the default (English) catalogue so the opener text is predictable.
Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

describe("the artifacts opener under a node", () => {
  it("renders one straddling opener, collapsed, instead of always-on chips", () => {
    const markup = renderToStaticMarkup(
      createElement(ArtifactNodes, { ids: ["a", "b", "c"], onOpen: () => undefined }),
    );

    // A single toggle, closed by default — not a row of visible artifact chips.
    expect(markup).toContain('aria-expanded="false"');
    // The parenthesised count format the redesign asks for, in any language.
    expect(markup).toMatch(/\(\d+\)/);
    // #327: the collection participates in layout. An absolute list looked
    // attached to the node, but its wrapper contributed zero height and the
    // next node in a vertical branch stayed directly underneath it.
    const column = markup.match(/^<div class="([^"]+)"/)?.[1] ?? "";
    expect(column).toContain("relative");
    expect(column).not.toContain("absolute");
    expect(column).not.toContain("top-full");
    // The straddle from #66 survives, as a fixed offset on the pill alone: its
    // wrapper closes before the list starts, so the list cannot move it.
    const straddle = SOURCE.indexOf('className="-translate-y-1/2"');
    const list = SOURCE.indexOf("<ol");
    expect(straddle).toBeGreaterThan(-1);
    expect(list).toBeGreaterThan(straddle);
    expect(SOURCE.slice(straddle, list)).toContain("</div>");
    // Collapsed: the dashed numbered list is not in the DOM until opened.
    expect(markup).not.toContain("<ol");
  });

  it("renders nothing for a node that produced no artifacts", () => {
    expect(
      renderToStaticMarkup(createElement(ArtifactNodes, { ids: [], onOpen: () => undefined })),
    ).toBe("");
  });

  it("marks the artifact that owns the open preview", () => {
    expect(SOURCE).toContain("activeId?: string | null");
    expect(SOURCE).toContain('aria-current={active ? "true" : undefined}');
    expect(SOURCE).toContain('active && "ring-2 ring-brand-400"');
    expect(GUIDED).toContain("activeArtifactId={preview?.artifact_id ?? null}");
    expect(UNDERSTANDING).toContain("activeArtifactId={preview?.artifact_id ?? null}");
  });
});
