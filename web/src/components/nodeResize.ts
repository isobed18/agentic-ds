export type ResizeEdge = "e" | "s" | "se";

export interface NodeSize {
  width: number;
  height: number;
}

export interface ResizeBounds {
  minWidth: number;
  maxWidth: number;
  minHeight: number;
  maxHeight: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** The new size after dragging one edge or the corner by (dx, dy).
 *
 * #67: nodes had hardcoded widths and no resize. Only the axes the grabbed
 * handle controls move — the right edge changes width alone, the bottom edge
 * height alone, the corner both — and every result is clamped. The clamp is not
 * cosmetic: without a floor a node collapses to nothing, and without a ceiling
 * one node shoves its flex neighbours off-canvas instead of letting them reflow.
 */
export function resizeFromEdge(
  edge: ResizeEdge,
  start: NodeSize,
  delta: { dx: number; dy: number },
  bounds: ResizeBounds,
): NodeSize {
  const width =
    edge === "e" || edge === "se"
      ? clamp(start.width + delta.dx, bounds.minWidth, bounds.maxWidth)
      : start.width;
  const height =
    edge === "s" || edge === "se"
      ? clamp(start.height + delta.dy, bounds.minHeight, bounds.maxHeight)
      : start.height;
  return { width, height };
}
