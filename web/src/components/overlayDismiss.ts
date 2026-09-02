import { useEffect, useRef } from "react";

/** Close an overlay on an outside press or on Escape (#387).
 *
 * Every dialog and side panel in the app could only be closed with its `×`.
 * Clicking the backdrop, or the workspace behind a docked panel, did nothing.
 * `Notifications` was the one place that handled it, and it handled it inline;
 * this is that pattern lifted so the other seven overlays share it rather than
 * repeating it.
 *
 * Two details the naive version gets wrong:
 *
 * - **A press that starts inside the panel and ends outside is not a dismissal.**
 *   It is a text selection dragged past the edge, or a slider. The decision is
 *   made on `mousedown`, so where the press *began* is what counts -- which is
 *   also why a backdrop `onClick` is not enough for the docked `Inspector`.
 * - **A drag is not a click.** The understanding canvas pans by dragging, and a
 *   pan that happens to start on empty canvas should not take the open panel
 *   with it. A press that travels further than `DRAG_SLOP` is left alone.
 */
const OVERLAY_SELECTOR = '[role="dialog"]';
const DRAG_SLOP = 4;

/** Overlays currently listening, oldest first. Escape only reaches the last. */
const openOverlays: object[] = [];

/** Whether a press on `target` should dismiss the overlay rooted at `panel`.
 *
 * Exported for its own sake: the stacking rule is the part worth pinning, and
 * it is pure.
 */
export function pressDismisses(panel: Element, target: Node): boolean {
  // Inside the panel: not an outside press, whatever happens next.
  if (panel.contains(target)) return false;
  const candidate = target as Partial<Element>;
  const overlay = typeof candidate.closest === "function" ? candidate.closest(OVERLAY_SELECTOR) : null;
  // A press inside some *other* overlay belongs to that overlay. Only an
  // overlay that wraps this panel is this panel's own backdrop -- otherwise
  // opening the extracted-tables dialog from the Documents inspector would let
  // every click in the dialog close the inspector underneath it.
  if (overlay && !overlay.contains(panel)) return false;
  return true;
}

/** Attach the returned ref to the panel; presses outside it close the overlay. */
export function useOverlayDismiss<T extends HTMLElement>(onDismiss: () => void, enabled = true) {
  const panel = useRef<T | null>(null);
  // Latest callback without re-registering the listeners on every render.
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;

  useEffect(() => {
    if (!enabled) return;
    const token = {};
    openOverlays.push(token);
    let press: { x: number; y: number } | null = null;

    const onDown = (event: MouseEvent) => {
      const node = panel.current;
      press = node && event.target && pressDismisses(node, event.target as Node)
        ? { x: event.clientX, y: event.clientY }
        : null;
    };
    const onUp = (event: MouseEvent) => {
      const start = press;
      press = null;
      if (!start) return;
      if (Math.abs(event.clientX - start.x) > DRAG_SLOP) return;
      if (Math.abs(event.clientY - start.y) > DRAG_SLOP) return;
      dismiss.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Only the topmost overlay, so a dialog opened from a panel does not take
      // the panel down with it.
      if (openOverlays[openOverlays.length - 1] !== token) return;
      dismiss.current();
    };

    document.addEventListener("mousedown", onDown);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("keydown", onKey);
    return () => {
      const index = openOverlays.indexOf(token);
      if (index >= 0) openOverlays.splice(index, 1);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("mouseup", onUp);
      document.removeEventListener("keydown", onKey);
    };
  }, [enabled]);

  return panel;
}
