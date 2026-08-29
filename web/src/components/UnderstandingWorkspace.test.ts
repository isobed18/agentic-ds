import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CanvasSurface, Inspector } from "./UnderstandingWorkspace";

Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

function classNameFor(markup: string, element: string): string {
  const match = markup.match(new RegExp(`<${element}[^>]*class="([^"]+)"`));
  expect(match, `${element} should be present`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("the understanding inspector layout", () => {
  it("docks the inspector beside a canvas that gives up its width", () => {
    // The regression this file exists for (#40). The inspector used to be
    // fixed over a full-width canvas, leaving the right-hand graph nodes
    // unreachable underneath it even after panning.
    const markup = renderToStaticMarkup(createElement(
      CanvasSurface,
      {
        docked: true,
        children: createElement("div", null, "Canvas content"),
        overlay: createElement(Inspector, {
          title: "Details",
          eyebrow: "Staging",
          onClose: () => undefined,
          children: createElement("p", null, "Inspector content"),
        }),
      },
    ));

    expect(markup).toContain("grid-template-columns:minmax(0, 1fr) min(440px, 94vw)");
    expect(classNameFor(markup, "aside").split(" ")).toEqual(expect.arrayContaining(["h-full", "w-full"]));
    expect(classNameFor(markup, "aside").split(" ")).not.toContain("fixed");
  });
});
