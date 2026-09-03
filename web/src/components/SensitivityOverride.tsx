/**
 * Correct the machine's PII call before it is used.
 *
 * The classifier decides which columns are dropped from the feature pool. It is
 * a heuristic and it is wrong in both directions — it misses personal columns
 * whose names it does not recognise, and it flags derived counters that merely
 * mention a personal word. The person reading this screen usually knows which
 * is which, and until now had no way to say so.
 *
 * Shown at intake in manual mode: the run has stopped, the classification is
 * on screen, and nothing downstream has been built on it yet.
 */
import { useState } from "react";
import { api, type ProfiledTable } from "../lib/api";
import { t } from "../lib/i18n";
import { Badge, Spinner, cx } from "./ui";

type Choice = "pii" | "internal";

export function SensitivityOverride({
  runId, tables, onApplied,
}: {
  runId: string;
  tables: ProfiledTable[];
  onApplied?: (applied: Record<string, Choice>) => void;
}) {
  const [changes, setChanges] = useState<Record<string, Choice>>({});
  // The source profile prop is an immutable staging snapshot. Keep successful
  // human corrections as the local baseline so the selected button and the
  // success message agree immediately with what the backend will use next.
  const [applied, setApplied] = useState<Record<string, Choice>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const columns = tables.flatMap((table) =>
    table.columns.map((column) => ({ table: table.name, ...column })),
  );
  if (!columns.length) return null;

  function baseline(name: string, machine: string): Choice {
    return applied[name] ?? (machine === "pii" ? "pii" : "internal");
  }

  function current(name: string, machine: string): Choice {
    return changes[name] ?? baseline(name, machine);
  }

  function set(name: string, value: Choice) {
    setSaved(false);
    setChanges((prev) => ({ ...prev, [name]: value }));
  }

  async function apply() {
    if (!Object.keys(changes).length) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.overrideSensitivity(runId, changes);
      setApplied((previous) => ({ ...previous, ...result.applied }));
      setChanges({});
      setSaved(true);
      onApplied?.(result.applied);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const pending = Object.entries(changes).filter(
    ([name, value]) =>
      value !== baseline(name, columns.find((c) => c.name === name)?.sensitivity ?? "internal"),
  ).length;

  return (
    <section className="card mb-4 px-4 py-4">
      <div className="mb-1 flex items-center gap-2">
        <Badge tone="warn">{t("Personal data")}</Badge>
        <h3 className="text-sm font-semibold text-ink">{t("Check what was marked personal")}</h3>
      </div>
      <p className="mb-3 max-w-2xl text-xs leading-relaxed text-ink-mute">
        {t("Columns marked personal are kept out of the model. The classifier is a heuristic and gets both directions wrong — correct it here before the run builds on it.")}
      </p>

      <div className="max-h-72 overflow-y-auto rounded-lg border border-line">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-sunken">
            <tr className="text-3xs uppercase tracking-wide text-ink-faint">
              <th className="px-3 py-2 text-left font-medium">{t("Column")}</th>
              <th className="px-3 py-2 text-left font-medium">{t("Kind")}</th>
              <th className="px-3 py-2 text-right font-medium">{t("Distinct")}</th>
              <th className="px-3 py-2 text-right font-medium" />
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => {
              const choice = current(column.name, column.sensitivity);
              const changed = choice !== (column.sensitivity === "pii" ? "pii" : "internal");
              return (
                <tr key={`${column.table}.${column.name}`} className="border-t border-line-soft">
                  <td className="px-3 py-1.5">
                    <span className="font-medium text-ink">{column.name}</span>
                    <span className="ml-1.5 text-3xs text-ink-faint">{column.table}</span>
                    {changed && <Badge tone="brand">{t("changed")}</Badge>}
                  </td>
                  <td className="px-3 py-1.5 text-ink-mute">
                    {column.semantic_type.replace(/_/g, " ")}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-ink-soft">
                    {(column.unique_rate * 100).toFixed(0)}%
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <span className="inline-flex rounded-md border border-line p-0.5">
                      {(["internal", "pii"] as Choice[]).map((value) => (
                        <button
                          key={value}
                          onClick={() => set(column.name, value)}
                          className={cx(
                            "rounded px-2 py-0.5 text-2xs font-medium transition-colors",
                            choice === value
                              ? value === "pii"
                                ? "bg-warn-500 text-white"
                                : "bg-brand-600 text-white"
                              : "text-ink-mute hover:bg-surface-sunken",
                          )}
                        >
                          {value === "pii" ? t("personal") : t("usable")}
                        </button>
                      ))}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {error && <p className="mt-2 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}

      <div className="mt-3 flex items-center gap-2">
        {busy && <Spinner />}
        <button onClick={() => void apply()} disabled={!pending || busy} className="btn-primary text-xs">
          {pending ? t("Apply {n} changes", { n: pending }) : t("No changes")}
        </button>
        {saved && <span className="text-xs text-ok-700">{t("Applied. Continue when ready.")}</span>}
      </div>
    </section>
  );
}
