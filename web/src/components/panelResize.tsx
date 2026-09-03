import { createContext, useCallback, useContext, useEffect, useState, type PointerEvent as ReactPointerEvent } from "react";

import { t } from "../lib/i18n";

/** A docked side panel the viewer can widen by dragging its left edge (#407).
 *
 * The inspector's width was a constant in the canvas grid, so a long rationale,
 * a table list, or a relationship list scrolled in a 27.5rem column no matter
 * how much empty canvas sat beside it. `ResizableNode` already does this for
 * cards on the canvas, but a node sizes itself; a docked panel is a grid column
 * of the surface around it, so the width has to live where the grid does. This
 * is the same drag, split across that boundary: `CanvasSurface` owns the number
 * and publishes a setter, and the handle inside the panel calls it.
 */

/** 27.5rem -- the width the column was pinned at before it could be dragged. */
export const PANEL_DEFAULT_WIDTH = 440;
/** Below this the panel's own two-column metric grids start to break up. */
export const PANEL_MIN_WIDTH = 288;
/** Wide enough for the widest content here; past it the canvas is the loser. */
export const PANEL_MAX_WIDTH = 960;

const STORAGE_KEY = "ads.panelWidth";

/** The viewport width, or the widest the panel may ever be where there is no
 *  window at all -- server rendering, and the tests that render these panels to
 *  static markup. Without a viewport there is nothing to spare width from, so
 *  the fixed ceiling is the only limit that means anything. */
function viewportWidth(): number {
  return typeof window === "undefined" ? PANEL_MAX_WIDTH : window.innerWidth;
}

/** `width` clamped to what the current viewport can actually give the panel.
 *
 * The 94vw ceiling is inherited from the old `min(27.5rem, 94vw)` column: on a
 * phone the panel is allowed to take nearly everything, because the alternative
 * is a panel too narrow to read. On a desktop the fixed ceiling wins first.
 */
export function clampPanelWidth(width: number, viewport: number): number {
  const ceiling = Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, Math.round(viewport * 0.94)));
  if (!Number.isFinite(width)) return Math.min(ceiling, PANEL_DEFAULT_WIDTH);
  return Math.min(ceiling, Math.max(PANEL_MIN_WIDTH, Math.round(width)));
}

/** The stored width, or null when nothing usable is stored.
 *
 * `sessionStorage` rather than `localStorage`: the issue asks for persistence
 * as a nice-to-have, and a width chosen for one afternoon's screen is a worse
 * default months later than the one the app ships with. Every access is guarded
 * -- a private window can throw on the getter itself, not just return null.
 */
export function readStoredPanelWidth(): number | null {
  try {
    const raw = globalThis.sessionStorage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function storePanelWidth(width: number): void {
  try {
    globalThis.sessionStorage?.setItem(STORAGE_KEY, String(width));
  } catch {
    // A viewer who blocks site data still gets to resize; they just get the
    // default back on the next load.
  }
}

type PanelResize = { width: number; resizeTo: (width: number) => void };

const PanelResizeContext = createContext<PanelResize | null>(null);

export const PanelResizeProvider = PanelResizeContext.Provider;

/** The panel width and its setter, for the component that owns the layout.
 *
 * Re-clamps on viewport resize, so a width chosen on a wide window does not
 * leave the canvas with nothing when the window is narrowed or the device is
 * rotated.
 */
export function usePanelWidth(): PanelResize {
  const [width, setWidth] = useState(() =>
    clampPanelWidth(readStoredPanelWidth() ?? PANEL_DEFAULT_WIDTH, viewportWidth()),
  );

  const resizeTo = useCallback((next: number) => {
    const clamped = clampPanelWidth(next, viewportWidth());
    setWidth(clamped);
    storePanelWidth(clamped);
  }, []);

  useEffect(() => {
    function onResize() {
      setWidth((current) => clampPanelWidth(current, viewportWidth()));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return { width, resizeTo };
}

/** The drag strip on a docked panel's left edge.
 *
 * Renders nothing outside a `PanelResizeProvider`, so a panel that is not a
 * resizable column -- an overlay, a test rendering the panel on its own --
 * keeps working unchanged.
 */
export function PanelResizeHandle() {
  const control = useContext(PanelResizeContext);
  const [dragging, setDragging] = useState(false);
  if (!control) return null;
  const { width, resizeTo } = control;

  function begin(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    // The strip sits over the panel's border, and the canvas behind it pans on
    // drag; neither should also see this gesture.
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function move(event: ReactPointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    // The panel is docked right, so its left edge moving left widens it. Width
    // is read from the pointer's distance to the right edge rather than
    // accumulated from a delta, so a drag that hits the clamp and comes back
    // tracks the pointer instead of drifting away from it.
    resizeTo(viewportWidth() - event.clientX);
  }

  function end(event: ReactPointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  // A pointer drag is not reachable from the keyboard, and this is a real
  // control, so the arrow keys move it the way they move any separator.
  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 64 : 16;
    if (event.key === "ArrowLeft") { event.preventDefault(); resizeTo(width + step); }
    else if (event.key === "ArrowRight") { event.preventDefault(); resizeTo(width - step); }
    else if (event.key === "Home") { event.preventDefault(); resizeTo(PANEL_MAX_WIDTH); }
    else if (event.key === "End") { event.preventDefault(); resizeTo(PANEL_MIN_WIDTH); }
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t("Resize panel")}
      aria-valuenow={width}
      aria-valuemin={PANEL_MIN_WIDTH}
      aria-valuemax={PANEL_MAX_WIDTH}
      tabIndex={0}
      data-no-pan
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      // Wider than it looks: the visible line is the panel's own border, and
      // this is the hit area around it, the way the node handles are (#67).
      className={`absolute inset-y-0 left-0 z-30 w-2 -translate-x-1/2 cursor-ew-resize touch-none transition-colors hover:bg-brand-300/60 focus-visible:bg-brand-400 focus-visible:outline-none ${dragging ? "bg-brand-400" : ""}`}
    />
  );
}
