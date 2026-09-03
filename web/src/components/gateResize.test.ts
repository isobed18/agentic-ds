import { describe, expect, it } from "vitest";

import CATALOGUE from "../lib/i18n.ts?raw";
import CARD_SOURCE from "./GateApproval.tsx?raw";
import SOURCE from "./gateResize.tsx?raw";
import {
  GATE_MAX_HEIGHT,
  GATE_MIN_HEIGHT,
  clampGateHeight,
  readStoredGateHeight,
} from "./gateResize";

const TALL = 1200;

describe("resizable gate approval card (#441)", () => {
  it("still fits its content until someone says otherwise", () => {
    // The card had no height of its own and no stored one, so `null` -- "as
    // tall as the content" -- has to survive as a real value rather than being
    // rounded into some default. A gate with three lines in it should not be
    // padded out to a height chosen for a leakage gate with twenty.
    expect(readStoredGateHeight()).toBeNull();
    expect(SOURCE).toContain("height: number | null");
    expect(CARD_SOURCE).toContain(
      "style={gateHeight.height === null ? undefined : { height: gateHeight.height }}",
    );
  });

  it("leaves the canvas on screen no matter how far the drag goes", () => {
    // The whole complaint is that the card takes the viewport and the graph the
    // decision is about becomes a sliver. A drag may grow the card, but not
    // past three quarters of the window.
    expect(clampGateHeight(99_999, TALL)).toBe(900);
    expect(clampGateHeight(99_999, 1000)).toBe(750);
    expect(clampGateHeight(GATE_MAX_HEIGHT, TALL)).toBe(GATE_MAX_HEIGHT);
  });

  it("keeps the card tall enough to answer the gate from", () => {
    expect(clampGateHeight(10, TALL)).toBe(GATE_MIN_HEIGHT);
    // A drag past the pointer's own edge cannot produce a negative or
    // non-finite height.
    expect(clampGateHeight(-400, TALL)).toBe(GATE_MIN_HEIGHT);
    expect(clampGateHeight(Number.NaN, TALL)).toBe(900);
  });

  it("gives a short window the floor rather than an unusable card", () => {
    // On a laptop in a split window 75vh is smaller than the card's own floor.
    // A card too short to show one option is worse than a covered canvas.
    expect(clampGateHeight(GATE_MAX_HEIGHT, 160)).toBe(GATE_MIN_HEIGHT);
  });

  it("scrolls the body instead of clipping it once a height is set", () => {
    // A fixed height with no scroller would hide the option buttons, which is
    // strictly worse than the card being too tall -- the gate would become
    // unanswerable rather than merely inconvenient.
    expect(CARD_SOURCE).toContain('<div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">');
    expect(CARD_SOURCE).toContain(
      'className="card relative mb-4 flex min-h-0 flex-col border-l-4 border-l-stop-500"',
    );
    // The padding moved onto the scroller; the card itself must not keep it, or
    // the last option sits under a padding strip that never scrolls.
    expect(CARD_SOURCE).not.toContain('className="card mb-4 border-l-4 border-l-stop-500 px-4 py-4"');
  });

  it("remembers the choice for the session, not for ever", () => {
    // #407's precedent: a height chosen for one afternoon's screen is a worse
    // default months later than the one the app ships with. Clearing it stores
    // nothing rather than storing a number that means "auto".
    expect(SOURCE).toContain('const STORAGE_KEY = "ads.gateCardHeight"');
    expect(SOURCE).toContain("globalThis.sessionStorage?.setItem(STORAGE_KEY");
    expect(SOURCE).toContain("globalThis.sessionStorage?.removeItem(STORAGE_KEY)");
    expect(SOURCE).not.toContain("localStorage");
  });

  it("re-clamps when the window changes rather than keeping a stale height", () => {
    // A height chosen on a tall window would otherwise leave the canvas with
    // nothing after a resize or a rotation.
    expect(SOURCE).toContain('window.addEventListener("resize", onResize)');
    expect(SOURCE).toContain('window.removeEventListener("resize", onResize)');
  });

  it("does not pan the canvas or answer the gate while dragging", () => {
    // Every control under this strip is a button that submits a decision, so a
    // press that leaks through is not a cosmetic problem.
    expect(SOURCE).toContain("data-no-pan");
    expect(SOURCE).toContain("event.stopPropagation()");
    expect(SOURCE).toContain("setPointerCapture(event.pointerId)");
    expect(SOURCE).toContain("event.preventDefault()");
  });

  it("tracks the pointer from the card's own top edge", () => {
    // Accumulating a delta drifts away from the pointer after a drag hits the
    // clamp and comes back, which on a card this large is very visible.
    expect(SOURCE).toContain("resizeTo(event.clientY - top)");
  });

  it("can be undone without guessing where the content height was", () => {
    // Once a height is set, no pointer gesture would otherwise mean "stop
    // having an opinion about this".
    expect(SOURCE).toContain("onDoubleClick={reset}");
    expect(SOURCE).toContain('event.key === "Escape"');
  });

  it("is reachable from the keyboard and announced as a separator", () => {
    expect(SOURCE).toContain('role="separator"');
    expect(SOURCE).toContain('aria-orientation="horizontal"');
    expect(SOURCE).toContain("tabIndex={0}");
    expect(SOURCE).toContain('event.key === "ArrowUp"');
    expect(SOURCE).toContain('event.key === "ArrowDown"');
    expect(CATALOGUE).toContain('"Resize the approval card": "Onay kartını yeniden boyutlandır"');
    expect(CATALOGUE).toContain('"Drag to set the height; double-click to fit the content."');
  });
});
