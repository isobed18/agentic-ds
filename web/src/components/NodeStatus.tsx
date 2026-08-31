/**
 * The status mark and badge every pipeline node uses (#195).
 *
 * Both canvases draw the same concept — a node's state — and each used to draw
 * it its own way: `StatusMark` on the understanding page, `StatusDot` on the ML
 * pipeline, with different palettes, different labels ("Tamamlandı" against
 * "tamamlandı") and a pulse that only one of them stopped for
 * `prefers-reduced-motion`. Keeping two lists in sync was never going to hold,
 * so there is one component here and the vocabularies are folded into one
 * presentation by `statusTone` in `lib/status`.
 */
import { statusBadgeLabel, statusTone } from "../lib/status";
import { Badge, cx } from "./ui";

/** The state mark: ✓ complete, ! needs attention, ● working, ○ waiting. */
export function StatusMark({ status }: { status?: string | null }) {
  const tone = statusTone(status);
  return (
    <span
      className={cx(
        "relative grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[9px]",
        tone === "complete" ? "border-ok-300 bg-ok-50 text-ok-700"
          : tone === "running" ? "border-brand-400 bg-brand-50 text-brand-700"
            : tone === "attention" ? "border-stop-300 bg-stop-50 text-stop-700"
              : "border-line bg-surface text-ink-faint",
      )}
    >
      {tone === "complete" ? "✓"
        : tone === "attention" ? "!"
          : tone === "running"
            ? <><span>●</span><span className="absolute inset-0 animate-ping rounded-full bg-brand-300 opacity-40 motion-reduce:animate-none" /></>
            : "○"}
    </span>
  );
}

/** The state badge. Capitalised, because it stands on its own rather than mid-sentence. */
export function StatusBadge({ status }: { status?: string | null }) {
  const tone = statusTone(status);
  return (
    <Badge tone={tone === "complete" ? "ok" : tone === "attention" ? "stop" : tone === "running" ? "brand" : "neutral"}>
      {statusBadgeLabel(status)}
    </Badge>
  );
}

/** Mark on the left, badge on the right: the header row of every node card. */
export function NodeStatusHeader({ status }: { status?: string | null }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <StatusMark status={status} />
      <StatusBadge status={status} />
    </div>
  );
}
