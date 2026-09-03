/**
 * Planner / Orchestrator side panel.
 *
 * Collapsible, because the brief is explicit that configuration and chat should
 * not permanently occupy the screen. Remembered rules sit above the transcript
 * so the standing constraints are visible without scrolling.
 */
import { useEffect, useRef, useState } from "react";
import { api, type PlannerOverrideProposal, type ToolActivityEvent } from "../lib/api";
import { t } from "../lib/i18n";
import { stageName } from "./PipelineRail";
import { mergeToolActivity, toolActivityLine } from "./toolActivity";
import { Badge, Spinner, cx } from "./ui";

/** #411: "tool" is not a chat role -- nobody said it and there is nothing to
 *  reply to. It is an interstitial status line, kept in the same list so it
 *  lands in the transcript in the order it happened rather than in a second
 *  column beside it, and rendered as light italic text rather than a bubble. */
interface Message { role: "assistant" | "user" | "tool"; text: string; at: string }
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

/** What a proposal would change, as sentences a reader can check.
 *
 * #449: asking the planner for a review checkpoint produced a line reading
 * "eda → İnsan onayı" among the configuration patches -- an arrow between a raw
 * stage id and a label, in a list whose other rows are JSON. A person who typed
 * "eda kısmına human approval koy" was looking for confirmation that the
 * checkpoint had been understood, and this did not read as one.
 *
 * The supervision rows say what will happen and name the stage the way the rest
 * of the product names it. `configuration_patch` keeps the arrow, because a
 * key/value patch is what it is.
 */
