/** Interactive surfaces that own a pointer gesture instead of the canvas.
 *
 * Keep this shared between both hand-built canvases. The guided canvas once
 * omitted panels and data-no-pan overlays, so dragging to select inspector text
 * moved the graph and could strand its pointer-capture state (#161).
 *
 * `label` is here because the list was written in terms of the controls
 * themselves and a label is the rest of a control's hit area -- the part a
 * person actually aims at. Without it, a press on a checkbox's words started a
 * pan, the viewport captured the pointer, and the click that followed was
 * delivered there instead of to the box, so the checkbox never flipped (#189).
 * A label is never canvas background.
 */
export const CANVAS_PAN_BLOCKERS =
  "button,input,textarea,select,label,a,aside,[role='dialog'],[data-no-pan]";

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
