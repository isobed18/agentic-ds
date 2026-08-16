/**
 * The horizontal analysis strip and its expanded panel.
 *
 * This is the layout `design_assets/eda_design.png` specifies and the previous
 * version got wrong: analyses were stacked vertically as collapsed headings, so
 * a reader had to open each one to discover whether it held anything worth
 * seeing. Here every analysis is visible at once as a thumbnail carrying its own
 * chart and severity, and the selected one expands below with the full chart,
 * key insights and the numbers behind it.
 *
 * Severity comes from the backend. The frontend deciding for itself what counts
 * as a warning would eventually disagree with the gate, and a UI that flags
 * something the pipeline considers fine — or stays calm about something it
 * stopped for — is worse than either being slightly miscalibrated.
 */
import { useEffect, useRef, useState } from "react";
import { Chart, type ChartSpec } from "./Charts";
import { Badge, DataTable, cx } from "./ui";

export interface AnalysisPanel {
  id: string;
  title: string;
  severity: "ok" | "info" | "review" | "warning" | "issue" | string;
  caption?: string;
  description?: string;
  chart: ChartSpec;
  insights?: string[];
  table?: { columns: string[]; rows: (string | number)[][] } | null;
}

const SEVERITY: Record<string, { tone: "neutral" | "ok" | "warn" | "stop" | "brand"; label: string }> = {
  ok: { tone: "ok", label: "OK" },
  info: { tone: "neutral", label: "Info" },
  review: { tone: "brand", label: "Review" },
  warning: { tone: "warn", label: "Warning" },
  issue: { tone: "stop", label: "Potential issue" },
};

export function AnalysisStrip({ panels }: { panels: AnalysisPanel[] }) {
  const [selected, setSelected] = useState(panels[0]?.id ?? null);
  const scroller = useRef<HTMLDivElement>(null);

  // Keep the selection valid when a different stage or run swaps the panels out.
  useEffect(() => {
    if (!panels.some((p) => p.id === selected)) setSelected(panels[0]?.id ?? null);
  }, [panels, selected]);

  if (!panels.length) return null;
  const active = panels.find((p) => p.id === selected) ?? panels[0];

  const scroll = (dir: -1 | 1) =>
    scroller.current?.scrollBy({ left: dir * 320, behavior: "smooth" });

  return (
    <section className="mb-4">
      <div className="relative">
        <div
          ref={scroller}
          className="flex snap-x gap-3 overflow-x-auto scroll-smooth pb-2"
          style={{ scrollbarWidth: "thin" }}
        >
          {panels.map((panel) => {
            const meta = SEVERITY[panel.severity] ?? SEVERITY.info;
            const isActive = panel.id === active.id;
            return (
              <button
                key={panel.id}
                onClick={() => setSelected(panel.id)}
                aria-pressed={isActive}
                className={cx(
                  "group relative w-[212px] shrink-0 snap-start rounded-xl border bg-surface p-3 text-left transition-all",
                  isActive
                    ? "border-brand-500 ring-2 ring-brand-100"
                    : "border-line hover:border-ink-faint",
                )}
              >
                <div className="mb-1.5 flex items-start justify-between gap-2">
                  <span className="text-[13px] font-semibold leading-tight text-ink">
                    {panel.title}
                  </span>
                  {isActive && (
                    <span className="grid h-4 w-4 shrink-0 place-items-center rounded bg-brand-500 text-[10px] font-bold text-white">
                      ✓
                    </span>
                  )}
                </div>
                <Badge tone={meta.tone}>{meta.label}</Badge>
                <div className="my-2 h-[76px] w-full text-ink">
                  <Chart spec={panel.chart} compact />
                </div>
                <p className="truncate text-[11px] text-ink-mute" title={panel.caption}>
                  {panel.caption}
                </p>
              </button>
            );
          })}
        </div>

        {panels.length > 3 && (
          <>
            <ScrollButton side="left" onClick={() => scroll(-1)} />
            <ScrollButton side="right" onClick={() => scroll(1)} />
          </>
        )}
      </div>

      <ExpandedPanel panel={active} />
    </section>
  );
}

function ScrollButton({ side, onClick }: { side: "left" | "right"; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={side === "left" ? "Scroll left" : "Scroll right"}
      className={cx(
        "absolute top-1/2 z-10 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-full border border-line bg-surface shadow-card hover:bg-surface-sunken",
        side === "left" ? "-left-3" : "-right-3",
      )}
    >
      <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 text-ink-mute" fill="none" stroke="currentColor" strokeWidth="2">
        <path
          d={side === "left" ? "M12 5 7 10l5 5" : "m8 5 5 5-5 5"}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

function ExpandedPanel({ panel }: { panel: AnalysisPanel }) {
  const [open, setOpen] = useState(true);
  const meta = SEVERITY[panel.severity] ?? SEVERITY.info;

  return (
    <article className="card mt-1 px-4 py-3.5">
      <header className="flex flex-wrap items-center gap-2.5">
        <h3 className="text-sm font-semibold text-ink">{panel.title}</h3>
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <button
          onClick={() => setOpen((o) => !o)}
          className="btn-ghost ml-auto !py-1 text-[11px]"
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </header>
      {panel.description && (
        <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-ink-mute">
          {panel.description}
        </p>
      )}

      {open && (
        <div className="mt-3 grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <div className="min-w-0 text-ink">
            <div className="min-h-[220px] w-full">
              <Chart spec={panel.chart} />
            </div>
            {panel.chart.x_label && (
              <p className="mt-1 text-center text-[11px] text-ink-faint">{panel.chart.x_label}</p>
            )}
          </div>

          <div className="min-w-0 space-y-4">
            {panel.insights && panel.insights.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-xs font-semibold text-ink">Key insights</h4>
                <ul className="space-y-1.5">
                  {panel.insights.map((insight, i) => (
                    <li key={i} className="flex gap-2 text-xs leading-relaxed text-ink-soft">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
                      <span>{insight}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {panel.table && panel.table.rows.length > 0 && (
              <div>
                <h4 className="mb-1.5 text-xs font-semibold text-ink">Summary statistics</h4>
                <div className="max-h-[260px] overflow-y-auto rounded-lg border border-line">
                  <DataTable columns={panel.table.columns} rows={panel.table.rows} />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
