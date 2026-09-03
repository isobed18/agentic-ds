import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { ArtifactNodes } from "./ArtifactNodes";
import { setShowDiagnostics } from "../lib/diagnostics";
import SOURCE from "./ArtifactNodes.tsx?raw";
import GUIDED from "./GuidedPipeline.tsx?raw";
import UNDERSTANDING from "./UnderstandingWorkspace.tsx?raw";

// Force the default (English) catalogue so the opener text is predictable.
Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

afterEach(() => setShowDiagnostics(false));

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

  it("marks the artifact that owns the open preview", () => {
    expect(SOURCE).toContain("activeId?: string | null");
    expect(SOURCE).toContain('aria-current={active ? "true" : undefined}');
    expect(SOURCE).toContain('active && "ring-2 ring-brand-400"');
    expect(GUIDED).toContain("activeArtifactId={preview?.artifact_id ?? null}");
    expect(UNDERSTANDING).toContain("activeArtifactId={preview?.artifact_id ?? null}");
  });
});

describe("the list reveals what it just gained (#408)", () => {
  it("opens itself when the preference turns diagnostics on", () => {
    setShowDiagnostics(true);
    const markup = renderToStaticMarkup(
      createElement(ArtifactNodes, {
        ids: ["a", "b"],
        diagnosticIds: new Set(["b"]),
        onOpen: () => undefined,
      }),
    );

    // The effect that opens it runs on mount, so static markup still shows the
    // collapsed pill -- what matters here is that the wiring exists and that
    // the viewer's own click takes the pill back off the caller.
    expect(markup).toContain("aria-expanded");
    expect(SOURCE).toContain("const revealed = showDiagnostics && ids.some((id) => diagnosticIds?.has(id) ?? false)");
    expect(SOURCE).toContain("if (revealed) { auto.current = true; setOpen(true); }");
    expect(SOURCE).toContain("else if (auto.current) { auto.current = false; setOpen(false); }");
  });

  it("leaves a list with no diagnostic in it alone", () => {
    setShowDiagnostics(true);
    // `some` rather than a bare preference read: a card that gained nothing
    // must not pop open because a card elsewhere on the canvas did.
    expect(SOURCE).toContain("ids.some((id) => diagnosticIds?.has(id) ?? false)");
  });

  it("hands the pill back to whoever clicks it", () => {
    // Clicking clears the auto flag, so turning the toggle off later does not
    // close a list the viewer opened for themselves.
    expect(SOURCE).toContain("onClick={() => { auto.current = false; setOpen((current) => !current); }}");
  });

  it("labels the rows that are diagnostics", () => {
    setShowDiagnostics(true);
    const markup = renderToStaticMarkup(
      createElement(ArtifactNodes, {
        ids: ["a"],
        diagnosticIds: new Set(["a"]),
        onOpen: () => undefined,
      }),
    );

    // Collapsed on first render, so the marker is checked at the source: the
    // row is dashed and captioned rather than silently lengthening the list.
    expect(markup).toContain("aria-expanded");
    expect(SOURCE).toContain("const diagnostic = diagnosticIds?.has(id) ?? false");
    expect(SOURCE).toContain('t("Diagnostic")');
    expect(SOURCE).toContain('diagnostic ? "border-dashed border-slate-300" : "border-line"');
  });
});

describe("the node hides diagnostics itself (#424)", () => {
  it("drops them from the list while the preference is off", () => {
    // #305 filtered in one caller, so every other artifact list in the app --
    // the stage inspector, the staging graph, the advanced editor -- listed
    // diagnostics whatever the toggle said. Every canvas list comes through
    // here, so the filter does too: give it the run's diagnostic ids and the
    // node cannot leak one into the default view.
    //
    // A node holding nothing but diagnostics is the observable case: it
    // disappears rather than keeping an opener onto an empty list. The guard
    // used to be on `ids`, which counted the hidden ones.
    expect(
      renderToStaticMarkup(
        createElement(ArtifactNodes, {
          ids: ["audit"],
          diagnosticIds: new Set(["audit"]),
          onOpen: () => undefined,
        }),
      ),
    ).toBe("");
    expect(SOURCE).toContain("showDiagnostics || !diagnosticIds ? ids : ids.filter((id) => !diagnosticIds.has(id))");
    expect(SOURCE).toContain("if (!visibleIds.length) return null;");
  });

  it("keeps them when the preference is on", () => {
    setShowDiagnostics(true);
    expect(
      renderToStaticMarkup(
        createElement(ArtifactNodes, {
          ids: ["audit"],
          diagnosticIds: new Set(["audit"]),
          onOpen: () => undefined,
        }),
      ),
    ).toContain("aria-expanded");
    expect(SOURCE).toContain("const showDiagnostics = useShowDiagnostics();");
  });

  it("leaves the results alone either way", () => {
    // Filtering is about the diagnostic ids only; a node that also holds a
    // result keeps its opener with the preference off.
    expect(
      renderToStaticMarkup(
        createElement(ArtifactNodes, {
          ids: ["result", "audit"],
          diagnosticIds: new Set(["audit"]),
          onOpen: () => undefined,
        }),
      ),
    ).toContain("aria-expanded");
  });

  it("hides nothing for a list with no run behind it", () => {
    // Omitting `diagnosticIds` is the old behaviour: no run, no closed set,
    // nothing to classify.
    expect(
      renderToStaticMarkup(
        createElement(ArtifactNodes, { ids: ["a", "b"], onOpen: () => undefined }),
      ),
    ).toContain("aria-expanded");
  });
});
