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

/**
 * The gate card was the one screen that stayed English while the rest of the
 * product was Turkish (#192).
 *
 * Its wording is built as English literals on the server
 * (`ads/gates/evaluator.py`) and the card printed those strings raw. The server
 * cannot translate them either: the prompt is built inside the run's worker,
 * with no request and therefore no active language.
 *
 * So the card translates from what is machine-readable rather than from prose
 * -- `option_id` and `question_kind`, both stable -- and falls back to the
 * server's own text for anything it does not recognise. An option added on the
 * server still renders; it just renders in English until a key is added here.
 */
function optionText(option: { option_id: string; label: string; consequence: string }, stageId: string) {
  switch (option.option_id) {
    case "approve":
      return {
        label: t("Approve and continue"),
        consequence: t("Accept this stage's output as-is and proceed to the next stage."),
        downstream: undefined as string | undefined,
      };
    case "retry":
      return {
        label: t("Send back for rework"),
        consequence: t("Re-run this stage with your instructions attached."),
        downstream: t("Re-runs {stage} and everything after it.", { stage: stageId }),
      };
    case "abort":
      return {
        label: t("Stop the run"),
        consequence: t("Halt here. Completed artifacts are kept and the run can be resumed."),
        downstream: undefined as string | undefined,
      };
    case "review_first":
      return {
        label: t("Inspect artifacts before deciding"),
        consequence: t(
          "Pause without committing. This stage discards work that cannot be rebuilt automatically.",
        ),
        downstream: undefined as string | undefined,
      };
    default:
      return { label: option.label, consequence: option.consequence, downstream: undefined };
  }
}

function questionText(prompt: { question: string; question_kind?: string; stage_id: string }): string {
  if (prompt.question_kind === "checkpoint") {
    return t(
      "Stage {stage} is a required checkpoint. Review the output and choose how to proceed.",
      { stage: prompt.stage_id },
    );
  }
  if (prompt.question_kind === "no_output") {
    return t(
      "Stage {stage} produced nothing, so there is no output to approve. Send it back for rework, or stop the run.",
      { stage: prompt.stage_id },
    );
  }
  if (prompt.question_kind === "problem") {
    return t("Stage {stage} stopped because a problem was detected. Your decision is needed.", {
      stage: prompt.stage_id,
    });
  }
  return prompt.question;
}

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
  const suspectColumns = prompt.leakage_suspect_columns ?? [];
  const targetColumns = new Set(prompt.leakage_target_columns ?? []);
  // #262: default-checked to match what the automatic retry would have
  // dropped -- the human is confirming a mechanical fix, not starting from
  // a blank slate.
  const [dropColumns, setDropColumns] = useState<Set<string>>(() => new Set(suspectColumns));

  function toggleDropColumn(column: string) {
    setDropColumns((prev) => {
      const next = new Set(prev);
      if (next.has(column)) next.delete(column);
      else next.add(column);
      return next;
    });
  }

  async function answer(optionId: string) {
    if (sent || busy) return;
    setBusy(optionId);
    setError(null);
    try {
      // #262: a checked leakage suspect becomes the exact `drop_feature:
      // <column>` syntax `leakage_audit_stage` understands -- the same format
      // the automatic retry already generates -- rather than relying on a
      // human to type it, or an LLM to interpret free text that the
      // deterministic stage cannot read. The free-text note, if any, still
      // rides along as an extra instruction.
      const columnInstructions = suspectColumns
        .filter((column) => dropColumns.has(column))
        .map((column) => `drop_feature: ${column}  # ${targetColumns.has(column) ? "target leakage" : "blocking leakage"}`);
      const instructions = note ? [...columnInstructions, note] : columnInstructions;
      await api.answer(runId, {
        stage_id: decision.stage_id,
        decision: optionId,
        instructions,
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
        {/* #191: a rework can legitimately re-escalate the same stage, and the
            old card reappearing looked exactly like the stale one that used to
            come back forever. The attempt says which escalation this is, so a
            genuine loop is distinguishable from a card that never left. */}
        {decision.attempt > 1 && (
          <span className="text-xs text-ink-faint">
            {t("Attempt {count}", { count: decision.attempt })}
          </span>
        )}
      </div>
      {sent && (
        <p className="mb-2 text-xs text-ok-700">
          {t("Recorded. The run is resuming — this card clears on the next refresh.")}
        </p>
      )}
      <h3 className="text-sm font-semibold text-ink">{questionText(prompt)}</h3>
      {/* #259: `context_summary` is raw English prose (boilerplate + per-rule
          detail, joined) built server-side with no active language. When the
          server also sent the additive `reason_codes` field, translate: the
          boilerplate via `context_note` (a small finite set already in the
          client catalogue, translated the same way GuidedPipeline.tsx
          translates a stage's description) and each reason via the same
          `reasonLabel` the badge above already uses. A run recorded before
          this change has no `reason_codes` and falls back to the untranslated
          `context_summary` it always had. */}
      {prompt.reason_codes && prompt.reason_codes.length > 0 ? (
        <>
          {prompt.context_note && (
            <p className="mt-1 break-words text-xs leading-relaxed text-ink-mute">{t(prompt.context_note)}</p>
          )}
          <div className="mt-2">
            <p className="text-xs font-medium text-ink-mute">{t("Why this stopped:")}</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">
              {prompt.reason_codes.map((code) => (
                <li key={code} className="break-words text-xs leading-relaxed text-ink-mute">{reasonLabel(code)}</li>
              ))}
            </ul>
          </div>
        </>
      ) : (
        prompt.context_summary && (
          <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-ink-mute">{prompt.context_summary}</p>
        )
      )}

      {suspectColumns.length > 0 && (
        <div className="mt-3 rounded-lg border border-line bg-surface-sunken px-3 py-2.5">
          <p className="text-[11px] font-semibold text-ink">{t("Columns to drop")}</p>
          <ul className="mt-1.5 space-y-1">
            {suspectColumns.map((column) => (
              <li key={column}>
                <label className="flex items-center gap-2 text-xs text-ink-mute">
                  <input
                    type="checkbox"
                    checked={dropColumns.has(column)}
                    disabled={sent || busy !== null}
                    onChange={() => toggleDropColumn(column)}
                  />
                  <span className="font-mono text-ink">{column}</span>
                  <span className="text-[10px] text-ink-faint">
                    {targetColumns.has(column) ? t("target leakage") : t("blocking leakage")}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {prompt.options.map((o) => {
          const metin = optionText(o, prompt.stage_id);
          return (
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
              <span className="text-sm font-medium text-ink">{metin.label}</span>
              {o.recommended && (
                <span className="rounded bg-brand-500/15 px-1 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide text-brand-700">
                  {t("suggested")}
                </span>
              )}
            </span>
            <span className="mt-0.5 block text-[11px] leading-snug text-ink-mute">{metin.consequence}</span>
            {o.downstream_effect && (
              <span className="mt-1 block text-[11px] text-warn-700">
                {metin.downstream ?? o.downstream_effect}
              </span>
            )}
          </button>
          );
        })}
      </div>

      {prompt.allows_free_text !== false && (
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("Optional instructions to attach to a rework…")}
          className="field mt-2.5 text-xs"
        />
      )}
      {error && <p className="mt-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
    </section>
  );
}
