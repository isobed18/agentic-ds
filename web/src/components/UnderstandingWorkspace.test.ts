import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BranchNode, CanvasSurface, DockedPanel, Inspector } from "./UnderstandingWorkspace";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";

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

  it("gives the planner and inspector separate dock columns", () => {
    // The regression this exists for (#48). The planner used to be an
    // absolute right-edge overlay above the inspector, so opening it swallowed
    // the node details instead of sharing the workspace.
    const markup = renderToStaticMarkup(createElement(CanvasSurface, {
      docked: true,
      plannerDocked: true,
      children: createElement("div", null, "Canvas content"),
      overlay: createElement(Fragment, null,
        createElement(Inspector, {
          title: "Details",
          eyebrow: "Staging",
          onClose: () => undefined,
          children: createElement("p", null, "Inspector content"),
        }),
        createElement(DockedPanel, {
          children: createElement("p", null, "Planner content"),
        }),
      ),
    }));

    expect(markup).toContain(
      "grid-template-columns:minmax(0, 1fr) minmax(0, min(440px, 47vw)) minmax(0, min(390px, 47vw))",
    );
    const planner = markup.match(/<div data-docked-panel="true" class="([^"]+)"/)?.[1]?.split(" ") ?? [];
    expect(planner).toEqual(expect.arrayContaining(["h-full", "w-full"]));
    expect(planner).not.toEqual(expect.arrayContaining(["absolute", "fixed"]));
    const proposalScreen = WORKSPACE_SOURCE.slice(
      WORKSPACE_SOURCE.indexOf("export function UnderstandingAndProposal"),
      WORKSPACE_SOURCE.indexOf("/** The fixed ML pipeline"),
    );
    expect(proposalScreen).toContain("plannerDocked={plannerOpen}");
    expect(proposalScreen).toContain("{plannerOpen && <DockedPanel><PlannerPanel");
  });
});

describe("understanding node status placement", () => {
  it("puts the branch status before its title, matching the other node cards", () => {
    // The regression this exists for (#47). BranchNode alone put its status
    // mark on the right; every other reachable node card starts on the left.
    const markup = renderToStaticMarkup(createElement(BranchNode, {
      title: "Structured data",
      files: [{ name: "orders.csv", format: "csv", route: "structured", tableNames: ["orders"] }],
      steps: [{ id: "profile", label: "Profile", status: "complete" }],
      artifactIds: [],
      onClick: () => undefined,
      onOpenArtifact: () => undefined,
    }));

    const statusMark = markup.indexOf("relative grid h-5 w-5");
    const title = markup.indexOf(">Structured data<");
    expect(statusMark).toBeGreaterThan(-1);
    expect(title).toBeGreaterThan(-1);
    expect(statusMark).toBeLessThan(title);
  });
});
