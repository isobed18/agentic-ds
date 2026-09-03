import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactDialog, BranchNode, CanvasSurface, DockedPanel, Inspector, OutcomeNotice, RoutingDetails, SourceOverview, SourceSummary } from "./UnderstandingWorkspace";
import type { SourceProfile } from "../lib/api";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import GUIDED_SOURCE from "./GuidedPipeline.tsx?raw";
import AUTOMATION_SOURCE from "../pages/AutomationWorkspace.tsx?raw";
import REVIEW_SOURCE from "./DocumentTableReview.tsx?raw";
import CATALOGUE from "../lib/i18n.ts?raw";
import { CANVAS_BASE_WIDTH } from "./canvasZoom";
import { t } from "../lib/i18n";

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

    expect(markup).toContain("grid-template-columns:minmax(0, 1fr) min(27.5rem, 94vw)");
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
      children: createElement("div", null, "Canvas content"),
      overlay: createElement(Inspector, {
        title: "Uploaded files",
        eyebrow: "Source",
        onClose: () => undefined,
        children: createElement("p", null, "A very long list of files"),
      }),
    }));

    // The row can never exceed the surface, whatever a panel contains.
    expect(markup).toContain("grid-template-rows:minmax(0, 1fr)");
    expect(classNameFor(markup, "div")).toContain("overflow-hidden");
    // And each docked column may actually shrink to it: a grid item's automatic
    // minimum size is its content, so without this it overflows the clamped row.
    expect(classNameFor(markup, "aside").split(" ")).toContain("min-h-0");
    // #378: the planner is a page column now, and carries the same rule so it
    // shrinks to its own clamped row rather than growing the page.
    const planner = renderToStaticMarkup(createElement(DockedPanel, { children: createElement("p", null, "Planner content") }))
      .match(/<div data-docked-panel="true" class="([^"]+)"/)?.[1]?.split(" ") ?? [];
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
    // The regression this exists for (#48). The planner used to be an absolute
    // right-edge overlay above the inspector, so opening it swallowed the node
    // details instead of sharing the workspace.
    //
    // #378 moved the planner out of the canvas entirely: it is a page-level
    // column beside <main>, so it reaches every lifecycle state rather than
    // only this canvas. #48 now holds by construction -- the two panels live in
    // different grids and cannot overlay one another -- so what is left to pin
    // here is that the inspector still docks rather than floating, and that the
    // planner is a sibling of <main> rather than something drawn over it.
    const markup = renderToStaticMarkup(createElement(CanvasSurface, {
      docked: true,
      children: createElement("div", null, "Canvas content"),
      overlay: createElement(Inspector, {
        title: "Details",
        eyebrow: "Staging",
        onClose: () => undefined,
        children: createElement("p", null, "Inspector content"),
      }),
    }));

    expect(markup).toContain("grid-template-columns:minmax(0, 1fr) min(27.5rem, 94vw)");
    expect(markup).toContain("Inspector content");

    const panel = renderToStaticMarkup(createElement(DockedPanel, {
      children: createElement("p", null, "Planner content"),
    }));
    const classes = panel.match(/<div data-docked-panel="true" class="([^"]+)"/)?.[1]?.split(" ") ?? [];
    expect(classes).toEqual(expect.arrayContaining(["h-full", "w-full"]));
    expect(classes).not.toEqual(expect.arrayContaining(["absolute", "fixed"]));

    // The page puts it beside <main>, and the canvas no longer reserves a
    // column it cannot fill.
    expect(AUTOMATION_SOURCE).toContain("<DockedPanel><PlannerPanel");
    expect(AUTOMATION_SOURCE).toContain('<main className="min-h-0 min-w-0 flex-1">');
    expect(GUIDED_SOURCE).not.toContain("plannerDocked=");
    expect(WORKSPACE_SOURCE).not.toContain("plannerDocked?:");
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
    // #297: a column count carries the column unit, not the generic "öğe".
    expect(markup).toContain("6 sütun");
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

  it("offers no reuse-cache checkbox before running Intake (#314)", () => {
    // The cache it controlled is an in-memory dict on the plane, lost on every
    // server restart, so in the deployed product the box almost never had a
    // cache to hit. Its description also read as if it were on by default when
    // the state initialised to false.
    const profile = {
      source_id: "upload:abc",
      tables: [], documents: [], privacy: "safe",
      source_files: [
        { name: "a.csv", format: "csv", route: "structured", reason: { en: "", tr: "" }, table_names: [] },
      ],
    } as unknown as SourceProfile;

    const markup = renderToStaticMarkup(createElement(SourceSummary, {
      profile, onStart: () => undefined, busy: false,
    }));

    expect(markup).not.toContain('type="checkbox"');
    // The one control that does still act is untouched.
    expect(markup).toMatch(/Run Intake|Veri almayı çalıştır/);
    // The prop chain is gone with it, not merely unrendered.
    expect(WORKSPACE_SOURCE).not.toContain("onReuseCache");
    expect(WORKSPACE_SOURCE).not.toContain("reuseCache");
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

describe("promoted document tables in the Documents panel (#389)", () => {
  it("counts down the candidates a promotion settled instead of the immutable total", () => {
    // `table_candidates` is `len(item.tables)` on an immutable extraction
    // artifact, so gating the yellow box on it meant the box, its warning, and
    // its button survived every promotion unchanged. The same subtraction
    // GuidedPipeline has done since #361.
    expect(WORKSPACE_SOURCE).toContain("const promoted = workspace?.promoted_document_tables ?? []");
    expect(WORKSPACE_SOURCE).toContain("const candidateTables = Math.max(0, extractedCandidates - promoted.length)");
    expect(WORKSPACE_SOURCE).toContain("{candidateTables > 0 && <div className=\"rounded-xl border border-warn-200");
    expect(WORKSPACE_SOURCE).toContain('t("Review {count} extracted tables", { count: candidateTables })');
    // And nothing on this path still reads the raw candidate count.
    expect(WORKSPACE_SOURCE).not.toContain("(extraction?.table_candidates ?? 0) > 0");
  });

  it("re-reads the workspace once tables are promoted", () => {
    // `onPromoted={() => undefined}` meant not even the session that promoted
    // saw its own result: the panel behind the dialog stayed byte-for-byte
    // what it was before.
    expect(WORKSPACE_SOURCE).toContain("void api.stagingWorkspace(runId).then(onWorkspaceUpdated)");
    expect(WORKSPACE_SOURCE).toContain("onPromoted={reloadWorkspace}");
    expect(WORKSPACE_SOURCE).not.toContain("onPromoted={() => undefined}");
  });

  it("seeds the dialog from what was already promoted", () => {
    expect(WORKSPACE_SOURCE).toContain("promotedCandidateIds={promotedIds}");
    expect(GUIDED_SOURCE).toContain("promotedCandidateIds={promoted.map((table) => table.candidate_id)}");
  });

  it("leaves standing evidence of the promotion in the panel", () => {
    // The dialog's green panel goes away with the dialog. What was promoted
    // stays on the workspace, so the panel can show it.
    expect(WORKSPACE_SOURCE).toContain('t("{count} tables promoted into data", { count: promoted.length })');
    expect(WORKSPACE_SOURCE).toContain("{promoted.map((table) =>");
  });
});

describe("the outcome notice's action (#388)", () => {
  it("offers the extracted-tables dialog when there are candidates to choose", () => {
    // "Inspect understanding" selected the synthesis node, which is not the
    // action a person needs on this notice. The label and the target now match
    // the Documents panel's own entry point into the same dialog.
    expect(WORKSPACE_SOURCE).toContain("const canReviewTables = Boolean(runId && extraction?.artifact_id && tableCandidates > 0)");
    expect(WORKSPACE_SOURCE).toContain('canReviewTables ? t("Review {count} extracted tables", { count: tableCandidates }) : explainsDecision ? t("See the full reason") : t("Inspect understanding")');
    expect(WORKSPACE_SOURCE).toContain('canReviewTables ? setReviewing(true) : explainsDecision ? setSelection("proposal") : setSelection("synthesis")');
    // The notice renders for declined / deferred / no_plan alike, so the label
    // is a prop rather than a literal inside OutcomeNotice.
    expect(WORKSPACE_SOURCE).toContain("onClick={onInspect}>{actionLabel}</button>");
  });
});

describe("a deferred or declined decision can be read in full (#409)", () => {
  it("sends the notice's action to the panel that explains the decision", () => {
    // The banner shows `decision_summary` plus four rationale bullets and
    // nothing else. `PlanProposal`'s non-create branch already renders the
    // unsliced summary, the whole rationale list, and "Ask the Planner to
    // reconsider" -- and the action routed past it to the synthesis node.
    expect(WORKSPACE_SOURCE).toContain(
      'const explainsDecision = outcome?.kind === "deferred" || outcome?.kind === "declined"',
    );
    expect(WORKSPACE_SOURCE).toContain('explainsDecision ? setSelection("proposal")');
    // `no_plan` keeps the synthesis target: there is no plan record behind it
    // to open, which is exactly what that outcome means.
    expect(WORKSPACE_SOURCE).toContain(': setSelection("synthesis")');
  });

  it("keeps the table review as the primary action and the reason beside it", () => {
    // #388 gave the primary action to the table review when there are
    // candidates to choose. That still holds; the reason gets its own link
    // rather than displacing it.
    expect(WORKSPACE_SOURCE).toContain(
      'secondaryLabel={canReviewTables && explainsDecision ? t("See the full reason") : undefined}',
    );
    expect(WORKSPACE_SOURCE).toContain("{secondaryLabel && onSecondary && <button");
  });

  it("says when the rationale list is truncated", () => {
    // Four of nine reasons used to look like the whole account.
    expect(WORKSPACE_SOURCE).toContain("outcome.rationale.length > 4 &&");
    expect(WORKSPACE_SOURCE).toContain('t("+{count} more reasons", { count: outcome.rationale.length - 4 })');
  });

  it("translates both new strings", () => {
    expect(CATALOGUE).toContain('"See the full reason": "Gerekçenin tamamını gör"');
    expect(CATALOGUE).toContain('"+{count} more reasons": "+{count} gerekçe daha"');
  });
});

describe("closing a dialog that scrolls (#388)", () => {
  it("keeps the artifact dialog's × out of the scrolling area", () => {
    // The header used to sit inside the `overflow-y-auto` container, so with a
    // long artifact the only way to close the dialog was to scroll back up.
    const markup = renderToStaticMarkup(createElement(ArtifactDialog, {
      preview: { artifact_id: "c".repeat(64), artifact_type: "artifact", findings: [] },
      onClose: () => undefined,
    }));

    const panel = markup.match(/<div class="(flex max-h-\[86vh\][^"]*)"/);
    expect(panel, "the dialog panel should be present").not.toBeNull();
    // The panel itself no longer scrolls; it is a column.
    expect(panel?.[1]).toContain("flex-col");
    expect(panel?.[1]).not.toContain("overflow-y-auto");
    // The close button is above the scrolling body, not inside it.
    const closeIndex = markup.indexOf("btn-ghost");
    const bodyIndex = markup.indexOf("min-h-0 flex-1 overflow-y-auto");
    expect(closeIndex).toBeGreaterThan(-1);
    expect(bodyIndex).toBeGreaterThan(closeIndex);
  });

  it("does the same for the extracted-table review", () => {
    // Same structure, same bug: this one is the case in the report, because a
    // PDF with many candidates makes the list long by design.
    expect(REVIEW_SOURCE).toContain('className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-surface shadow-2xl"');
    expect(REVIEW_SOURCE).toContain('className="flex shrink-0 items-start gap-4 border-b border-line p-5"');
    expect(REVIEW_SOURCE).toContain('<div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">');
  });
});

describe("the Intake panel's per-file explanations (#386)", () => {
  it("shows only the header row until a file is expanded", () => {
    // Every card used to stack its insight, its reason, its table list and its
    // measured-from-content block unconditionally, so a panel with more than a
    // couple of files was a wall of prose to scroll past to find a file name.
    const markup = renderToStaticMarkup(createElement(RoutingDetails, {
      files: [{
        name: "orders.csv",
        format: "csv",
        route: "structured",
        reason: { en: "CSV, read as a table.", tr: "CSV, tablo olarak okunur." },
        tableNames: ["orders"],
        measuredFlow: "tablo",
        measuredEvidence: "Delimiter , over 12 columns",
      }],
    }));

    expect(markup).toContain("orders.csv");
    expect(markup).toContain('aria-expanded="false"');
    // The prose is gone, not merely restyled.
    expect(markup).not.toContain("tablo olarak okunur");
    expect(markup).not.toContain("Delimiter");
    expect(markup).not.toContain("orders</p>");
  });

  it("names the disclosure for anyone who cannot see the chevron", () => {
    const markup = renderToStaticMarkup(createElement(RoutingDetails, {
      files: [{ name: "orders.csv", format: "csv", route: "structured", tableNames: [] }],
    }));

    expect(markup).toContain("Dosya ayrıntılarını göster");
  });

  it("starts expanded when the measurement contradicts the extension", () => {
    // This is the reason a person opens the panel, so it may never end up
    // behind a collapsed card.
    const markup = renderToStaticMarkup(createElement(RoutingDetails, {
      files: [{
        name: "report.csv",
        format: "csv",
        route: "structured",
        tableNames: [],
        measuredFlow: "belge",
        contradictsExtension: true,
        measuredEvidence: "PDF text layer: 15 pages",
      }],
    }));

    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain("PDF text layer: 15 pages");
  });

  it("starts expanded when the measurement could not decide", () => {
    const markup = renderToStaticMarkup(createElement(RoutingDetails, {
      files: [{
        name: "dump.bin",
        format: "bin",
        route: "needs_review",
        tableNames: [],
        measuredFlow: "islenemez",
        measuredDeterministic: false,
        needsDecisionBecause: "no delimiter found",
      }],
    }));

    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain("no delimiter found");
  });
});

describe("dismissing the outcome notice (#384)", () => {
  const kinds = ["declined", "deferred", "no_plan"] as const;

  function notice(kind: (typeof kinds)[number]) {
    return renderToStaticMarkup(createElement(OutcomeNotice, {
      outcome: { kind, summary: { en: "No target survives.", tr: "Hedef kalmıyor." }, rationale: [] },
      files: ["a.csv"],
      actionLabel: "Inspect",
      onInspect: () => undefined,
      onDismiss: () => undefined,
    }));
  }

  it("gives every variant of the fixed banner an ×", () => {
    // All three are the same `fixed left-1/2 top-[72px]` overlay pinned over the
    // page, so all three need a way out -- not just the declined one.
    for (const kind of kinds) {
      const markup = notice(kind);
      expect(markup, kind).toContain(`aria-label="${t("Dismiss")}"`);
      expect(markup, kind).toContain("×");
    }
  });

  it("keeps the action button beside it", () => {
    // The × is an addition, not a replacement: "Inspect understanding" (or the
    // extracted-tables entry point from #388) still has to be reachable.
    expect(notice("declined")).toContain("Inspect");
  });

  it("dismisses locally, per outcome, without touching the run", () => {
    // A bare boolean would silence a later, different outcome too; the run and
    // the routing state are never written.
    expect(WORKSPACE_SOURCE).toContain('const [dismissed, setDismissed] = useState<StagingOutcome["kind"] | null>(null)');
    expect(WORKSPACE_SOURCE).toContain("routing.outcome.kind !== dismissed ? routing.outcome : null");
    expect(WORKSPACE_SOURCE).toContain("onDismiss={() => setDismissed(outcome.kind)}");
  });
});
