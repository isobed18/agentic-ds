import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactNodes } from "./ArtifactNodes";
import SOURCE from "./ArtifactNodes.tsx?raw";

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
    // The property that actually matters (#162): the column holding the list
    // has its top pinned to the node's edge and *no* height-proportional
    // transform. Asserting the class names alone let a change that recentred
    // the column on the edge pass -- `bottom-0 translate-y-1/2` and
    // `top-full -translate-y-1/2` place a wrapper identically, because the
    // percentage resolves against the wrapper's own height either way.
    const column = markup.match(/^<div class="([^"]+)"/)?.[1] ?? "";
    expect(column).toContain("top-full");
    expect(column).not.toContain("bottom-0");
    expect(column, "the wrapper containing the list must not scale with its height").not.toMatch(/translate-y/);
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

  it("shows a title this session already fetched without a second Loading pass", () => {
    // #368: a source pin, because the titles only exist inside the opened list
    // and opening it needs an effect this renderer does not run. The property
    // is that the title comes from a *synchronous* read of the session cache:
    // awaiting the cached promise instead would still render "Loading…" for a
    // frame on every return to the section, which is the symptom.
    expect(SOURCE).toContain("previews[id] ?? api.cachedArtifactPreview(id)");
  });

  it("renders nothing for a node that produced no artifacts", () => {
    expect(
      renderToStaticMarkup(createElement(ArtifactNodes, { ids: [], onOpen: () => undefined })),
    ).toBe("");
  });
});
