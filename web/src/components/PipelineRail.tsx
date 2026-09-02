/**
 * The stage rail, as a pannable and zoomable canvas.
 *
 * Branching is preserved visually: nodes that declare `branch_of` are stacked in
 * the same column, because the brief requires branches to stay legible rather
 * than being flattened into a line. Status, attempts and retries live on the
 * node itself so attention is visible where the work is.
 *
 * Branching is also what forced the interaction model. A branched run is wider
 * than any sensible strip of screen, and the rail sits above the stage
 * workspace, which needs the rest of the window. So the rail keeps a fixed
 * height and the graph moves inside it: two-finger scroll pans in both axes,
 * pinch (or ctrl/⌘ + wheel) zooms toward the pointer, dragging pans, and the
 * bottom edge is draggable when a branched run genuinely needs more room.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "../lib/i18n";
import type { WorkflowNode } from "../lib/api";
import { elapsedLabel, isAttention } from "../lib/status";
import { cx } from "./ui";

interface Props {
  /** node id -> candidate labels, drawn as parallel nodes in one column. */
  pseudoBranches?: Record<string, string[]>;
  nodes: WorkflowNode[];
  selected: string | null;
  onSelect: (id: string) => void;
}

const DOT: Record<string, string> = {
  succeeded: "bg-ok-500",
  running: "bg-brand-500 animate-pulse",
  retry: "bg-warn-500 animate-pulse",
  blocked: "bg-stop-500",
  failed: "bg-stop-500",
  pending: "bg-line",
};

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 2;
const MIN_HEIGHT = 120;
const MAX_HEIGHT = 460;

