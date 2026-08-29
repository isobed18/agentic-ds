import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactNodes } from "./ArtifactNodes";

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
    // Straddles the node's bottom edge, the redesign's defining placement (#66).
    expect(markup).toContain("translate-y-1/2");
    // Collapsed: the dashed numbered list is not in the DOM until opened.
    expect(markup).not.toContain("<ol");
  });

  it("renders nothing for a node that produced no artifacts", () => {
    expect(
      renderToStaticMarkup(createElement(ArtifactNodes, { ids: [], onOpen: () => undefined })),
    ).toBe("");
  });
});
