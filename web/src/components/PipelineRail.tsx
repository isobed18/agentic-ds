/**
 * The horizontal stage rail.
 *
 * Branching is preserved visually: nodes that declare `branch_of` are stacked in
 * the same column, because the brief requires branches to stay legible rather
 * than being flattened into a line. Status, attempts and retries live on the
 * node itself so attention is visible where the work is.
 */
import type { WorkflowNode } from "../lib/api";
import { isAttention } from "../lib/status";
import { cx } from "./ui";

interface Props {
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

export function PipelineRail({ nodes, selected, onSelect }: Props) {
  const columns = toColumns(nodes);

  return (
    <div className="shrink-0 overflow-x-auto border-b border-line bg-surface px-6 py-4">
      <div className="flex items-stretch gap-2">
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
    </div>
  );
}

function NodeCard({
  node, active, onSelect, compact,
}: { node: WorkflowNode; active: boolean; onSelect: (id: string) => void; compact: boolean }) {
  const attention = isAttention(node.status);
  return (
    <button
      onClick={() => onSelect(node.id)}
      title={node.description}
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
        {node.label ?? titleize(node.id)}
      </span>
      <span className="flex items-center gap-1.5">
        <span className={cx("h-2 w-2 shrink-0 rounded-full", DOT[node.status] ?? DOT.pending)} />
        <span className="truncate text-[10.5px] text-ink-mute">
          {node.kind === "deterministic" ? "deterministic" : "agent"}
        </span>
      </span>
      {attention && (
        <span className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full bg-stop-500 text-[9px] font-bold text-white">
          !
        </span>
      )}
    </button>
  );
}

export function titleize(id: string) {
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
