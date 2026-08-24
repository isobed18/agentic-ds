/**
 * The intake stage, which is also the screen where a run is decided.
 *
 * This is the whole stage workspace rather than a tab inside it, and it is not
 * a dialog: choosing a dataset starts the run and stops it here, so by the time
 * this is on screen intake and schema discovery have really executed and every
 * number below was measured by the run itself. Nothing downstream has been
 * built on any of it yet, which is what makes this the one moment where
 * changing the PII call or the run's supervision is free.
 *
 * Pressing Run continues this same run. It does not start a second one.
 */
import { useState } from "react";
import { api, type ProfiledTable, type RunOptions } from "../lib/api";
import { t } from "../lib/i18n";
import { DataReview } from "./DataReview";
import { SensitivityOverride } from "./SensitivityOverride";
import { Badge, Spinner, cx } from "./ui";

export function IntakeStage({
  runId, sourceId, staged, staging, tables, options, onStarted, onDiscarded,
}: {
  runId: string;
  sourceId: string;
  /** Stopped after schema discovery and waiting to be configured. */
  staged: boolean;
  /** The first two stages are still executing. */
  staging: boolean;
  /** Data cards this run's intake produced; empty until it finishes. */
  tables: ProfiledTable[];
  options: RunOptions | null;
  onStarted: () => void;
  onDiscarded: () => void;
}) {
  const [runMode, setRunMode] = useState<"auto" | "manual">("auto");
  const [panelSize, setPanelSize] = useState(1);
  const [edaAgent, setEdaAgent] = useState(true);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState<"run" | "discard" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy("run");
    setError(null);
    try {
      await api.startStaged(runId, {
        run_mode: runMode,
        agent_panel_size: panelSize,
        eda_agent: edaAgent,
        target_column: target || undefined,
      });
      onStarted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function discard() {
    setBusy("discard");
    try {
      await api.discardStaged(runId);
      onDiscarded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  const columns = tables.flatMap((table) => table.columns.map((c) => c.name));

  return (
    <div className="space-y-4">
      {staging && (
        <div className="card flex items-center gap-3 border-l-4 border-l-brand-500 px-4 py-3">
          <Spinner label={t("Reading the data and its schema…")} />
        </div>
      )}

      {(staged || staging) && (
        <section className="card px-4 py-4">
          <div className="mb-3 flex flex-wrap items-baseline gap-2">
            <h3 className="text-sm font-semibold text-ink">{t("Run configuration")}</h3>
            <Badge tone="brand">{sourceId}</Badge>
            <p className="text-xs text-ink-mute">
              {t("Applied when the run continues. Nothing below is decided yet.")}
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            {/* Manual is not a second control path: it declares every stage a
                checkpoint, so it runs through the same gate as everything else
                and cannot weaken a hard rule. */}
            <Field label={t("Supervision")}>
              <div className="flex gap-1.5">
                {(["auto", "manual"] as const).map((value) => (
                  <button
                    key={value}
                    onClick={() => setRunMode(value)}
                    className={cx(
                      "flex-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                      runMode === value
                        ? "border-brand-500 bg-brand-50"
                        : "border-line hover:bg-surface-sunken",
                    )}
                  >
                    <span className="block font-semibold text-ink">
                      {value === "auto" ? t("Automatic") : t("Step by step")}
                    </span>
                    <span className="mt-0.5 block leading-snug text-ink-mute">
                      {value === "auto"
                        ? t("The gate stops the run only where its own signals demand it.")
                        : t("Every stage stops for your approval.")}
                    </span>
                  </button>
                ))}
              </div>
            </Field>

            <Field
              label={t("Target column")}
              hint={t("Leave empty to let the agent discover the problem from the data.")}
            >
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
              >
                <option value="">{t("Let the agent decide")}</option>
                {columns.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </Field>

            <Field
              label={t("Agent panel")}
              hint={t("Running a stage several times independently is what produces the agreement signal the gate uses.")}
            >
              <select
                value={panelSize}
                onChange={(e) => setPanelSize(Number(e.target.value))}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm"
              >
                {(options?.agent_panel_sizes ?? [1, 3]).map((size: number) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </Field>

            <Field label={t("Exploratory analysis")}>
              <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={edaAgent}
                  onChange={(e) => setEdaAgent(e.target.checked)}
                  className="h-4 w-4"
                />
                <span className="text-ink-soft">
                  {t("Let an agent write its own analysis on top of the fixed profile")}
                </span>
              </label>
            </Field>
          </div>

          {error && (
            <p className="mt-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>
          )}

          <div className="mt-4 flex items-center gap-2 border-t border-line-soft pt-3">
            <button
              onClick={() => void start()}
              disabled={busy !== null || staging}
              className="btn-primary text-sm disabled:opacity-50"
            >
              {busy === "run" ? t("Starting…") : t("Run")}
            </button>
            <button
              onClick={() => void discard()}
              disabled={busy !== null || staging}
              className="btn-ghost text-sm disabled:opacity-50"
            >
              {t("Discard")}
            </button>
            <span className="text-[11px] text-ink-faint">
              {staging
                ? t("Available once the first two stages finish.")
                : t("Continues this run; it does not start a second one.")}
            </span>
          </div>
        </section>
      )}

      {/* Offered here because the classification is on screen and nothing has
          been built on it: the columns marked personal are the ones kept out of
          the model's features. */}
      {tables.length > 0 && (
        <SensitivityOverride runId={runId} tables={tables} />
      )}

      <DataReview sourceId={sourceId} />
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-[10px] uppercase tracking-wide text-ink-faint">{label}</p>
      {children}
      {hint && <p className="mt-1 text-[11px] leading-snug text-ink-faint">{hint}</p>}
    </div>
  );
}