function overrideItems(proposal: PlannerOverrideProposal | null): string[] {
  if (!proposal) return [];
  return [
    ...Object.entries(proposal.configuration_patch).map(([key, value]) => `${key} → ${JSON.stringify(value)}`),
    ...Object.entries(proposal.stage_directives).flatMap(([stage, values]) => values.map((value) => `${stageName(stage)} → ${value}`)),
    ...proposal.checkpoint_stages.map((stage) => t("Review checkpoint after {stage}", { stage: stageName(stage) })),
    ...proposal.auto_proceed_stages.map((stage) => t("Proceed automatically after {stage}", { stage: stageName(stage) })),
    ...Object.entries(proposal.max_retries_by_stage).map(([stage, value]) => t("Retry {stage} up to {count} times", { stage: stageName(stage), count: value })),
    ...(proposal.pipeline_blueprint ? [t("Pipeline graph revision {revision}", { revision: proposal.pipeline_blueprint.revision ?? 1 })] : []),
  ];
}

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
  // #449: applying cleared the list and left one transient word, so the panel's
  // answer to "did it take the checkpoint?" was "Override applied" and nothing
  // about what was in it. The applied items stay on screen.
  const [applied, setApplied] = useState<string[]>([]);
  const [pendingOverride, setPendingOverride] = useState<PlannerOverrideProposal | null>(null);
  const [overrideOutcome, setOverrideOutcome] = useState<string | null>(null);
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
    if (!runId) { setMessages([]); setRecommendations([]); setApplied([]); setPendingOverride(null); setOverrideOutcome(null); setProblemRecommendations([]); setGraphEditRejected(null); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loadWorkspace = async () => {
      try {
        const workspace = await api.stagingWorkspace(runId);
        if (cancelled) return;
        // #411: the saved transcript replaces the chat turns, but the live
        // tool lines are not in it and are not the server's to return. They
        // are kept, after the history, because that is when they happened.
        const saved: Message[] = workspace.chat_history.map((item) => ({
          role: (item.role === "planner" ? "assistant" : "user") as Message["role"],
          text: item.content.en,
          at: t("saved"),
        }));
        setMessages((current) => [...saved, ...current.filter((item) => item.role === "tool")]);
        const pending = workspace.pending_override ?? null;
        setPendingOverride(pending);
        setRecommendations(overrideItems(pending));
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

  /** #411: a live line each time an agent uses a tool.
   *
   * Tool use only ever reached the screen after the fact, as a "Tool calls: 7"
   * fact on a finished stage's artifact -- never while it was happening, and
   * never naming the tool. The broker publishes every call to an in-process
   * feed as it makes it; this reads what is new since the last cursor and
   * appends it to the transcript.
   *
   * Polling rather than streaming: the run already reports itself by polling,
   * the feed is an in-memory read, and a dropped connection on a long-lived
   * stream is a reconnection problem this does not need to have. `active` in
   * the answer is the stop signal, so a finished run's panel goes quiet
   * instead of asking forever.
   */
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // The cursor advances only after the events behind it are on screen, so a
    // failed request repeats a range rather than skipping it; `shown` is what
    // makes that repeat harmless.
    let cursor = 0;
    let shown: ToolActivityEvent[] = [];
    const poll = async () => {
      let keepGoing = true;
      try {
        const feed = await api.toolActivity(runId, cursor);
        if (cancelled) return;
        const { events, added } = mergeToolActivity(shown, feed.events);
        shown = events;
        if (added.length > 0) {
          const at = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          setMessages((current) => [
            ...current,
            ...added.map((event) => ({ role: "tool" as const, text: toolActivityLine(event), at })),
          ]);
        }
        cursor = feed.cursor;
        keepGoing = feed.active;
      } catch {
        // A blip is not a reason to stop narrating a run that is still going.
        keepGoing = true;
      }
      if (!cancelled && keepGoing) timer = setTimeout(() => { void poll(); }, 2000);
    };
    void poll();
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
    setOverrideOutcome(null);
    try {
      const res = await api.plannerChat({
        run_id: runId, stage_id: stageId, source_id: sourceId, message: text,
      });
      const reply = res.reply ?? res.message ?? "(no reply)";
      // #246: surface a refused graph edit rather than dropping it. Without
      // this the reply's prose ("moving on to the ML stage") is all the person
      // sees, while the edit that would carry the run there was rejected.
      setGraphEditRejected(typeof res.graph_edit_rejected === "string" ? res.graph_edit_rejected : null);
      const pending = res.override_proposal ?? null;
      setPendingOverride(pending);
      setRecommendations(overrideItems(pending));
      if (pending) setApplied([]);
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
      setMessages((m) => [...m, { role: "assistant", text: String(reply), at: now }]);
      if (runId) {
        const workspace = await api.stagingWorkspace(runId).catch(() => null);
        if (workspace) onWorkspaceUpdated?.(workspace);
        const savedPending = workspace?.pending_override ?? pending;
        setPendingOverride(savedPending);
        setRecommendations(overrideItems(savedPending));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function resolveOverride(action: "apply" | "discard") {
    if (!runId || !pendingOverride || busy) return;
    setBusy(true);
    setError(null);
    try {
      const settled = overrideItems(pendingOverride);
      const workspace = action === "apply"
        ? await api.applyPlannerOverride(runId, pendingOverride.proposal_id)
        : await api.discardPlannerOverride(runId, pendingOverride.proposal_id);
      setPendingOverride(workspace.pending_override ?? null);
      setRecommendations(overrideItems(workspace.pending_override ?? null));
      setApplied(action === "apply" ? settled : []);
      setOverrideOutcome(t(action === "apply" ? "Override applied" : "Override discarded"));
      onWorkspaceUpdated?.(workspace);
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
        <span className="text-2xs font-medium tracking-wide text-ink-mute [writing-mode:vertical-rl]">
          {t("Planner")}
        </span>
      </button>
    );
  }

  return (
    <aside className={cx(
      "flex flex-col bg-surface",
      onToggle ? "w-[21.25rem] shrink-0 border-l border-line" : "h-[35rem] rounded-xl border border-line",
    )}>
      <header className="flex h-[3.25rem] shrink-0 items-center gap-2 border-b border-line px-4">
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
        <p className="mb-2 text-2xs font-semibold uppercase tracking-wide text-ink-faint">
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
              <p className="flex-1 text-2xs font-semibold text-warn-800">{t("The planner's pipeline change was not applied")}</p>
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
            <p className="text-3xs leading-relaxed text-warn-800">{graphEditRejected}</p>
          </section>
        )}

        {overrideOutcome && <p className="mb-4 rounded-lg border border-ok-200 bg-ok-50 px-3 py-2 text-2xs font-semibold text-ok-700" role="status">{overrideOutcome}</p>}

        {/* #449: what the last apply actually put into effect. Without it the
            only trace of an accepted checkpoint was the word "applied", and the
            person who asked for one had nothing to check their request
            against. Superseded by the next proposal, cleared by a discard. */}
        {!pendingOverride && applied.length > 0 && (
          <section className="mb-4 rounded-lg border border-ok-200 bg-ok-50 px-3 py-2.5">
            <p className="mb-2 text-2xs font-semibold text-ok-700">{t("Applied to this run")}</p>
            <ul className="space-y-1">
              {applied.map((item) => <li key={item} className="text-3xs leading-relaxed text-ink-soft">· {item}</li>)}
            </ul>
          </section>
        )}
        {pendingOverride && recommendations.length > 0 && (
          <section className="mb-4 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2.5">
            <div className="mb-2 flex items-center gap-2">
              <p className="text-2xs font-semibold text-ink">{t("Proposed pipeline override")}</p>
              <Badge tone="warn">{t("Awaiting your approval")}</Badge>
            </div>
            <ul className="space-y-1">
              {recommendations.map((item) => <li key={item} className="text-3xs leading-relaxed text-ink-soft">· {item}</li>)}
            </ul>
            <div className="mt-3 flex justify-end gap-2 border-t border-brand-100 pt-3">
              <button type="button" className="btn-ghost !py-1.5 text-xs" disabled={busy} onClick={() => void resolveOverride("discard")}>{t("Cancel")}</button>
              <button type="button" className="btn-primary !py-1.5 text-xs" disabled={busy} onClick={() => void resolveOverride("apply")}>{busy ? t("Applying…") : t("Apply override")}</button>
            </div>
          </section>
        )}

        {problemRecommendations.length > 0 && (
          <section className="mb-4 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2.5">
            <p className="mb-2 text-2xs font-semibold text-ink">{t("Ranked ML opportunities")}</p>
            <ol className="space-y-2">
              {problemRecommendations.map((item) => (
                <li key={`${item.rank}:${item.target_column}`} className="rounded-lg bg-surface px-2.5 py-2 text-3xs text-ink-soft">
                  <div className="flex items-start gap-2"><Badge tone="brand">#{item.rank}</Badge><div className="min-w-0"><p className="font-semibold text-ink">{item.problem_title}</p><p className="font-mono text-4xs text-ink-mute">{item.target_column} · {item.task_type} · {item.primary_metric}</p></div></div>
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
                    className="rounded-full border border-line bg-surface px-2.5 py-1 text-left text-2xs text-ink-soft hover:border-brand-200 hover:bg-brand-50"
                  >
                    {t(prompt)}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="space-y-2.5">
          {messages.map((m, i) => (m.role === "tool" ? (
            // No speaker line and no bubble: this is the run narrating itself,
            // not a turn in the conversation.
            <p key={i} className="px-1 text-3xs italic leading-relaxed text-ink-faint">{m.text}</p>
          ) : (
            <div key={i} className={cx("flex flex-col gap-0.5", m.role === "user" && "items-end")}>
              <span className="text-3xs text-ink-faint">
                {m.role === "assistant" ? t("Assistant") : t("You")} · {m.at}
              </span>
              <p className={cx(
                "max-w-[92%] whitespace-pre-wrap rounded-xl px-3 py-2 text-xs leading-relaxed",
                m.role === "assistant" ? "bg-brand-50 text-ink-soft" : "bg-ink text-white",
              )}>
                {m.text}
              </p>
            </div>
          )))}
          {busy && <Spinner label={t("Planner is thinking…")} />}
          {error && <p className="rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
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
        <p className="mt-2 text-3xs leading-relaxed text-ink-faint">
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
