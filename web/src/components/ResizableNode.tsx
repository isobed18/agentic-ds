import { useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import { t } from "../lib/i18n";
import { resizeFromEdge, type NodeSize, type ResizeBounds, type ResizeEdge } from "./nodeResize";

const HANDLES: { edge: ResizeEdge; className: string; label: string }[] = [
  // Thin strips along the right and bottom, a small square in the corner. Each
  // carries its own resize cursor so the affordance is visible on hover, the
  // way the stage rail's height handle already is (#67).
  { edge: "e", className: "right-0 top-0 h-full w-1.5 cursor-ew-resize", label: "Resize node width" },
  { edge: "s", className: "bottom-0 left-0 h-1.5 w-full cursor-ns-resize", label: "Resize node height" },
  { edge: "se", className: "bottom-0 right-0 h-3 w-3 cursor-nwse-resize", label: "Resize node" },
];

const DEFAULT_BOUNDS: ResizeBounds = {
  minWidth: 120,
  maxWidth: 520,
  minHeight: 64,
  maxHeight: 640,
};

/** A node card the viewer can resize from its edges, with neighbours reflowing.
 *
 * #67: every node had a hardcoded size and no way to change it. This wraps a
 * card, sizes it by inline style, and drives that size from edge/corner drags.
 * The card fills the wrapper (`w-full h-full`), so the wrapper's size is the
 * node's size; because the wrapper is an ordinary flex item, growing or
 * shrinking it makes its siblings reflow rather than overlap.
 */
export function ResizableNode({
  defaultWidth,
  defaultHeight,
  bounds,
  className,
  children,
}: {
  defaultWidth: number;
  defaultHeight?: number;
  bounds?: Partial<ResizeBounds>;
  className?: string;
  /**
   * #186: a card whose layout depends on how wide it currently is takes a
   * function instead. The width handed over is the one this component puts in
   * the inline style, so a child cannot disagree with what is rendered, and no
   * measuring pass is needed to find it out.
   */
  children: ReactNode | ((width: number) => ReactNode);
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<NodeSize | null>(null);
  const drag = useRef<{ edge: ResizeEdge; startX: number; startY: number; start: NodeSize } | null>(
    null,
  );
  const limits: ResizeBounds = { ...DEFAULT_BOUNDS, ...bounds };
  const width = size?.width ?? defaultWidth;

  function begin(edge: ResizeEdge, event: ReactPointerEvent<HTMLSpanElement>) {
    if (!ref.current) return;
    // The card under a handle is a button/link; stop the drag from also being
    // read as a click that opens the node's inspector.
    event.preventDefault();
    event.stopPropagation();
    const rect = ref.current.getBoundingClientRect();
    drag.current = {
      edge,
      startX: event.clientX,
      startY: event.clientY,
      start: { width: rect.width, height: rect.height },
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function move(event: ReactPointerEvent<HTMLSpanElement>) {
    const state = drag.current;
    if (!state) return;
    setSize(
      resizeFromEdge(
        state.edge,
        state.start,
        { dx: event.clientX - state.startX, dy: event.clientY - state.startY },
        limits,
      ),
    );
  }

  function end(event: ReactPointerEvent<HTMLSpanElement>) {
    if (!drag.current) return;
    drag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return (
    <div
      ref={ref}
      className={className}
      style={{ width, height: size?.height ?? defaultHeight }}
    >
      {typeof children === "function" ? children(width) : children}
      {HANDLES.map((handle) => (
        <span
          key={handle.edge}
          role="separator"
          aria-label={t(handle.label)}
          onPointerDown={(event) => begin(handle.edge, event)}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={end}
          className={`absolute z-20 touch-none ${handle.className}`}
        />
      ))}
    </div>
  );
}
