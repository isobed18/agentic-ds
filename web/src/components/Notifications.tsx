import { t } from "../lib/i18n";
/**
 * Run notifications.
 *
 * Derived from what the server already reports rather than maintained
 * separately: a bell that keeps its own notion of "needs attention" drifts from
 * what the runs actually say, and then the badge lies. Everything here is read
 * from `/api/runs`, which reports status and any pending question, and
 * `/api/home`, whose recent list carries the models and reports runs produced
 * along with the project and automation that own them.
 *
 * Seen state is per browser and deliberately shallow — this marks which items
 * you have already looked at, not which decisions you have made. The decision
 * itself lives in the run.
 *
 * Dismissal is per browser for the same reason, and for a harder one: there is
 * no server-side notification to delete, so "cleared" can only mean "this
 * browser stops rebuilding the item from the run list".
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { itemsFrom, type NotificationItem } from "./notificationItems";
import { dismissAll, dismissOne, pruneDismissed, visibleItems } from "./notificationDismissal";
import { cx } from "./ui";

const SEEN_KEY = "ads.notifications.seen";
const DISMISSED_KEY = "ads.notifications.dismissed";
const POLL_MS = 8000;

function readIds(key: string): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function Notifications() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(() => readIds(SEEN_KEY));
  const [dismissed, setDismissed] = useState<Set<string>>(() => readIds(DISMISSED_KEY));
  const panel = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const refresh = useCallback(async () => {
    try {
      // One interval for both. The artifact events are a bonus on top of the
      // run events, so a failing /api/home degrades to the run-only panel this
      // was rather than blanking it.
      const [runs, home] = await Promise.all([api.runs(), api.home().catch(() => null)]);
      const next = itemsFrom(runs, home?.recent ?? []);
      setItems(next);
      // Pruned only after a poll succeeded: pruning against an empty list
      // because the API was briefly down would resurrect everything cleared.
      setDismissed((current) => {
        const kept = pruneDismissed(current, next);
        if (kept !== current) localStorage.setItem(DISMISSED_KEY, JSON.stringify([...kept]));
        return kept;
      });
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

  const visible = visibleItems(items, dismissed);
  const unread = visible.filter((i) => !seen.has(i.id));
  // A question outranks a completion: the badge should say "you are blocking
  // something", not "there is news".
  const urgent = unread.some((i) => i.tone === "stop");

  function markAllSeen() {
    const next = new Set([...seen, ...visible.map((i) => i.id)]);
    setSeen(next);
    localStorage.setItem(SEEN_KEY, JSON.stringify([...next]));
  }

  function persistDismissed(next: Set<string>) {
    setDismissed(next);
    localStorage.setItem(DISMISSED_KEY, JSON.stringify([...next]));
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
            <span className="text-[11px] text-ink-faint">{visible.length}</span>
            {visible.length > 0 && (
              <button
                onClick={() => persistDismissed(dismissAll(dismissed, visible))}
                className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-ink-mute hover:bg-surface-sunken hover:text-ink"
              >
                {t("Clear all")}
              </button>
            )}
          </header>

          {visible.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-ink-mute">{t("Nothing to report.")}</p>
          ) : (
            <ul className="max-h-[380px] overflow-y-auto">
              {visible.map((i) => (
                // Row and dismiss are siblings, not nested: a button inside a
                // button is invalid markup, and the inner click is swallowed in
                // some browsers however hard the handler tries to stop it.
                <li
                  key={i.id}
                  className="flex items-start border-b border-line-soft last:border-b-0 hover:bg-surface-sunken"
                >
                  <button
                    onClick={() => { setOpen(false); navigate(i.href); }}
                    className="flex min-w-0 flex-1 items-start gap-3 py-3 pl-4 text-left"
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
                  <button
                    onClick={() => persistDismissed(dismissOne(dismissed, i.id))}
                    title={t("Dismiss")}
                    aria-label={t("Dismiss")}
                    className="mr-2 mt-2.5 shrink-0 rounded-md p-1 text-ink-faint hover:bg-surface hover:text-ink"
                  >
                    <svg
                      viewBox="0 0 16 16"
                      className="h-3.5 w-3.5"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                    >
                      <path d="M4 4l8 8M12 4l-8 8" />
                    </svg>
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
