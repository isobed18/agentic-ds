import { t } from "../lib/i18n";
/**
 * Explore more than one problem from the same data.
 *
 * A branch is a separate run, not a fork inside the engine. Once the problem
 * changes every later stage reasons from a different premise — a different
 * target, a different split, a different notion of leakage — so each branch
 * gets its own agents rather than sharing state that is no longer true for
 * both sides. What they share is the dataset and the launch configuration.
 *
 * The run this card sits in keeps whichever candidate it already chose; the
 * boxes here spawn the alternatives beside it.
 */
import { useState } from "react";
import { api, type RunRequest, type Story } from "../lib/api";
import { Badge, Spinner, cx } from "./ui";

type Choice = NonNullable<Story["choices"]>[number];

export function BranchPicker({
  choices, sourceId, parentRunId, onBranched,
}: {
  choices: Choice[];
  sourceId: string | null;
  parentRunId: string;
  onBranched: (runIds: string[]) => void;
}) {
  // The first candidate is the one this run already took, so it is not on offer.
  const alternatives = choices.slice(1).filter((c) => c.viable !== false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!alternatives.length || !sourceId) return null;

  function toggle(i: number) {
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  async function branch() {
    if (!picked.size || !sourceId) return;
    setBusy(true);
    setError(null);
    try {
      const created: string[] = [];
      for (const index of [...picked].sort()) {
        const c = alternatives[index];
        // Pinned, not proposed: the point of a branch is to force a specific
        // problem, so this run states its intent rather than rediscovering one.
        const body: RunRequest = {
          source_id: sourceId,
          mode: "agent",
          agent_panel_size: 1,
          base_table: "",
          base_grain: [],
          task_type: c.task,
          primary_metric: c.metric,
          target_column: c.target ?? null,
          problem_title: c.title,
          parent_run_id: parentRunId,
          branch_label: c.title,
        };
        created.push((await api.createRun(body)).run_id);
      }
      setPicked(new Set());
      onBranched(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card mb-4 px-4 py-4">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone="brand">{t("Branch")}</Badge>
        <h3 className="text-sm font-semibold text-ink">{t("Try another problem alongside this one")}</h3>
      </div>
      <p className="mb-3 text-xs leading-relaxed text-ink-mute">
        {t("Each one you pick starts its own run on the same data with its own agents. This run keeps the problem it already chose.")}
      </p>

      <div className="grid gap-2 md:grid-cols-2">
        {alternatives.map((c, i) => (
          <button
            key={i}
            onClick={() => toggle(i)}
            disabled={busy}
            className={cx(
              "rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-50",
              picked.has(i) ? "border-brand-500 bg-brand-50" : "border-line hover:bg-surface-sunken",
            )}
          >
            <span className="flex items-start gap-2">
              <span
                className={cx(
                  "mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded border text-3xs font-bold",
                  picked.has(i) ? "border-brand-600 bg-brand-600 text-white" : "border-line",
                )}
                aria-hidden
              >
                {picked.has(i) ? "✓" : ""}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink">{c.title}</span>
                <span className="mt-0.5 block text-2xs text-ink-mute">
                  {c.target ?? "—"} · {(c.task ?? "").replace(/_/g, " ")} · {c.metric ?? "—"}
                </span>
              </span>
            </span>
          </button>
        ))}
      </div>

      {error && <p className="mt-2 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}

      <div className="mt-3 flex items-center gap-2">
        {busy && <Spinner />}
        <button onClick={() => void branch()} disabled={!picked.size || busy} className="btn-primary text-xs">
          {picked.size ? t("Start {n} branches", { n: picked.size }) : t("Start branches")}
        </button>
      </div>
    </section>
  );
}
