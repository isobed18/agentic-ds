/**
 * What this stage needs a person to look at, gathered in one place.
 *
 * Every design mock carries this panel, and the reason is structural rather
 * than decorative: a stage's concerns are currently scattered across each
 * artifact's warnings, each analysis panel's severity, and the gate's triggered
 * rules. A reader had to open all three to answer "is anything wrong here".
 *
 * Nothing is computed here. Severity was assigned when the measurement was
 * taken; this only collects and orders.
 */
import { t } from "../lib/i18n";
import { Badge, cx } from "./ui";

export interface AttentionItem {
  severity: "issue" | "warning" | "review" | "info" | string;
  title: string;
  detail?: string;
  source?: string;
}

const ORDER: Record<string, number> = { issue: 0, warning: 1, review: 2, info: 3 };

const ICON: Record<string, { bg: string; fg: string; glyph: string }> = {
  issue: { bg: "bg-stop-50", fg: "text-stop-600", glyph: "!" },
  warning: { bg: "bg-warn-50", fg: "text-warn-700", glyph: "!" },
  review: { bg: "bg-brand-50", fg: "text-brand-600", glyph: "?" },
  info: { bg: "bg-surface-sunken", fg: "text-ink-mute", glyph: "i" },
};

export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  if (!items.length) return null;
  const ordered = [...items].sort(
    (a, b) => (ORDER[a.severity] ?? 9) - (ORDER[b.severity] ?? 9),
  );
  const blocking = ordered.filter((i) => i.severity === "issue").length;

  return (
    <aside className="card px-4 py-3.5">
      <header className="mb-2.5 flex items-center gap-2">
        <BellIcon />
        <h3 className="flex-1 text-sm font-semibold text-ink">{t("Needs attention")}</h3>
        <Badge tone={blocking ? "stop" : "warn"}>{ordered.length}</Badge>
      </header>

      <ul className="space-y-2">
        {ordered.map((item, i) => {
          const icon = ICON[item.severity] ?? ICON.info;
          return (
            <li
              key={i}
              className="flex items-start gap-2.5 rounded-lg border border-line bg-surface-sunken px-3 py-2"
            >
              <span
                className={cx(
                  "mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold",
                  icon.bg,
                  icon.fg,
                )}
              >
                {icon.glyph}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium leading-snug text-ink">
                  {item.title}
                </span>
                {item.detail && (
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-mute">
                    {item.detail}
                  </span>
                )}
                {item.source && (
                  <span className="mt-0.5 block text-[10px] uppercase tracking-wide text-ink-faint">
                    {item.source}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-warn-600" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M10 3a4.5 4.5 0 0 0-4.5 4.5c0 3-1.5 4-1.5 4h12s-1.5-1-1.5-4A4.5 4.5 0 0 0 10 3Z" strokeLinejoin="round" />
      <path d="M8.6 15a1.6 1.6 0 0 0 2.8 0" strokeLinecap="round" />
    </svg>
  );
}