/** Group branch siblings into one column so parallel paths read as parallel. */
function toColumns(nodes: WorkflowNode[]): WorkflowNode[][] {
  const ordered = [...nodes].sort((a, b) => a.order - b.order);
  const columns: WorkflowNode[][] = [];
  const byParent = new Map<string, number>();

  for (const node of ordered) {
    const parent = node.branch_of;
    if (parent && byParent.has(parent)) {
      columns[byParent.get(parent)!].push(node);
      continue;
    }
    columns.push([node]);
    if (parent) byParent.set(parent, columns.length - 1);
  }
  return columns;
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

export function PipelineRail({ nodes, selected, onSelect, pseudoBranches }: Props) {
  const viewport = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [height, setHeight] = useState(160);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const resize = useRef<{ y: number; h: number } | null>(null);

  /**
   * Zoom about a point, so the graph grows toward whatever is under the
   * pointer rather than toward the top-left corner. Scaling alone moves the
   * thing you were looking at off-screen, which is the difference between a
   * zoom control and a zoom.
   */
  const zoomAt = useCallback((factor: number, clientX?: number, clientY?: number) => {
    setView((current) => {
      const next = clamp(current.k * factor, MIN_ZOOM, MAX_ZOOM);
      if (next === current.k) return current;
      const box = viewport.current?.getBoundingClientRect();
      const px = clientX !== undefined && box ? clientX - box.left : (box?.width ?? 0) / 2;
      const py = clientY !== undefined && box ? clientY - box.top : (box?.height ?? 0) / 2;
      const ratio = next / current.k;
      return {
        k: next,
        x: px - (px - current.x) * ratio,
        y: py - (py - current.y) * ratio,
      };
    });
  }, []);

  /**
   * Attached by hand rather than through `onWheel`, because React registers
   * wheel listeners as passive and a passive listener cannot call
   * `preventDefault`. Without that call, a pinch on a trackpad zooms the whole
   * browser page and a two-finger scroll navigates back.
   */
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    function onWheel(event: WheelEvent) {
      event.preventDefault();
      // A trackpad pinch arrives as a wheel event with ctrlKey set; the
      // browser synthesises it, and no actual ctrl key is held.
      if (event.ctrlKey || event.metaKey) {
        zoomAt(Math.exp(-event.deltaY / 220), event.clientX, event.clientY);
        return;
      }
      setView((current) => ({
        ...current,
        x: current.x - event.deltaX,
        y: current.y - event.deltaY,
      }));
    }
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  /** Centre the graph in the viewport at a scale that shows all of it. */
  const fit = useCallback(() => {
    const box = viewport.current?.getBoundingClientRect();
    const content = canvas.current;
    if (!box || !content) return;
    const width = content.scrollWidth;
    const tall = content.scrollHeight;
    if (!width || !tall) return;
    const k = clamp(Math.min((box.width - 32) / width, (box.height - 32) / tall), MIN_ZOOM, 1);
    setView({ k, x: (box.width - width * k) / 2, y: (box.height - tall * k) / 2 });
  }, []);

  function onPointerDown(event: React.PointerEvent) {
    if (event.button !== 0) return;
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, ox: view.x, oy: view.y };
    setDragging(true);
  }
  function onPointerMove(event: React.PointerEvent) {
    if (!drag.current) return;
    const start = drag.current;
    setView((current) => ({
      ...current,
      x: start.ox + (event.clientX - start.x),
      y: start.oy + (event.clientY - start.y),
    }));
  }
  function endDrag() {
    drag.current = null;
    setDragging(false);
  }

  // Resizing the rail. A run with three live branches wants more height than a
  // straight one, and which of those you are looking at is not something the
  // component can know.
  useEffect(() => {
    function move(event: PointerEvent) {
      if (!resize.current) return;
      setHeight(clamp(resize.current.h + (event.clientY - resize.current.y), MIN_HEIGHT, MAX_HEIGHT));
    }
    function up() {
      resize.current = null;
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);

  // Training already fits several candidates in one stage. Drawing them as
  // parallel nodes shows what the run actually did; it does not change the
  // pipeline, and they merge back at the next stage because they never left it.
  const expanded = pseudoBranches
    ? nodes.flatMap((node) => {
        const labels = pseudoBranches[node.id];
        if (!labels || labels.length < 2) return [node];
        return labels.map((label, i) => ({
          ...node,
          id: i === 0 ? node.id : `${node.id}::${i}`,
          label,
          branch_of: node.id,
        }));
      })
    : nodes;
  const columns = toColumns(expanded);

  return (
    <div className="relative shrink-0 border-b border-line bg-surface-sunken">
      <div
        ref={viewport}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className={cx(
          "relative overflow-hidden touch-none select-none",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
        style={{
          height,
          // A faint grid is what makes panning legible: without it the graph
          // slides against a flat surface and the movement reads as jitter.
          backgroundImage:
            "radial-gradient(circle, rgb(0 0 0 / 0.055) 1px, transparent 1px)",
          backgroundSize: `${24 * view.k}px ${24 * view.k}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
      >
        <div
          ref={canvas}
          className="absolute left-0 top-0 flex origin-top-left items-stretch gap-2 p-4"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
        >
          {columns.map((column, index) => (
            <div key={index} className="flex items-center gap-2">
              <div className="flex flex-col gap-2">
                {column.map((node) => (
                  <NodeCard
                    key={node.id}
                    node={node}
                    active={node.id === selected}
                    onSelect={onSelect}
                    compact={column.length > 1}
                  />
                ))}
              </div>
              {index < columns.length - 1 && (
                <svg viewBox="0 0 24 8" className="h-2 w-5 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.6">
                  <path d="M0 4h20m0 0-4-3m4 3-4 3" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </div>
          ))}
        </div>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-2.5">
          <span className="rounded bg-surface/80 px-1.5 py-0.5 text-[10px] text-ink-faint backdrop-blur">
            {t("Drag to move · pinch or ctrl+scroll to zoom")}
          </span>
          <div className="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-line bg-surface/95 p-0.5 shadow-sm backdrop-blur">
            <Control onClick={() => zoomAt(1 / 1.2)} label={t("Zoom out")} disabled={view.k <= MIN_ZOOM}>
              −
            </Control>
            <button
              onClick={() => setView({ x: 0, y: 0, k: 1 })}
              title={t("Reset zoom")}
              className="min-w-[3.1rem] rounded px-1 py-1 text-[11px] tabular-nums text-ink-mute hover:bg-surface-sunken"
            >
              {Math.round(view.k * 100)}%
            </button>
            <Control onClick={() => zoomAt(1.2)} label={t("Zoom in")} disabled={view.k >= MAX_ZOOM}>
              +
            </Control>
            <span className="mx-0.5 h-4 w-px bg-line" />
            <button
              onClick={fit}
              title={t("Fit to view")}
              className="rounded px-2 py-1 text-[11px] text-ink-mute hover:bg-surface-sunken"
            >
              {t("Fit")}
            </button>
          </div>
        </div>
      </div>

      <div
        onPointerDown={(event) => {
          resize.current = { y: event.clientY, h: height };
        }}
        title={t("Drag to resize")}
        className="group absolute inset-x-0 -bottom-1 z-10 flex h-2.5 cursor-ns-resize items-center justify-center"
      >
        <span className="h-0.5 w-10 rounded-full bg-line transition-colors group-hover:bg-ink-faint" />
      </div>
    </div>
  );
}

function Control({
  onClick, label, disabled, children,
}: { onClick: () => void; label: string; disabled: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      className="rounded px-2 py-1 text-sm leading-none text-ink-mute hover:bg-surface-sunken disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function NodeCard({
  node, active, onSelect, compact,
}: { node: WorkflowNode; active: boolean; onSelect: (id: string) => void; compact: boolean }) {
  const attention = isAttention(node.status);
  const elapsed = elapsedLabel(node.elapsed_seconds);
  return (
    <button
      onClick={() => onSelect(node.id)}
      title={t(node.description)}
      className={cx(
        "relative flex w-[132px] shrink-0 flex-col gap-1 rounded-xl border bg-surface px-3 text-left transition-all",
        compact ? "py-2" : "py-2.5",
        active
          ? "border-brand-500 ring-2 ring-brand-100"
          : attention
            ? "border-stop-500/40 hover:border-stop-500"
            : "border-line hover:border-ink-faint",
      )}
    >
      <span className="flex items-center justify-between">
        <span className="text-[11px] font-semibold tabular-nums text-ink-faint">
          {String(node.order).padStart(2, "0")}
        </span>
        {node.retry_count > 0 && (
          <span className="rounded bg-warn-50 px-1 text-[10px] font-semibold text-warn-700">
            ×{node.retry_count + 1}
          </span>
        )}
      </span>
      <span className="truncate text-[13px] font-semibold leading-tight text-ink">
        {node.label ?? stageName(node.id)}
      </span>
      <span className="flex items-center gap-1.5">
        <span className={cx("h-2 w-2 shrink-0 rounded-full", DOT[node.status] ?? DOT.pending)} />
        <span className="truncate text-[10.5px] text-ink-mute">
          {node.kind === "deterministic" ? t("deterministic") : t("agent")}
        </span>
        {elapsed && (
          <span className="ml-auto shrink-0 text-[10.5px] tabular-nums text-ink-faint">
            {elapsed}
          </span>
        )}
      </span>
      {attention && (
        <span className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-stop-500 text-[9px] font-bold text-white">
          !
        </span>
      )}
    </button>
  );
}

/**
 * Stage ids are codes, so they are translated through the catalogue by their
 * English display name rather than by prettifying the identifier. Prettifying
 * produced strings like "Eda" that no catalogue could sensibly hold, which is
 * why the rail stayed English while the rest of the interface turned over.
 */
export function stageName(id: string) {
  return t(titleize(id));
}

export function titleize(id: string) {
  if (id === "eda") return "Exploratory analysis";
  // Prettifying would give "Rl Feature Engineering". RL is an initialism, and
  // the same reason `eda` is special-cased applies: the catalogue key has to be
  // a string a person would actually write.
  if (id === "rl_feature_engineering") return "RL feature engineering";
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
