import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

import { t } from "../lib/i18n";

/** A height the viewer sets on the gate approval card by dragging it (#441).
 *
 * The card is mounted `shrink-0` in a column flex above the workspace, with no
 * height cap and no scroll container, so its height is whatever its content
 * happens to be and the canvas underneath gets what is left. That varies a lot:
 * a leakage gate stacks a reason list, one checkbox row per suspect column, a
 * three-column options grid and a free-text field, and takes most of the
 * viewport. The graph the decision is about ends up a sliver, with no way to
 * shrink the card to see it -- and no way to give it more room when the prose
 * is long instead.
 *
 * `ResizableNode` (#67) already drags a south edge and `panelResize` (#407)
 * already clamps against the viewport and remembers the result. This is the
 * same drag on the other axis: the card owns its own height, so unlike the
 * docked panel there is no grid column to publish a setter through.
 */

/** Below this the card cannot show a question and one option row together. */
export const GATE_MIN_HEIGHT = 140;
/** Past this the card is not being read, it is being lived in. */
export const GATE_MAX_HEIGHT = 900;

const STORAGE_KEY = "ads.gateCardHeight";

/** The viewport height, or the tallest the card may ever be where there is no
 *  window at all -- server rendering, and the tests that render this card to
 *  static markup. Without a viewport there is nothing to spare height from, so
 *  the fixed ceiling is the only limit that means anything. */
function viewportHeight(): number {
  return typeof window === "undefined" ? GATE_MAX_HEIGHT : window.innerHeight;
}

/** `height` clamped to what the current viewport can actually give the card.
 *
 * The 75vh ceiling is the point of the whole change: the card may grow, but not
 * so far that the canvas it is asking a question about disappears. On a short
 * window the floor wins first, because a card too short to show an option is
 * worse than a covered canvas.
 */
export function clampGateHeight(height: number, viewport: number): number {
  const ceiling = Math.max(GATE_MIN_HEIGHT, Math.min(GATE_MAX_HEIGHT, Math.round(viewport * 0.75)));
  if (!Number.isFinite(height)) return ceiling;
  return Math.min(ceiling, Math.max(GATE_MIN_HEIGHT, Math.round(height)));
}

/** The stored height, or null when nothing usable is stored.
 *
 * Null is a real value here and not a fallback: it means "as tall as the
 * content", which is what the card did before it could be dragged and what it
 * still does until someone drags it. A gate that fits in 200px should not be
 * padded out to a height chosen for a different gate.
 *
 * `sessionStorage` follows #407: a height chosen for one afternoon's screen is
 * a worse default months later than the one the app ships with. Every access is
 * guarded -- a private window can throw on the getter itself, not just return
 * null.
 */
export function readStoredGateHeight(): number | null {
  try {
    const raw = globalThis.sessionStorage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function storeGateHeight(height: number | null): void {
  try {
    if (height === null) globalThis.sessionStorage?.removeItem(STORAGE_KEY);
    else globalThis.sessionStorage?.setItem(STORAGE_KEY, String(height));
  } catch {
    // A viewer who blocks site data still gets to resize; they just get the
    // content height back on the next load.
  }
}

export interface GateHeight {
  /** Pixels, or null for "as tall as the content". */
  height: number | null;
  resizeTo: (height: number) => void;
  reset: () => void;
}

/** The card's height and its setters.
 *
 * Re-clamps on viewport resize, so a height chosen on a tall window does not
 * leave the canvas with nothing when the window is shortened or the device is
 * rotated.
 */
export function useGateCardHeight(): GateHeight {
  const [height, setHeight] = useState<number | null>(() => {
    const stored = readStoredGateHeight();
    return stored === null ? null : clampGateHeight(stored, viewportHeight());
  });

  const resizeTo = useCallback((next: number) => {
    const clamped = clampGateHeight(next, viewportHeight());
    setHeight(clamped);
    storeGateHeight(clamped);
  }, []);

  const reset = useCallback(() => {
    setHeight(null);
    storeGateHeight(null);
  }, []);

  useEffect(() => {
    function onResize() {
      setHeight((current) => (current === null ? null : clampGateHeight(current, viewportHeight())));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return { height, resizeTo, reset };
}

/** The drag strip along the bottom edge of the gate approval card.
 *
 * Double-click restores the content height. Without it the drag is one-way in
 * practice: once a height is set there is no pointer gesture that means "stop
 * having an opinion about this", and a viewer who dragged the card short would
 * have to guess where the content height was to get back to it.
 */
export function GateResizeHandle({
  cardRef,
  height,
  resizeTo,
  reset,
}: { cardRef: RefObject<HTMLElement | null> } & GateHeight) {
  const [dragging, setDragging] = useState(false);
  const held = useRef(false);

  /** The height the card is at right now, dragged or not. */
  function currentHeight(): number {
    if (height !== null) return height;
    return Math.round(cardRef.current?.getBoundingClientRect().height ?? GATE_MIN_HEIGHT);
  }

  function begin(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    // The strip sits over the card's bottom border and the canvas behind it
    // pans on drag; neither should also see this gesture.
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    held.current = true;
    setDragging(true);
  }

  function move(event: ReactPointerEvent<HTMLDivElement>) {
    if (!held.current) return;
    // Height is the pointer's distance from the card's own top edge rather than
    // a delta accumulated from where the drag started, so a drag that hits the
    // clamp and comes back tracks the pointer instead of drifting away from it.
    const top = cardRef.current?.getBoundingClientRect().top;
    if (top === undefined) return;
    resizeTo(event.clientY - top);
  }

  function end(event: ReactPointerEvent<HTMLDivElement>) {
    if (!held.current) return;
    held.current = false;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  // A pointer drag is not reachable from the keyboard, and this is a real
  // control, so the arrow keys move it the way they move any separator.
  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 64 : 16;
    if (event.key === "ArrowDown") { event.preventDefault(); resizeTo(currentHeight() + step); }
    else if (event.key === "ArrowUp") { event.preventDefault(); resizeTo(currentHeight() - step); }
    else if (event.key === "Home") { event.preventDefault(); resizeTo(GATE_MIN_HEIGHT); }
    else if (event.key === "End") { event.preventDefault(); resizeTo(GATE_MAX_HEIGHT); }
    else if (event.key === "Escape") { event.preventDefault(); reset(); }
  }

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={t("Resize the approval card")}
      title={t("Drag to set the height; double-click to fit the content.")}
      aria-valuenow={height ?? undefined}
      aria-valuemin={GATE_MIN_HEIGHT}
      aria-valuemax={GATE_MAX_HEIGHT}
      tabIndex={0}
      data-no-pan
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      onDoubleClick={reset}
      onKeyDown={onKeyDown}
      // Wider than it looks: the visible line is the card's own bottom border,
      // and this is the hit area around it, the way the node handles are (#67).
      className={`absolute inset-x-0 bottom-0 z-30 h-2 translate-y-1/2 cursor-ns-resize touch-none rounded-b-xl transition-colors hover:bg-brand-300/60 focus-visible:bg-brand-400 focus-visible:outline-none ${dragging ? "bg-brand-400" : ""}`}
    />
  );
}
