import { useMemo, useState } from "react";

import { api, type ProfiledColumn } from "../lib/api";
import { t } from "../lib/i18n";
import { useOverlayDismiss } from "./overlayDismiss";
import type { TargetColumnGroup } from "./targetColumns";
import { cx } from "./ui";

/** Naming the ML target yourself, on a surface big enough to read it on.
 *
 * #464: this was three `<select>`s and a button in one `flex-wrap` row, at the
 * bottom of the red failure box, inside a docked panel that defaults to 440px.
 * The row wrapped to several lines and column names clipped at 11.25rem --
 * while choosing a target is precisely the moment a person needs to read column
 * names and compare them. It takes the shape artifact previews already use,
 * because choosing what the model predicts deserves at least as much room as
 * reading an artifact does.
 *
 * The evidence per column is what the picker was missing rather than a
 * decoration: `is_usable_target` marks the plausible ones with a star, and the
 * measured shape, null rate and cardinality behind that mark are what let a
 * person disagree with it -- inference cannot tell a 0/1 label from a 0/1
 * quantity, and the person looking at their own data can.
 */
export function ProblemTargetDialog({
  runId, groups, onClose, onPinned,
}: {
  runId: string;
  groups: TargetColumnGroup[];
  onClose: () => void;
  /** The run is re-entering `problem_discovery`; the caller re-reads it. */
  onPinned: () => void;
}) {
  const panel = useOverlayDismiss<HTMLDivElement>(onClose);
  const columns = useMemo(() => groups.flatMap((group) => group.columns), [groups]);
  const [column, setColumn] = useState(
    () => (columns.find((item) => item.candidate_target) ?? columns[0])?.name ?? "",
  );
  // Empty means "read it off the column's measured shape", which is the right
  // default. It is offered because inference cannot tell a 0/1 label from a
  // 0/1 quantity and the person looking at their own data can.
  const [taskType, setTaskType] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pin() {
    if (busy || !column) return;
    setBusy(true); setError(null);
    try {
      await api.pinProblemFraming(runId, {
        kind: "predict_column",
        target_column: column,
        task_type: taskType || null,
      });
      onPinned();
      onClose();
    } catch (caught) {
      // Stays open on failure: an unknown column or a run that has moved on is
      // something to correct here, not a reason to lose the choice.
      setError(caught instanceof Error ? caught.message : String(caught));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true">
      <div ref={panel} className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-surface shadow-2xl">
        <div className="flex shrink-0 items-start gap-4 border-b border-line p-5">
          <div className="min-w-0 flex-1">
            <p className="text-3xs font-semibold uppercase tracking-wide text-brand-600">{t("Human decision")}</p>
            <h3 className="mt-1 text-lg font-semibold text-ink">{t("Name the problem yourself")}</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-mute">{t("This re-runs problem discovery on this run with your choice pinned. Intake, schema discovery and integration are kept.")}</p>
          </div>
          <button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close")} onClick={onClose}>×</button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
          {error && <p role="alert" className="mt-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
          {columns.length === 0 && <p className="mt-4 text-xs text-ink-mute">{t("This run has no profiled columns to choose from.")}</p>}
          {groups.map((group) => (
            <section key={group.table} className="mt-4">
              <p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{group.table}</p>
              <ul className="mt-2 space-y-1">
                {group.columns.map((item) => (
                  <li key={item.name}>
                    <label className={cx(
                      "flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors",
                      item.name === column ? "border-brand-400 bg-brand-50" : "border-line hover:bg-surface-sunken",
                    )}>
                      <input type="radio" name="ads-problem-target" value={item.name} checked={item.name === column} onChange={() => setColumn(item.name)} className="h-3.5 w-3.5 shrink-0" />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">{item.name}</span>
                      {item.candidate_target && <span className="shrink-0 rounded bg-brand-500/15 px-1.5 py-0.5 text-4xs font-semibold uppercase tracking-wide text-brand-700">{t("suggested")}</span>}
                      {/* The measurements behind the star, so a person can
                          disagree with it on evidence rather than on a hunch. */}
                      <ColumnEvidence column={item} />
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className="flex shrink-0 flex-wrap items-end justify-between gap-3 border-t border-line p-5">
          <label className="flex flex-col gap-1 text-3xs font-medium text-ink-mute">{t("Task type")}
            <select value={taskType} onChange={(event) => setTaskType(event.target.value)} className="rounded-lg border border-line bg-surface px-2 py-1.5 text-2xs font-medium text-ink outline-none">
              <option value="">{t("From the column's shape")}</option>
              <option value="regression">{t("Regression")}</option>
              <option value="binary_classification">{t("Binary classification")}</option>
              <option value="multiclass_classification">{t("Multiclass classification")}</option>
            </select>
          </label>
          <div className="flex gap-2">
            <button type="button" className="btn-ghost text-xs" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
            <button type="button" className="btn-primary text-xs" disabled={busy || !column} onClick={() => void pin()}>{busy ? t("Working…") : t("Re-run problem discovery")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Shape, null rate and cardinality -- what makes a column choosable. */
function ColumnEvidence({ column }: { column: ProfiledColumn }) {
  return <span className="shrink-0 text-3xs tabular-nums text-ink-faint">
    {t(column.semantic_type)}
    {" · "}
    {t("{percent}% null", { percent: (column.null_rate * 100).toFixed(1) })}
    {" · "}
    {column.is_unique ? t("unique") : t("{percent}% distinct", { percent: (column.unique_rate * 100).toFixed(1) })}
  </span>;
}
