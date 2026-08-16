/**
 * Configure and start a run.
 *
 * Every choice offered here is read from `/api/run-options` and the selected
 * source's profile — the task types, metrics, split strategies and panel sizes
 * are the backend's closed vocabularies, not a list retyped in the frontend
 * that could drift out of agreement with what the API accepts.
 *
 * The panel-size control is the reason this dialog exists. Running the same
 * stage across several independent samples is what produces the agreement
 * signal the gate uses to detect a genuinely ambiguous decision, and there was
 * previously no way to ask for it.
 */
import { useEffect, useMemo, useState } from "react";
import {
  api,
  type ProfiledTable,
  type RunOptions,
  type RunRequest,
  type SourceProfile,
} from "../lib/api";
import { Badge, Spinner, cx } from "./ui";

const titleize = (s: string) => s.replace(/_/g, " ");

export function LaunchDialog({
  onClose, onLaunched,
}: { onClose: () => void; onLaunched: (runId: string) => void }) {
  const [options, setOptions] = useState<RunOptions | null>(null);
  const [sources, setSources] = useState<{ source_id: string; label: string }[]>([]);
  const [profile, setProfile] = useState<SourceProfile | null>(null);

  const [sourceId, setSourceId] = useState("");
  const [mode, setMode] = useState("agent");
  const [panelSize, setPanelSize] = useState(1);
  const [baseTable, setBaseTable] = useState("");
  const [grain, setGrain] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [taskType, setTaskType] = useState("");
  const [metric, setMetric] = useState("");
  const [strategy, setStrategy] = useState("random");
  const [nFolds, setNFolds] = useState(5);
  const [testSize, setTestSize] = useState(0.2);
  const [instructions, setInstructions] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.runOptions(), api.dataSources()])
      .then(([o, s]) => {
        setOptions(o);
        setSources(s);
        setNFolds(o.defaults.n_folds);
        setTestSize(o.defaults.test_size);
        setTaskType(o.task_types[0] ?? "");
        setStrategy(o.split_strategies[0] ?? "random");
        setMode(o.execution_modes[0]?.value ?? "agent");
        if (s[0]) setSourceId(s[0].source_id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Loading the profile is what turns table and column pickers into real
  // choices instead of free-text fields that fail validation server-side.
  useEffect(() => {
    if (!sourceId) return;
    let cancelled = false;
    setProfile(null);
    api.sourceProfile(sourceId)
      .then((p) => {
        if (cancelled) return;
        setProfile(p);
        const first = p.tables[0];
        if (first) {
          setBaseTable(first.name);
          setGrain(first.candidate_keys[0] ?? []);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => { cancelled = true; };
  }, [sourceId]);

  const table: ProfiledTable | null = useMemo(
    () => profile?.tables.find((t) => t.name === baseTable) ?? null,
    [profile, baseTable],
  );

  // Reset the grain when the base table changes; a key from another table is
  // never valid here.
  useEffect(() => {
    if (table) setGrain(table.candidate_keys[0] ?? []);
  }, [table?.name]);

  const metrics = options?.metrics_by_task[taskType] ?? [];
  useEffect(() => {
    if (metrics.length && !metrics.includes(metric)) setMetric(metrics[0]);
  }, [taskType, metrics.join(",")]);

  const targets = table?.columns.filter((c) => c.candidate_target) ?? [];
  useEffect(() => {
    if (targets.length && !targets.some((t) => t.name === target)) {
      setTarget(targets[0].name);
    }
  }, [table?.name, targets.length]);

  const ready = Boolean(sourceId && baseTable && grain.length && taskType && metric);

  async function launch() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    const body: RunRequest = {
      source_id: sourceId,
      mode,
      agent_panel_size: panelSize,
      base_table: baseTable,
      base_grain: grain,
      task_type: taskType,
      primary_metric: metric,
      target_column: target || null,
      problem_title: `${titleize(taskType)} on ${baseTable}`,
      validation: { strategy, n_folds: nFolds, test_size: testSize },
      instructions: instructions.trim() ? [instructions.trim()] : [],
    };
    try {
      const created = await api.createRun(body);
      onLaunched(created.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-pop">
        <header className="flex shrink-0 items-center gap-3 border-b border-line px-5 py-3.5">
          <h2 className="flex-1 text-base font-semibold">Start a run</h2>
          <button onClick={onClose} className="rounded p-1 text-ink-faint hover:bg-surface-sunken hover:text-ink" title="Close">
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="m5 5 10 10M15 5 5 15" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {loading && <Spinner label="Loading options…" />}
          {error && <p className="rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}

          {options && (
            <>
              <Field label="Data source" hint="Profiled locally. No rows leave this machine.">
                <select value={sourceId} onChange={(e) => setSourceId(e.target.value)} className="field">
                  {sources.map((s) => <option key={s.source_id} value={s.source_id}>{s.label}</option>)}
                </select>
              </Field>

              <Field label="Execution mode">
                <div className="grid gap-2 sm:grid-cols-2">
                  {options.execution_modes.map((m) => (
                    <button
                      key={m.value}
                      onClick={() => setMode(m.value)}
                      className={cx(
                        "rounded-lg border px-3 py-2.5 text-left transition-colors",
                        mode === m.value ? "border-brand-500 bg-brand-50" : "border-line hover:bg-surface-sunken",
                      )}
                    >
                      <span className="block text-sm font-medium text-ink">{m.label}</span>
                      <span className="mt-0.5 block text-[11px] leading-snug text-ink-mute">{m.description}</span>
                    </button>
                  ))}
                </div>
              </Field>

              {mode === "agent" && (
                <Field
                  label="Agents per stage"
                  hint="Independent samples of each agent stage. More samples measure whether the decision is stable; they do not make it better. Disagreement stops the run for a human."
                >
                  <div className="flex gap-2">
                    {options.agent_panel_sizes.map((n) => (
                      <button
                        key={n}
                        onClick={() => setPanelSize(n)}
                        className={cx(
                          "flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
                          panelSize === n ? "border-brand-500 bg-brand-50 text-brand-700" : "border-line text-ink-soft hover:bg-surface-sunken",
                        )}
                      >
                        {n === 1 ? "1 agent" : `${n} agents`}
                      </button>
                    ))}
                  </div>
                  {panelSize > 1 && (
                    <p className="mt-1.5 text-[11px] text-ink-faint">
                      Each agent stage runs {panelSize}×, so expect roughly {panelSize}× the
                      planning latency.
                    </p>
                  )}
                </Field>
              )}

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Base table" hint="The entity one ABT row represents.">
                  <select value={baseTable} onChange={(e) => setBaseTable(e.target.value)} className="field">
                    {(profile?.tables ?? []).map((t) => (
                      <option key={t.name} value={t.name}>
                        {t.name} ({t.rows.toLocaleString()} rows)
                      </option>
                    ))}
                  </select>
                </Field>

                <Field label="Base grain" hint="Columns that uniquely identify one row.">
                  <select
                    value={grain.join(",")}
                    onChange={(e) => setGrain(e.target.value ? e.target.value.split(",") : [])}
                    className="field"
                  >
                    {(table?.candidate_keys ?? []).map((key) => (
                      <option key={key.join(",")} value={key.join(",")}>{key.join(" + ")}</option>
                    ))}
                    {table && table.candidate_keys.length === 0 && (
                      <option value="">no candidate key measured</option>
                    )}
                  </select>
                </Field>

                <Field label="Task type">
                  <select value={taskType} onChange={(e) => setTaskType(e.target.value)} className="field">
                    {options.task_types.map((t) => <option key={t} value={t}>{titleize(t)}</option>)}
                  </select>
                </Field>

                <Field label="Primary metric">
                  <select value={metric} onChange={(e) => setMetric(e.target.value)} className="field">
                    {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Field>

                <Field
                  label="Target column"
                  hint={mode === "agent" ? "The planner may propose a different target." : undefined}
                >
                  <select value={target} onChange={(e) => setTarget(e.target.value)} className="field">
                    {targets.map((c) => (
                      <option key={c.name} value={c.name}>{c.name} — {titleize(c.semantic_type)}</option>
                    ))}
                    {targets.length === 0 && <option value="">no candidate target measured</option>}
                  </select>
                </Field>

                <Field
                  label="Split strategy"
                  hint={mode === "agent" ? "A floor. The agent may only choose something stronger." : undefined}
                >
                  <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="field">
                    {options.split_strategies.map((s) => <option key={s} value={s}>{titleize(s)}</option>)}
                  </select>
                </Field>

                <Field label="Folds">
                  <input
                    type="number" min={2} max={20} value={nFolds}
                    onChange={(e) => setNFolds(Number(e.target.value))}
                    className="field"
                  />
                </Field>

                <Field label="Holdout fraction">
                  <input
                    type="number" min={0.05} max={0.5} step={0.05} value={testSize}
                    onChange={(e) => setTestSize(Number(e.target.value))}
                    className="field"
                  />
                </Field>
              </div>

              <Field label="Instructions to the planner" hint="Optional. Carried into the agent's context.">
                <textarea
                  rows={2}
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  placeholder="e.g. we care most about under-forecasting senior specialists"
                  className="field resize-none text-xs"
                />
              </Field>

              {table && table.issues.length > 0 && (
                <div className="rounded-lg border border-warn-500/30 bg-warn-50 px-3 py-2">
                  <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-warn-700">
                    Measured issues on {table.name}
                  </p>
                  <ul className="space-y-0.5">
                    {table.issues.slice(0, 4).map((issue, i) => (
                      <li key={i} className="text-xs leading-relaxed text-ink-soft">{issue}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>

        <footer className="flex shrink-0 items-center gap-3 border-t border-line px-5 py-3">
          {profile && <Badge>{profile.tables.length} tables profiled</Badge>}
          {busy && <Spinner />}
          <div className="ml-auto flex items-center gap-2">
            <button onClick={onClose} className="btn-ghost text-xs">Cancel</button>
            <button onClick={() => void launch()} disabled={!ready || busy} className="btn-primary text-xs">
              Start run
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-soft">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] leading-snug text-ink-faint">{hint}</span>}
    </label>
  );
}
