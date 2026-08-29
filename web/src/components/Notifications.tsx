import { t } from "../lib/i18n";
/**
 * Run notifications.
 *
 * Derived from the run list rather than maintained separately: a bell that
 * keeps its own notion of "needs attention" drifts from what the runs actually
 * say, and then the badge lies. Everything here is read from `/api/runs`, which
 * already reports status and any pending question.
 *
 * Seen state is per browser and deliberately shallow — this marks which items
 * you have already looked at, not which decisions you have made. The decision
 * itself lives in the run.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type RunSummary } from "../lib/api";
import { reasonLabel } from "../lib/status";
import { cx } from "./ui";

const SEEN_KEY = "ads.notifications.seen";
const POLL_MS = 8000;

interface Item {
  id: string;
  runId: string;
  tone: "stop" | "warn" | "ok";
  title: string;
  detail: string;
  at?: string;
}

function itemsFrom(runs: RunSummary[]): Item[] {
  const items: Item[] = [];
  for (const run of runs) {
    const label = run.label ?? run.dataset ?? run.run_id;
    if (run.pending_question) {
      items.push({
        // Keyed by stage and attempt so a second question on the same run is a
        // new notification rather than a silent overwrite.
        id: `${run.run_id}:ask:${run.pending_question.stage_id}:${run.pending_question.attempt}`,
        runId: run.run_id,
        tone: "stop",
        title: t("Waiting for you"),
        detail: `${label} — ${reasonLabel(run.pending_question.reason_code)}`,
        at: run.last_activity,
      });
    } else if (run.status === "completed") {
      items.push({
        id: `${run.run_id}:done`,
        runId: run.run_id,
        tone: "ok",
        title: t("Run finished"),
        detail: label,
        at: run.last_activity,
      });
    } else if (run.status === "failed") {
      items.push({
        id: `${run.run_id}:failed`,
        runId: run.run_id,
        tone: "warn",
        title: t("Run stopped with an error"),
        detail: label,
        at: run.last_activity,
      });
    }
  }
  return items.sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
}

export function Notifications() {
  const [items, setItems] = useState<Item[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(SEEN_KEY) ?? "[]") as string[]);
    } catch {
      return new Set();
    }
  });
  const panel = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      setItems(itemsFrom(await api.runs()));
    } catch {
      // A failed poll is not worth a visible error; the next one usually works.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const unread = items.filter((i) => !seen.has(i.id));
  // A question outranks a completion: the badge should say "you are blocking
  // something", not "there is news".
  const urgent = unread.some((i) => i.tone === "stop");

  function markAllSeen() {
    const next = new Set([...seen, ...items.map((i) => i.id)]);
    setSeen(next);
    localStorage.setItem(SEEN_KEY, JSON.stringify([...next]));
  }

  return (
    <div className="relative" ref={panel}>
      <button
        onClick={() => { setOpen((o) => !o); if (!open) markAllSeen(); }}
        title={t("Notifications")}
        aria-label={unread.length ? `${unread.length} unread notifications` : "Notifications"}
        className="relative rounded-lg p-2 text-ink-mute hover:bg-surface-sunken hover:text-ink"
      >
        <svg viewBox="0 0 20 20" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.7">
          <path d="M10 3a4.5 4.5 0 0 0-4.5 4.5c0 3-1 4-1.5 4.5h12c-.5-.5-1.5-1.5-1.5-4.5A4.5 4.5 0 0 0 10 3Z" strokeLinejoin="round" />
          <path d="M8.5 15a1.6 1.6 0 0 0 3 0" strokeLinecap="round" />
        </svg>
        {unread.length > 0 && (
          <span
            className={cx(
              "absolute right-1 top-1 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold text-white",
              urgent ? "bg-stop-600" : "bg-brand-600",
            )}
          >
            {unread.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-[340px] overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
          <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
            <span className="flex-1 text-sm font-semibold text-ink">{t("Notifications")}</span>
            <span className="text-[11px] text-ink-faint">{items.length}</span>
          </header>

          {items.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-ink-mute">{t("Nothing to report.")}</p>
          ) : (
            <ul className="max-h-[380px] overflow-y-auto">
              {items.map((i) => (
                <li key={i.id}>
                  <button
                    onClick={() => { setOpen(false); navigate(`/projects?view=runs&run=${i.runId}`); }}
                    className="flex w-full items-start gap-3 border-b border-line-soft px-4 py-3 text-left last:border-b-0 hover:bg-surface-sunken"
                  >
                    <span
                      className={cx(
                        "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                        i.tone === "stop" ? "bg-stop-500" : i.tone === "warn" ? "bg-warn-500" : "bg-ok-500",
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-ink">{i.title}</span>
                      <span className="mt-0.5 block truncate text-xs text-ink-mute">{i.detail}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
