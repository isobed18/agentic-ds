/** Zoom arithmetic for the understanding canvas.
 *
 * Separated from the component so the bounds are testable without a DOM. The
 * interesting cases are all at the edges -- repeated clicks at a limit, and the
 * scrollable box keeping pace with the scaled content -- and none of them are
 * visible in a rendered snapshot.
 */

/** Legibility, not arithmetic, sets the floor: the phase nodes carry 10px
 *  labels and stop being readable below about half scale, so zooming further
 *  out would trade away the only thing zooming out is for. */
export const CANVAS_MIN_ZOOM = 0.5;
export const CANVAS_MAX_ZOOM = 1.6;
export const CANVAS_ZOOM_STEP = 1.2;

/** The unzoomed content box. */
export const CANVAS_BASE_WIDTH = 1500;
export const CANVAS_BASE_HEIGHT = 860;

export function clampZoom(value: number): number {
  // NaN has no position on the scale, so there is nothing to clamp it to and 1
  // is the only sane answer -- a NaN scale renders an empty canvas with no
  // error to explain it. An infinity does have a position, and clamps normally.
  if (Number.isNaN(value)) return 1;
  return Math.min(CANVAS_MAX_ZOOM, Math.max(CANVAS_MIN_ZOOM, value));
}

/** The zoom after one step in `direction` (-1 out, +1 in). */
export function steppedZoom(current: number, direction: number): number {
  return clampZoom(direction > 0 ? current * CANVAS_ZOOM_STEP : current / CANVAS_ZOOM_STEP);
}

/** The scrollable box for a given zoom.
 *
 * It has to grow with the content. Leaving it at the unzoomed size crops the
 * graph at the old bounds, and the right-hand nodes -- the proposed plan and
 * the pipeline preview, which is where the eye goes last -- become unreachable
 * at any zoom above 1.
 */
export function scaledBox(zoom: number): { width: number; height: number } {
  return { width: CANVAS_BASE_WIDTH * zoom, height: CANVAS_BASE_HEIGHT * zoom };
}

/** Whether a wheel event asks for zoom rather than scroll.
 *
 * Only the browser's own zoom gesture is taken over. A plain wheel or a
 * two-finger trackpad swipe must keep panning, which is how this canvas has
 * always worked and what people already have in their hands.
 */
export function isZoomGesture(event: { ctrlKey: boolean; metaKey: boolean }): boolean {
  return event.ctrlKey || event.metaKey;
}
