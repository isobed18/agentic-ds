import { t } from "../lib/i18n";
/**
 * The human answer to a stage gate escalation.
 *
 * This was built inside StageWorkspace, which only ever rendered from the
 * unrouted Workflows page, so a run that stopped in `awaiting_human` had no
 * reachable screen to answer it and sat stuck indefinitely (#81). Extracted
 * here so the live AutomationWorkspace can surface it without pulling in the
 * whole deprecated StageWorkspace module.
 */
import { useState } from "react";
import { api, type GateDecision } from "../lib/api";
import { reasonLabel } from "../lib/status";
import { Badge, cx } from "./ui";

export function ApprovalCard({
  runId, decision, onAnswered,
}: { runId: string; decision: GateDecision; onAnswered: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  /**
   * A gate answer is accepted exactly once: the run leaves `awaiting_human`
   * immediately, so a second submission is rejected with 400. This card stays
   * mounted until the next poll notices, and re-enabling the buttons in that
   * window invited a second click that looked like a failure even though the
   * first answer had already been applied and the run had resumed.
   */
  const [sent, setSent] = useState(false);
  const prompt = decision.human_prompt!;

  async function answer(optionId: string) {
    if (sent || busy) return;
    setBusy(optionId);
    setError(null);
    try {
      await api.answer(runId, {
        stage_id: decision.stage_id,
        decision: optionId,
        instructions: note ? [note] : [],
      });
      setNote("");
      setSent(true);
      onAnswered();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  return (
    <section className="card mb-4 border-l-4 border-l-stop-500 px-4 py-4">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone={sent ? "ok" : "stop"}>
          {sent ? t("Answer sent") : t("Approval required")}
        </Badge>
        <span className="text-xs text-ink-mute">{reasonLabel(decision.reason_code)}</span>
      </div>
      {sent && (
        <p className="mb-2 text-xs text-ok-700">
          {t("Recorded. The run is resuming — this card clears on the next refresh.")}
        </p>
      )}
      <h3 className="text-sm font-semibold text-ink">{prompt.question}</h3>
      <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-ink-mute">{prompt.context_summary}</p>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {prompt.options.map((o) => (
          <button
            key={o.option_id}
            onClick={() => void answer(o.option_id)}
            disabled={sent || busy !== null}
            className={cx(
              "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-50",
              o.recommended ? "border-brand-500 bg-brand-50 hover:bg-brand-100" : "border-line bg-surface hover:bg-surface-sunken",
            )}
          >
            <span className="flex items-baseline gap-1.5">
              <span className="text-sm font-medium text-ink">{o.label}</span>
              {o.recommended && (
                <span className="rounded bg-brand-500/15 px-1 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-brand-700">
                  {t("suggested")}
                </span>
              )}
            </span>
            <span className="mt-0.5 block text-[11px] leading-snug text-ink-mute">{o.consequence}</span>
            {o.downstream_effect && <span className="mt-1 block text-[11px] text-warn-700">{o.downstream_effect}</span>}
          </button>
        ))}
      </div>

      {prompt.allows_free_text !== false && (
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("Optional instructions to attach to a rework…")}
          className="field mt-2.5 text-xs"
        />
      )}
      {error && <p className="mt-2 text-xs text-stop-700">{error}</p>}
    </section>
  );
}
