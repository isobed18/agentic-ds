/**
 * The join plan as a picture.
 *
 * The brief is explicit that schema discovery must not be legible only to the
 * agent. This renders the same measured joins the agent reasoned over: the base
 * entity on the left, each joined table on the right, and the measured overlap
 * on the edge between them.
 *
 * Confidence is bucketed from the measured overlap rate, and an unmeasured join
 * is drawn as "unmeasured" rather than "low" — colouring an unknown red would
 * assert a finding nobody made.
 */
import { t } from "../lib/i18n";
import { cx } from "./ui";

export interface SchemaGraph {
  base_table?: string | null;
  nodes: { id: string; role: string; source_table?: string | null }[];
  edges: {
    source: string;
    target: string;
    left_columns: string[];
    right_columns: string[];
    how: string;
    overlap_rate?: number | null;
    confidence: string;
    via: string;
  }[];
}

// `label` is a catalogue key, not display text. It is deliberately left
// untranslated here and passed through `t()` at render time: translating at
// module level would freeze whichever language was active on first import.
const CONFIDENCE: Record<string, { dot: string; text: string; label: string }> = {
  high: { dot: "bg-ok-500", text: "text-ok-700", label: "High" },
  medium: { dot: "bg-warn-500", text: "text-warn-700", label: "Medium" },
  low: { dot: "bg-stop-500", text: "text-stop-700", label: "Low" },
  unmeasured: { dot: "bg-ink-faint", text: "text-ink-mute", label: "Unmeasured" },
};

export function SchemaMap({ graph }: { graph: SchemaGraph }) {
  if (!graph.edges.length) return null;
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,220px)_minmax(0,1fr)] lg:items-center">
      <div className="rounded-xl border-2 border-brand-500 bg-brand-50 px-4 py-3">
        <div className="flex items-center gap-2">
          <EntityIcon />
          <span className="min-w-0 truncate text-sm font-semibold text-ink">
            {graph.base_table}
          </span>
        </div>
        <span className="mt-0.5 block text-2xs text-brand-700">{t("Base entity")}</span>
      </div>

      <ul className="space-y-2">
        {graph.edges.map((edge, i) => {
          const node = byId.get(edge.target);
          const meta = CONFIDENCE[edge.confidence] ?? CONFIDENCE.unmeasured;
          return (
            <li key={i} className="flex items-center gap-2">
              <span className="hidden h-px w-6 shrink-0 bg-line lg:block" />
              <div className="flex min-w-0 flex-1 items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2">
                <TableIcon />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-ink">{edge.target}</p>
                  <p className="truncate text-2xs text-ink-mute">
                    {t("via {key}", { key: edge.via || "—" })}
                    {node?.source_table && (
                      <span className="text-ink-faint">
                        {" · "}
                        {t("aggregated from {table}", { table: node.source_table })}
                      </span>
                    )}
                  </p>
                </div>
                <span className="shrink-0 rounded bg-surface-sunken px-1.5 py-0.5 text-3xs uppercase tracking-wide text-ink-mute">
                  {edge.how}
                </span>
                <span className={cx("flex shrink-0 items-center gap-1.5 text-2xs font-medium", meta.text)}>
                  <span className={cx("h-2 w-2 rounded-full", meta.dot)} />
                  {edge.overlap_rate != null
                    ? `${(edge.overlap_rate * 100).toFixed(1)}%`
                    : t(meta.label)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="lg:col-span-2">
        <div className="flex flex-wrap items-center gap-4 border-t border-line-soft pt-2.5">
          <span className="text-2xs font-semibold uppercase tracking-wide text-ink-faint">
            {t("Match confidence")}
          </span>
          {(["high", "medium", "low", "unmeasured"] as const).map((key) => (
            <span key={key} className="flex items-center gap-1.5 text-2xs text-ink-mute">
              <span className={cx("h-2 w-2 rounded-full", CONFIDENCE[key].dot)} />
              {t(CONFIDENCE[key].label)}
            </span>
          ))}
          <span className="text-2xs text-ink-faint">
            {t("Share of child rows whose key was found in the parent.")}
          </span>
        </div>
      </div>
    </div>
  );
}

function EntityIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="10" cy="6.5" r="2.8" />
      <path d="M4 16.5c0-3 2.7-5 6-5s6 2 6 5" strokeLinecap="round" />
    </svg>
  );
}

function TableIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3" y="4" width="14" height="12" rx="1.6" />
      <path d="M3 8h14M8 8v8" />
    </svg>
  );
}
