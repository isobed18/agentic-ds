/**
 * Closing an overlay by clicking outside it (#387).
 *
 * Every dialog and panel in the app could only be closed with its `×`; clicking
 * the backdrop, or the workspace behind a docked panel, did nothing.
 * `Notifications` was the single exception and handled it inline.
 *
 * The stacking rule is the part worth pinning, so it is a pure function tested
 * against a stub tree; the wiring at each of the eight sites is pinned as source
 * shape, the way the rest of these component tests are.
 */
import { describe, expect, it } from "vitest";

import { pressDismisses } from "./overlayDismiss";
import DISMISS_SOURCE from "./overlayDismiss.ts?raw";
import WORKSPACE_SOURCE from "./UnderstandingWorkspace.tsx?raw";
import REVIEW_SOURCE from "./DocumentTableReview.tsx?raw";
import LAUNCH_SOURCE from "./LaunchDialog.tsx?raw";
import CONTENTS_SOURCE from "./ProjectContents.tsx?raw";
import NOTIFICATIONS_SOURCE from "./Notifications.tsx?raw";
import PROJECT_SOURCE from "../pages/ProjectWorkspace.tsx?raw";

type Stub = {
  parent: Stub | null;
  children: Stub[];
  overlay: boolean;
  contains(other: unknown): boolean;
  closest(selector: string): Stub | null;
  append(child: Stub): Stub;
};

/** The two DOM methods the rule uses, over a tree a test can build by hand. */
function node(overlay = false): Stub {
  const self: Stub = {
    parent: null,
    children: [],
    overlay,
    contains(other) {
      return other === self || self.children.some((child) => child.contains(other));
    },
    closest(selector) {
      if (selector === '[role="dialog"]' && self.overlay) return self;
      return self.parent ? self.parent.closest(selector) : null;
    },
    append(child) {
      child.parent = self;
      self.children.push(child);
      return child;
    },
  };
  return self;
}

const asElement = (stub: Stub) => stub as unknown as Element;

describe("which presses dismiss an overlay (#387)", () => {
  it("ignores a press that begins inside the panel", () => {
    // A text selection dragged past the edge, or a slider: where the press
    // began is what counts, which is why this is decided on mousedown.
    const panel = node();
    const inside = panel.append(node());
    expect(pressDismisses(asElement(panel), asElement(inside))).toBe(false);
  });

  it("dismisses a press on the panel's own backdrop", () => {
    const backdrop = node(true);
    const panel = backdrop.append(node());
    expect(pressDismisses(asElement(panel), asElement(backdrop))).toBe(true);
  });

  it("dismisses a press on the workspace behind a docked panel", () => {
    // The `Inspector` case: an <aside> beside the canvas, with no backdrop of
    // its own, so the canvas itself is the outside.
    const page = node();
    const panel = page.append(node());
    const canvas = page.append(node());
    expect(pressDismisses(asElement(panel), asElement(canvas))).toBe(true);
  });

  it("leaves a panel alone while a dialog is stacked above it", () => {
    // Opening the extracted-tables dialog from the Documents inspector: without
    // this, every click inside the dialog would close the inspector under it.
    const page = node();
    const inspector = page.append(node());
    const dialog = page.append(node(true));
    const dialogPanel = dialog.append(node());
    const button = dialogPanel.append(node());
    expect(pressDismisses(asElement(inspector), asElement(button))).toBe(false);
    // The dialog itself still closes on its own backdrop.
    expect(pressDismisses(asElement(dialogPanel), asElement(dialog))).toBe(true);
  });
});

describe("the dismissal hook", () => {
  it("does not treat a drag as a click", () => {
    // The understanding canvas pans by dragging, and a pan that starts on empty
    // canvas should not take the open panel with it.
    expect(DISMISS_SOURCE).toContain("const DRAG_SLOP = 4");
    expect(DISMISS_SOURCE).toContain("if (Math.abs(event.clientX - start.x) > DRAG_SLOP) return");
  });

  it("gives Escape only to the topmost overlay", () => {
    expect(DISMISS_SOURCE).toContain('if (event.key !== "Escape") return');
    expect(DISMISS_SOURCE).toContain("if (openOverlays[openOverlays.length - 1] !== token) return");
  });
});

describe("every overlay uses it", () => {
  it("covers the backdrop dialogs", () => {
    for (const source of [WORKSPACE_SOURCE, REVIEW_SOURCE, LAUNCH_SOURCE, CONTENTS_SOURCE]) {
      expect(source).toContain("useOverlayDismiss<HTMLDivElement>");
      expect(source).toContain("ref={panel}");
    }
    // Three of them live in this one page.
    expect(PROJECT_SOURCE.match(/useOverlayDismiss<HTMLDivElement>/g)).toHaveLength(3);
    expect(PROJECT_SOURCE.match(/ref=\{panel\}/g)).toHaveLength(3);
  });

  it("cancels rather than confirms a delete dialog", () => {
    expect(PROJECT_SOURCE).toContain("useOverlayDismiss<HTMLDivElement>(onCancel)");
    expect(PROJECT_SOURCE).not.toContain("useOverlayDismiss<HTMLDivElement>(onConfirm)");
    expect(CONTENTS_SOURCE).toContain("useOverlayDismiss<HTMLDivElement>(onCancel)");
  });

  it("covers the docked inspector, which has no backdrop to click", () => {
    expect(WORKSPACE_SOURCE).toContain("const panel = useOverlayDismiss<HTMLElement>(onClose)");
    expect(WORKSPACE_SOURCE).toContain("<aside ref={panel}");
  });

  it("leaves the inline copy in Notifications behind", () => {
    expect(NOTIFICATIONS_SOURCE).toContain("useOverlayDismiss<HTMLDivElement>(() => setOpen(false), open)");
    expect(NOTIFICATIONS_SOURCE).not.toContain('document.addEventListener("mousedown"');
  });

  it("gives the launch dialog's backdrop the role the stacking rule reads", () => {
    // It was the one modal backdrop without it.
    expect(LAUNCH_SOURCE).toContain('bg-ink/40 p-4" role="dialog" aria-modal="true"');
  });
});
