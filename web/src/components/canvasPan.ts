/** Interactive surfaces that own a pointer gesture instead of the canvas.
 *
 * Keep this shared between both hand-built canvases. The guided canvas once
 * omitted panels and data-no-pan overlays, so dragging to select inspector text
 * moved the graph and could strand its pointer-capture state (#161).
 */
export const CANVAS_PAN_BLOCKERS =
  "button,input,textarea,select,a,aside,[role='dialog'],[data-no-pan]";

export function isCanvasPanBlocked(target: EventTarget | null): boolean {
  if (!target || typeof target !== "object" || !("closest" in target)) return false;
  const closest = (target as { closest?: (selector: string) => unknown }).closest;
  return typeof closest === "function" && Boolean(closest.call(target, CANVAS_PAN_BLOCKERS));
}

export function releaseCanvasPointer(
  node: Pick<Element, "hasPointerCapture" | "releasePointerCapture"> | null,
  pointerId: number,
): void {
  if (node?.hasPointerCapture(pointerId)) node.releasePointerCapture(pointerId);
}
