/**
 * Planner / Orchestrator side panel.
 *
 * Collapsible, because the brief is explicit that configuration and chat should
 * not permanently occupy the screen. Remembered rules sit above the transcript
 * so the standing constraints are visible without scrolling.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { t } from "../lib/i18n";
import { Spinner, cx } from "./ui";

interface Message { role: "assistant" | "user"; text: string; at: string }

const RULES = [
  "Never expose raw rows to a model",
  "Ask before a CRITICAL stage proceeds",
  "Block leakage even in full-auto",
  "Retry weak analysis once, then escalate",
];

export function PlannerPanel({
  runId = null, stageId = null, sourceId = null, open = true, onToggle,
}: {
  runId?: string | null;
  stageId?: string | null;
  /** Ask about a dataset before any run exists; the planner gets its profile. */
  sourceId?: string | null;
  open?: boolean;
  onToggle?: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setMessages((m) => [...m, { role: "user", text, at: now }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const res = await api.plannerChat({
        run_id: runId, stage_id: stageId, source_id: sourceId, message: text,
      });
      const reply = res.reply ?? res.message ?? "(no reply)";
      setMessages((m) => [...m, { role: "assistant", text: String(reply), at: now }]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={onToggle}
        title={t("Open planner")}
        className="flex w-11 shrink-0 flex-col items-center gap-3 border-l border-line bg-surface py-4 hover:bg-surface-sunken"
      >
        <SparkIcon />
        <span className="text-[11px] font-medium tracking-wide text-ink-mute [writing-mode:vertical-rl]">
          {t("Planner")}
        </span>
      </button>
    );
  }

  return (
    <aside className={cx(
      "flex flex-col bg-surface",
      onToggle ? "w-[340px] shrink-0 border-l border-line" : "h-[560px] rounded-xl border border-line",
    )}>
      <header className="flex h-[52px] shrink-0 items-center gap-2 border-b border-line px-4">
        <SparkIcon />
        <span className="flex-1 text-sm font-semibold">
          {sourceId && !runId ? t("Ask about this data") : t("Planner / Orchestrator")}
        </span>
        {onToggle && (
        <button onClick={onToggle} className="rounded p-1 text-ink-faint hover:bg-surface-sunken hover:text-ink" title={t("Collapse")}>
          <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" />
          </svg>
        </button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
          {t("Remembered rules")}
        </p>
        <ul className="mb-4 space-y-1.5">
          {RULES.map((rule) => (
            <li key={rule} className="flex items-start gap-2 rounded-lg border border-line bg-surface-sunken px-2.5 py-1.5 text-xs text-ink-soft">
              <LockIcon />
              <span>{t(rule)}</span>
            </li>
          ))}
        </ul>

        {messages.length === 0 && (
          <p className="rounded-lg bg-brand-50 px-3 py-2.5 text-xs leading-relaxed text-ink-soft">
            {t("Ask about the current stage, request a different approach, or challenge a gate decision. The planner sees measured summaries — never raw rows.")}
          </p>
        )}

        <div className="space-y-2.5">
          {messages.map((m, i) => (
            <div key={i} className={cx("flex flex-col gap-0.5", m.role === "user" && "items-end")}>
              <span className="text-[10px] text-ink-faint">
                {m.role === "assistant" ? t("Assistant") : t("You")} · {m.at}
              </span>
              <p className={cx(
                "max-w-[92%] whitespace-pre-wrap rounded-xl px-3 py-2 text-xs leading-relaxed",
                m.role === "assistant" ? "bg-brand-50 text-ink-soft" : "bg-ink text-white",
              )}>
                {m.text}
              </p>
            </div>
          ))}
          {busy && <Spinner label={t("Planner is thinking…")} />}
          {error && <p className="rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
          <div ref={endRef} />
        </div>
      </div>

      <div className="shrink-0 border-t border-line p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(draft); }
            }}
            rows={2}
            placeholder={t("Ask the planner anything…")}
            className="field resize-none text-xs"
          />
          <button onClick={() => void send(draft)} disabled={busy || !draft.trim()} className="btn-primary h-9 w-9 !p-0" title={t("Send")}>
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M3 10h13m0 0-5-5m5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">
          {t("Planner uses workspace rules and context from this workflow.")}
        </p>
      </div>
    </aside>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M10 2.5 11.6 7 16 8.5 11.6 10 10 14.5 8.4 10 4 8.5 8.4 7z" strokeLinejoin="round" />
    </svg>
  );
}
function LockIcon() {
  return (
    <svg viewBox="0 0 20 20" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="4" y="8.5" width="12" height="8" rx="1.6" /><path d="M7 8.5V6a3 3 0 0 1 6 0v2.5" />
    </svg>
  );
}
