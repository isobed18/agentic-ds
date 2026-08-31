/**
 * Shared primitives.
 *
 * `Disclosure` is the load-bearing one: the design brief asks for progressive
 * disclosure throughout — summaries first, detail on demand — so every result
 * section in the workspace is collapsible rather than permanently occupying the
 * screen.
 */
import { useState, type ReactNode } from "react";

export const cx = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(" ");

/**
 * #183: the width of a workspace header's rename field.
 *
 * It was `w-[290px] max-w-[28vw]`, which reads as responsive but is not: the
 * vw cap only bites below roughly a 1000px viewport, so at every ordinary
 * window size the box sat at a flat 290px and never moved. clamp() makes the
 * viewport term the driver instead, with the pixel values as floor and
 * ceiling, so the field tracks the window continuously.
 *
 * The ceiling is well under the old fixed width because the section tabs are
 * centred in the same header by absolute positioning -- they are outside the
 * flex flow and cannot push back -- so the field has to yield on its own or it
 * grows underneath them.
 *
 * Shared because the project and automation headers sit one navigation step
 * apart and are read as the same control; two copies of the numbers would
 * drift.
 */
export const NAME_FIELD_WIDTH = "min-w-0 w-[clamp(112px,13vw,216px)]";

type Tone = "neutral" | "ok" | "warn" | "stop" | "brand";

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-sunken text-ink-mute border border-line",
  ok: "bg-ok-50 text-ok-700",
  warn: "bg-warn-50 text-warn-700",
  stop: "bg-stop-50 text-stop-700",
  brand: "bg-brand-50 text-brand-700",
};

/**
 * `truncate` is for badges holding a name the product does not control -- a file
 * name, above all. Without it a long one stretched the chip past its card and
 * put a horizontal scrollbar on the whole panel (#62).
 *
 * It takes two elements rather than one class. `.chip` is `inline-flex`, and
 * `text-overflow` does not apply to a flex container's own text, so the
 * ellipsis has to happen on a child; and both the chip and that child need
 * `min-w-0`, because a flex item's automatic minimum is its content and it
 * would otherwise refuse to shrink at all. `title` keeps the full name
 * reachable on hover, the way the file chips in `BranchNode` already do.
 */
export function Badge({ children, tone = "neutral", title, truncate = false }: { children: ReactNode; tone?: Tone; title?: string; truncate?: boolean }) {
  return (
    <span className={cx("chip", TONE[tone], truncate && "min-w-0 max-w-full")} title={title}>
      {truncate ? <span className="min-w-0 truncate">{children}</span> : children}
    </span>
  );
}

/** Map backend vocabulary to a visual tone in one place. */
export function toneFor(value?: string | null): Tone {
  const v = (value ?? "").toLowerCase();
  if (["succeeded", "completed", "passed", "good", "auto_proceed", "resolved", "ok", "high"].some((s) => v.includes(s))) return "ok";
  if (["failed", "escalate", "blocked", "awaiting_human", "interrupted", "error", "critical"].some((s) => v.includes(s))) return "stop";
  if (["retry", "warn", "review", "monitor", "pending_review", "medium"].some((s) => v.includes(s))) return "warn";
  if (["running", "active", "resuming", "queued", "selected", "winner"].some((s) => v.includes(s))) return "brand";
  return "neutral";
}

export function Disclosure({
  title,
  count,
  defaultOpen = false,
  right,
  children,
}: {
  title: ReactNode;
  count?: number;
  defaultOpen?: boolean;
  right?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="card overflow-hidden">
      <header className="flex items-center gap-3 px-4 py-3">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex flex-1 items-center gap-2.5 text-left"
          aria-expanded={open}
        >
          <Chevron open={open} />
          <span className="text-sm font-semibold text-ink">{title}</span>
          {count !== undefined && (
            <span className="rounded-full bg-surface-sunken px-2 py-0.5 text-xs text-ink-mute">{count}</span>
          )}
        </button>
        {right}
      </header>
      {open && <div className="border-t border-line px-4 py-4">{children}</div>}
    </section>
  );
}

export function Chevron({ open, size = "sm" }: { open: boolean; size?: "sm" | "md" }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={cx(size === "md" ? "h-5 w-5" : "h-4 w-4", "shrink-0 text-ink-faint transition-transform", open && "rotate-90")}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d="M7.5 4.5 13 10l-5.5 5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Who can see a thing (#207). There is no icon library here, so these follow
 * `Chevron` above: a 20x20 viewBox, no fill, `currentColor` stroke, sized with
 * Tailwind so each one inherits the colour of the text it sits beside.
 *
 * Pass `label` where the icon stands alone on a card and is the only thing
 * saying "private"; leave it off next to a visible word, where a screen reader
 * would otherwise read the state twice.
 */
function VisibilityGlyph({
  label,
  className,
  children,
}: {
  label?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={cx("h-4 w-4 shrink-0", className)}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
    >
      {children}
    </svg>
  );
}

export function Lock({ label, className }: { label?: string; className?: string }) {
  return (
    <VisibilityGlyph label={label} className={className}>
      <rect x="4.75" y="8.75" width="10.5" height="6.75" rx="1.6" />
      <path d="M7.4 8.75V6.9a2.6 2.6 0 0 1 5.2 0v1.85" />
    </VisibilityGlyph>
  );
}

export function Globe({ label, className }: { label?: string; className?: string }) {
  return (
    <VisibilityGlyph label={label} className={className}>
      <circle cx="10" cy="10" r="6.25" />
      <path d="M3.9 10h12.2" />
      <path d="M10 3.75c1.75 1.7 2.62 3.78 2.62 6.25S11.75 14.55 10 16.25c-1.75-1.7-2.62-3.78-2.62-6.25S8.25 5.45 10 3.75Z" />
    </VisibilityGlyph>
  );
}

export function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card flex min-w-[128px] flex-col gap-0.5 px-3.5 py-2.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</span>
      <span className="text-sm font-semibold text-ink">{value}</span>
      {hint && <span className="text-[11px] text-ink-mute">{hint}</span>}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line bg-surface px-6 py-14 text-center">
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint && <p className="max-w-md text-xs text-ink-mute">{hint}</p>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-ink-mute">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-brand-600" />
      {label}
    </div>
  );
}

export function DataTable({ columns, rows }: { columns: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line">
            {columns.map((c) => (
              <th key={c} className="px-3 py-2 text-left text-xs font-semibold text-ink-mute">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-line-soft last:border-0 hover:bg-surface-sunken">
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-2 text-ink-soft">{String(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
