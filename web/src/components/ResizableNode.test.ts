import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ResizableNode } from "./ResizableNode";

Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

describe("a resizable node card", () => {
  it("offers a width, height, and corner handle, each with its own cursor", () => {
    const markup = renderToStaticMarkup(
      createElement(ResizableNode, { defaultWidth: 205, className: "relative", children: "card" }),
    );

    expect(markup).toContain("cursor-ew-resize");
    expect(markup).toContain("cursor-ns-resize");
    expect(markup).toContain("cursor-nwse-resize");
  });

  it("starts at its default width so the graph looks unchanged until dragged", () => {
    const markup = renderToStaticMarkup(
      createElement(ResizableNode, { defaultWidth: 205, className: "relative", children: "card" }),
    );

    expect(markup).toContain("width:205px");
  });
});
