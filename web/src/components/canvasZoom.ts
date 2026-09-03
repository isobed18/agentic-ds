/** Zoom arithmetic for the understanding canvas.
 *
 * Zoom is held as an integer step, not as a scale factor, and that is the whole
 * design. Multiplying a float by 1.2 on the way in and dividing on the way out
 * looks like an inverse and is not one: the moment a step saturates at a limit
 * the multiplication is lost, so three scrolls in and three back out landed at
 * 92.6% instead of 100% (#57). Clamping the step index instead means going one
 * past the end costs nothing and comes back exactly.
 *
 * Separated from the component because every interesting case is an edge, and
 * none of them are visible in a rendered snapshot.
 */

/** Ratio between adjacent steps. */
export const CANVAS_ZOOM_RATIO = 1.2;

/** Step bounds, chosen for legibility rather than arithmetic: the phase nodes
 *  carry 10px labels and stop being readable much below half scale, so zooming
 *  further out would trade away the only thing zooming out is for. */
export const CANVAS_MIN_STEP = -4;
export const CANVAS_MAX_STEP = 3;

/** The unzoomed content box -- a *floor*, not a ceiling (#203).
 *
 * It used to be both. The graph row was drawn into a box fixed at this width
 * and centred inside it, and the row's own minimum is 1430px: 70px of slack in
 * a canvas whose nodes are resizable and whose fork connectors grow with the
 * number of branches. The moment the row outgrew the box, `justify-center`
 * overflowed it equally on *both* sides -- and the left overflow sits at a
 * negative offset inside a scroll container whose `scrollLeft` cannot go below
 * zero, so it was unreachable by definition. The first node in the row is
 * "Uploaded files", which is the one that vanished.
 *
 * Neither collapsing the sidebar nor zooming out could help: the node was not
 * behind the sidebar but clipped inside the canvas's own scroll box, and the
 * box was scaled as a whole, so the overflow scaled with it and the proportion
 * never changed.
 */
// #380: the floor the scroll box reserves for the graph, in px. The graph it
// has to contain is 10% wider and taller now, so this is too.
export const CANVAS_BASE_WIDTH = 1650;
export const CANVAS_BASE_HEIGHT = 946;

export function clampStep(step: number): number {
  if (!Number.isFinite(step)) return 0;
  return Math.min(CANVAS_MAX_STEP, Math.max(CANVAS_MIN_STEP, Math.round(step)));
}

/** The scale for a step. Step 0 is exactly 1, so the default state is not a
 *  rounded approximation of itself. */
export function zoomForStep(step: number): number {
  const clamped = clampStep(step);
  return clamped === 0 ? 1 : CANVAS_ZOOM_RATIO ** clamped;
}

/** The step nearest a desired scale, for a caller that wants a level rather
 *  than an increment (#58). */
export function stepForZoom(zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return 0;
  return clampStep(Math.log(zoom) / Math.log(CANVAS_ZOOM_RATIO));
}

/** What the control shows. Rounded once, here, so the label and the transform
 *  can never disagree about what the current zoom is. */
export function zoomPercent(step: number): number {
  return Math.round(zoomForStep(step) * 100);
}

/** The scrollable box for a step.
 *
 * It has to grow with the content. Left at the unzoomed size, zooming in crops
 * the graph at the old bounds and the right-hand nodes -- the proposed plan and
 * the pipeline preview, which is where the eye goes last -- become unreachable.
 */
export function scaledBox(
  step: number,
  content?: { width?: number; height?: number },
): { width: number; height: number } {
  const zoom = zoomForStep(step);
  return {
    width: Math.max(CANVAS_BASE_WIDTH, content?.width ?? 0) * zoom,
    height: Math.max(CANVAS_BASE_HEIGHT, content?.height ?? 0) * zoom,
  };
}

/** Whether a wheel event asks for zoom rather than scroll.
 *
 * Only the browser's own zoom gesture is taken over. A plain wheel or a
 * two-finger trackpad swipe keeps panning, which is how this canvas has always
 * worked and what people already have in their hands.
 */
export function isZoomGesture(event: { ctrlKey: boolean; metaKey: boolean }): boolean {
  return event.ctrlKey || event.metaKey;
}
