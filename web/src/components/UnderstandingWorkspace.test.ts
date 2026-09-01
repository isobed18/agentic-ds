import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactDialog, BranchNode, CanvasSurface, DockedPanel, Inspector, RoutingDetails, SourceOverview } from "./UnderstandingWorkspace";
import type { SourceProfile } from "../lib/api";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import GUIDED_SOURCE from "./GuidedPipeline.tsx?raw";
import { CANVAS_BASE_WIDTH } from "./canvasZoom";

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

  it("keeps the inspector header docked while only the body scrolls (#75)", () => {
    // overflow-y-auto used to sit on the <aside> itself, so a long panel
    // scrolled its title and × out of view with no way to close it.
    const markup = renderToStaticMarkup(createElement(Inspector, {
      title: "Details",
      eyebrow: "Staging",
      onClose: () => undefined,
      children: createElement("p", null, "SCROLL_BODY"),
    }));

    const asideClass = classNameFor(markup, "aside");
    expect(asideClass).not.toContain("overflow-y-auto");
    expect(asideClass).toContain("flex-col");
    // The header (bordered, pinned) sits before the scrolling container, so its
    // close button stays put; the body content lives inside that container.
    const headerIndex = classNameFor(markup, "header").length ? markup.indexOf("<header") : -1;
    const scrollIndex = markup.indexOf("overflow-y-auto");
    expect(headerIndex).toBeGreaterThan(-1);
    expect(scrollIndex).toBeGreaterThan(headerIndex);
    expect(markup.indexOf("SCROLL_BODY")).toBeGreaterThan(scrollIndex);
  });

  it("clamps the canvas row so a tall panel scrolls instead of growing (#161)", () => {
    // The reason closing and reopening the panel never helped: this is layout,
    // not state. The grid's single implicit row is `auto`, so it grew to the
    // inspector's full content height, the aside's `h-full` resolved to the
    // grown row, and its `overflow-y-auto` body was never shorter than its
    // content -- no scrollbar, no wheel movement, bottom unreachable.
    const markup = renderToStaticMarkup(createElement(CanvasSurface, {
      docked: true,
      plannerDocked: true,
      children: createElement("div", null, "Canvas content"),
      overlay: createElement(Fragment, null,
        createElement(Inspector, {
          title: "Uploaded files",
          eyebrow: "Source",
          onClose: () => undefined,
          children: createElement("p", null, "A very long list of files"),
        }),
        createElement(DockedPanel, { children: createElement("p", null, "Planner content") }),
      ),
    }));

    // The row can never exceed the surface, whatever a panel contains.
    expect(markup).toContain("grid-template-rows:minmax(0, 1fr)");
    expect(classNameFor(markup, "div")).toContain("overflow-hidden");
    // And each docked column may actually shrink to it: a grid item's automatic
    // minimum size is its content, so without this it overflows the clamped row.
    expect(classNameFor(markup, "aside").split(" ")).toContain("min-h-0");
    const planner = markup.match(/<div data-docked-panel="true" class="([^"]+)"/)?.[1]?.split(" ") ?? [];
    expect(planner).toContain("min-h-0");
  });

  it("sizes the content box to the graph instead of clipping it (#203)", () => {
    // The "Yüklenen dosyalar" node sat off-screen past the left edge, under the
    // collapsible menu, and neither collapsing the menu nor panning nor zooming
    // could reach it. It was not behind the sidebar: the graph row (min 1430px,
    // with resizable nodes and connectors that grow per branch) outgrew the
    // fixed 1500px box it was centred in, and `justify-center` pushed half the
    // overflow to a negative offset that `scrollLeft` cannot reach.
    const markup = renderToStaticMarkup(createElement(CanvasSurface, {
      children: createElement("div", { className: "flex min-w-[1430px]" }, "Canvas content"),
    }));

    // A floor, not a fixed size, so the box always contains the graph. Read
    // off the content box itself: the scrolling wrapper around it is still
    // sized in pixels, which is the whole point of `scaledBox`.
    const content = markup.match(/<div class="flex items-center justify-center" style="([^"]+)"/)?.[1];
    expect(content, "the content box should be present").toBeTruthy();
    expect(content).toContain("width:max-content");
    expect(content).toContain(`min-width:${CANVAS_BASE_WIDTH}px`);
    // And it is no longer pinned to that width, which is what clipped it.
    expect(content).not.toMatch(new RegExp(`(^|;)width:${CANVAS_BASE_WIDTH}px`));
    // Centring stays -- it is correct once the box cannot be narrower than its
    // content, and it is what centres a small graph in a large canvas.
    expect(markup).toContain("justify-center");
  });

  it("draws the accepted pipeline on the same canvas, not a second one (#203/#214)", () => {
    // There used to be a second canvas here -- `PanCanvas` -- centring its
    // children inside a block-level container that stopped at the viewport, so
    // the first node was exposed to the identical clipping once the row grew.
    // It also had no zoom, and #214 concatenates the two rows into one roughly
    // twice as wide. Both halves now share `CanvasSurface`, which sizes its box
    // to the graph and zooms, so there is one fix rather than two to keep.
    expect(GUIDED_SOURCE).not.toContain("function PanCanvas");
    expect(GUIDED_SOURCE).toContain("<CanvasSurface");
    expect(GUIDED_SOURCE).toContain("<RoutingGraph");
    // And there is no second canvas left behind here to drift from it: the
    // proposal is a state of the merged canvas, not a screen of its own.
    expect(WORKSPACE_SOURCE).not.toContain("export function UnderstandingAndProposal");
    expect(WORKSPACE_SOURCE).toContain("export function RoutingGraph");
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
    // #214 moved the screen that docks both of these -- the proposal -- onto
    // the merged canvas, which keeps the planner docked in its own column for
    // the run as well as for staging.
    const proposalScreen = GUIDED_SOURCE.slice(GUIDED_SOURCE.indexOf("export function GuidedPipeline"));
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

  it("shows a fallback instead of a blank body for an empty artifact (#74)", () => {
    // An artifact with no summary, no findings and a non-document type used to
    // render a literally blank dialog body under a generic "ARTIFACT" eyebrow.
    const markup = renderToStaticMarkup(createElement(ArtifactDialog, {
      preview: { artifact_id: "a".repeat(64), artifact_type: "artifact", findings: [] },
      onClose: () => undefined,
    }));

    expect(markup).toContain("Artifact kaydedildi");
  });

  it("does not show the fallback when the artifact has findings (#74)", () => {
    const markup = renderToStaticMarkup(createElement(ArtifactDialog, {
      preview: {
        artifact_id: "b".repeat(64),
        artifact_type: "schema_discovery",
        findings: [{ en: "Two candidate keys", tr: "İki aday anahtar" }],
      },
      onClose: () => undefined,
    }));

    expect(markup).not.toContain("Artifact kaydedildi");
    expect(markup).toContain("İki aday anahtar");
  });

  it("shows safe generic artifact fields instead of the empty fallback (#164)", () => {
    const markup = renderToStaticMarkup(createElement(ArtifactDialog, {
      preview: {
        artifact_id: "c".repeat(64),
        artifact_type: "data_card",
        fields: { table_name: "customers", n_rows: 24 },
        collection_sizes: { columns: 6 },
      },
      onClose: () => undefined,
    }));

    expect(markup).toContain("Veri kartı");
    expect(markup).toContain("customers");
    expect(markup).toContain("24");
    expect(markup).toContain("6 öğe");
    expect(markup).not.toContain("Artifact kaydedildi");
  });

  it("lets a person review, remove and add files before running (#85)", () => {
    const profile = {
      source_id: "upload:abc",
      tables: [], documents: [], privacy: "safe",
      source_files: [
        { name: "a.csv", format: "csv", route: "structured", reason: { en: "", tr: "" }, table_names: [] },
        { name: "b.pdf", format: "pdf", route: "documents", reason: { en: "", tr: "" }, table_names: [] },
      ],
    } as unknown as SourceProfile;

    const editable = renderToStaticMarkup(createElement(SourceOverview, {
      profile, onRemoveFile: () => undefined, onAddFiles: () => undefined,
    }));
    expect(editable).toContain("a.csv");
    expect(editable).toContain("b.pdf");
    expect(editable).toContain("Dosyayı kaldır"); // per-file remove control
    expect(editable).toContain("Dosya ekle"); // add-files control
    // One remove button per file.
    expect((editable.match(/aria-label="Dosyayı kaldır"/g) ?? []).length).toBe(2);

    const readOnly = renderToStaticMarkup(createElement(SourceOverview, { profile }));
    expect(readOnly).toContain("a.csv");
    expect(readOnly).not.toContain("Dosyayı kaldır");
    expect(readOnly).not.toContain("Dosya ekle");
  });

  it("lets a node be resized from its edges (#67)", () => {
    // The node had a hardcoded width and no resize affordance at all. Every
    // node type now carries the shared edge/corner handles.
    const markup = renderToStaticMarkup(createElement(BranchNode, {
      title: "Structured data",
      files: [],
      steps: [],
      artifactIds: [],
      onClick: () => undefined,
      onOpenArtifact: () => undefined,
    }));

    expect(markup).toContain("cursor-ew-resize");
    expect(markup).toContain("cursor-ns-resize");
    expect(markup).toContain("cursor-nwse-resize");
  });

  it("draws the measured analysis charts when an artifact carries panels (#304)", () => {
    // Opening the EDA artifact showed text and scalars; the distributions,
    // missingness and correlation heatmap the backend measured are rendered
    // here through the same AnalysisStrip the stage inspector uses.
    expect(WORKSPACE_SOURCE).toContain('import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip"');
    expect(WORKSPACE_SOURCE).toContain("const panels = (preview.panels ?? []) as AnalysisPanel[]");
    expect(WORKSPACE_SOURCE).toContain("panels.length > 0 && <div className=\"mt-4\"><AnalysisStrip panels={panels} />");
    // An all-charts artifact must not be judged empty and hidden behind the
    // "Artifact recorded" fallback.
    expect(WORKSPACE_SOURCE).toContain("preview.findings?.length || panels.length || hasArtifactMetadata(preview)");
  });

  it("clips long content inside a resized node card (#160)", () => {
    // Resizing narrower used to let the title and secondary text paint outside
    // the rounded card and over adjacent edges.
    const markup = renderToStaticMarkup(createElement(BranchNode, {
      title: "A very long structured-data title that must stay inside its card",
      files: [],
      steps: [],
      artifactIds: [],
      onClick: () => undefined,
      onOpenArtifact: () => undefined,
    }));

    expect(classNameFor(markup, "button")).toContain("overflow-hidden");
  });

  it("keeps the page-size label on one line whatever the language calls it (#293)", () => {
    // "per page" is one short word in English and two in Turkish -- "sayfa
    // başına". The label sits in a 28px-tall flex row, so the Turkish pair
    // broke across two lines and read as clipped text. The parent already
    // wraps, so the label moves to its own line rather than splitting.
    const markup = renderToStaticMarkup(createElement(RoutingDetails, {
      files: [{ name: "orders.csv", format: "csv", route: "structured", tableNames: [] }],
    }));

    expect(classNameFor(markup, "label")).toContain("whitespace-nowrap");
  });
});
