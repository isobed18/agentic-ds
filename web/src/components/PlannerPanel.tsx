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
import { Badge, Spinner, cx } from "./ui";

interface Message { role: "assistant" | "user"; text: string; at: string }
interface ProblemRecommendation {
  rank: number;
  problem_title: string;
  target_column: string;
  task_type: string;
  primary_metric: string;
  evidence: string[];
  caveats: string[];
}

const RULES = [
  "Never expose raw rows",
  "Ask before a high-impact choice",
  "Keep answer leakage blocked",
  "Retry weak analysis once, then escalate",
];

export function PlannerPanel({
  runId = null, stageId = null, sourceId = null, open = true, onToggle,
  starterPrompts = [], onWorkspaceUpdated,
}: {
  runId?: string | null;
  stageId?: string | null;
  /** Ask about a dataset before any run exists; the planner gets its profile. */
  sourceId?: string | null;
  open?: boolean;
  onToggle?: () => void;
  /** Short, visible questions for someone who does not yet know the dataset. */
  starterPrompts?: string[];
  onWorkspaceUpdated?: (workspace: Awaited<ReturnType<typeof api.stagingWorkspace>>) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [problemRecommendations, setProblemRecommendations] = useState<ProblemRecommendation[]>([]);
  // #246: a planner graph edit the runner would refuse is dropped server-side
  // (#231) with the reason in `graph_edit_rejected`. The panel used to ignore
  // it, so the planner's reply could say it was moving the run to the ML stage
  // while the edit that would have done so was silently discarded and nothing
  // appeared. Hold the reason so the refusal is shown, never silent.
  const [graphEditRejected, setGraphEditRejected] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => {
    if (!runId) { setMessages([]); setRecommendations([]); setProblemRecommendations([]); setGraphEditRejected(null); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loadWorkspace = async () => {
      try {
        const workspace = await api.stagingWorkspace(runId);
        if (cancelled) return;
        setMessages(workspace.chat_history.map((item) => ({
          role: (item.role === "planner" ? "assistant" : "user") as Message["role"],
          text: item.content.en,
          at: t("saved"),
        })));
        const plan = workspace.recommended_plan;
        if (!plan) { setRecommendations([]); return; }
        setRecommendations([
          ...Object.entries(plan.configuration).map(([key, value]) => `${key} → ${JSON.stringify(value)}`),
          ...Object.entries(plan.stage_directives).flatMap(([stage, values]) => values.map((value) => `${stage} → ${value}`)),
          ...Object.entries(plan.max_retries_by_stage).map(([stage, value]) => `${stage} retries → ${value}`),
        ]);
      } catch {
        // Intake and schema discovery may still be running. Retry until the
        // durable staging snapshot exists, then stop polling.
        if (!cancelled) timer = setTimeout(() => { void loadWorkspace(); }, 2500);
      }
    };
    void loadWorkspace();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

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
      // #246: surface a refused graph edit rather than dropping it. Without
      // this the reply's prose ("moving on to the ML stage") is all the person
      // sees, while the edit that would carry the run there was rejected.
      setGraphEditRejected(typeof res.graph_edit_rejected === "string" ? res.graph_edit_rejected : null);
      const proposed: string[] = [];
      const configuration = res.configuration_patch;
      if (configuration && typeof configuration === "object" && !Array.isArray(configuration)) {
        for (const [key, value] of Object.entries(configuration)) {
          proposed.push(`${key} → ${JSON.stringify(value)}`);
        }
      }
      const directives = res.stage_directives;
      if (directives && typeof directives === "object" && !Array.isArray(directives)) {
        for (const [stage, values] of Object.entries(directives)) {
          if (Array.isArray(values)) values.forEach((value) => proposed.push(`${stage} → ${String(value)}`));
        }
      }
      const retries = res.max_retries_by_stage;
      if (retries && typeof retries === "object" && !Array.isArray(retries)) {
        for (const [stage, value] of Object.entries(retries)) proposed.push(`${stage} retries → ${String(value)}`);
      }
      const ranked = Array.isArray(res.problem_recommendations)
        ? res.problem_recommendations
            .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
            .map((item) => ({
              rank: Number(item.rank),
              problem_title: String(item.problem_title ?? ""),
              target_column: String(item.target_column ?? ""),
              task_type: String(item.task_type ?? ""),
              primary_metric: String(item.primary_metric ?? ""),
              evidence: Array.isArray(item.evidence) ? item.evidence.map(String) : [],
              caveats: Array.isArray(item.caveats) ? item.caveats.map(String) : [],
            }))
            .filter((item) => item.target_column && item.problem_title)
        : [];
      setProblemRecommendations(ranked);
      setRecommendations(proposed);
      setMessages((m) => [...m, { role: "assistant", text: String(reply), at: now }]);
      if (runId) {
        const workspace = await api.stagingWorkspace(runId).catch(() => null);
        if (workspace) onWorkspaceUpdated?.(workspace);
        const plan = workspace?.recommended_plan;
        if (plan) {
          setRecommendations([
            ...Object.entries(plan.configuration).map(([key, value]) => `${key} → ${JSON.stringify(value)}`),
            ...Object.entries(plan.stage_directives).flatMap(([stage, values]) => values.map((value) => `${stage} → ${value}`)),
            ...Object.entries(plan.max_retries_by_stage).map(([stage, value]) => `${stage} retries → ${value}`),
          ]);
        }
      }
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

        {graphEditRejected && (
          <section className="mb-4 rounded-lg border border-warn-300 bg-warn-50 px-3 py-2.5" role="alert">
            <div className="mb-1 flex items-start gap-2">
              <p className="flex-1 text-[11px] font-semibold text-warn-800">{t("The planner's pipeline change was not applied")}</p>
              <button
                onClick={() => setGraphEditRejected(null)}
                className="rounded p-0.5 text-warn-800/70 hover:bg-warn-100 hover:text-warn-800"
                title={t("Dismiss")}
              >
                <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <p className="text-[10px] leading-relaxed text-warn-800">{graphEditRejected}</p>
          </section>
        )}

        {recommendations.length > 0 && (
          <section className="mb-4 rounded-lg border border-brand-100 bg-brand-50 px-3 py-2.5">
            <div className="mb-2 flex items-center gap-2">
              <p className="text-[11px] font-semibold text-ink">{t("Recommended pipeline overrides")}</p>
              <Badge tone="brand">{t(runId ? "review / applied where possible" : "recommended, not applied")}</Badge>
            </div>
            <ul className="space-y-1">
              {recommendations.map((item) => <li key={item} className="text-[10px] leading-relaxed text-ink-soft">· {item}</li>)}
            </ul>
          </section>
        )}

        {problemRecommendations.length > 0 && (
          <section className="mb-4 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2.5">
            <p className="mb-2 text-[11px] font-semibold text-ink">{t("Ranked ML opportunities")}</p>
            <ol className="space-y-2">
              {problemRecommendations.map((item) => (
                <li key={`${item.rank}:${item.target_column}`} className="rounded-lg bg-surface px-2.5 py-2 text-[10px] text-ink-soft">
                  <div className="flex items-start gap-2"><Badge tone="brand">#{item.rank}</Badge><div className="min-w-0"><p className="font-semibold text-ink">{item.problem_title}</p><p className="font-mono text-[9px] text-ink-mute">{item.target_column} · {item.task_type} · {item.primary_metric}</p></div></div>
                  <ul className="mt-2 space-y-1">{item.evidence.map((line) => <li key={line}>· {line}</li>)}</ul>
                  {item.caveats.map((line) => <p key={line} className="mt-1 text-warn-700">! {line}</p>)}
                </li>
              ))}
            </ol>
          </section>
        )}

        {messages.length === 0 && (
          <div className="space-y-2.5">
            <p className="rounded-lg bg-brand-50 px-3 py-2.5 text-xs leading-relaxed text-ink-soft">
              {sourceId
                ? t("Ask what these files describe, how documents and tables connect, what may be unreliable, or what the source could answer. The local planner sees measured summaries and bounded PDF excerpts — never raw table rows.")
                : t("Ask about columns, missingness, relationships, target candidates, ML problems, or the current stage. The planner sees measured summaries — never raw rows.")}
            </p>
            {starterPrompts.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {starterPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => void send(prompt)}
                    className="rounded-full border border-line bg-surface px-2.5 py-1 text-left text-[11px] text-ink-soft hover:border-brand-200 hover:bg-brand-50"
                  >
                    {t(prompt)}
                  </button>
                ))}
              </div>
            )}
          </div>
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
