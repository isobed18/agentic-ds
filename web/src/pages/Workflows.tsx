import { t } from "../lib/i18n";
/**
 * The main screen: pipeline rail on top, stage workspace centre, planner right.
 *
 * Progress is polled while a run is active so node status, attempts and
 * approval requests surface on the rail without a manual refresh.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { LaunchDialog } from "../components/LaunchDialog";
import { PipelineRail } from "../components/PipelineRail";
import { PlannerPanel } from "../components/PlannerPanel";
import { StageWorkspace } from "../components/StageWorkspace";
import { Badge, Spinner, cx, toneFor } from "../components/ui";
import { api, type RunOptions, type RunSummary, type WorkflowNode } from "../lib/api";
import { isActive, isRunActive, isSucceeded, statusLabel } from "../lib/status";


export function Workflows() {
  const [nodes, setNodes] = useState<WorkflowNode[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [stageId, setStageId] = useState<string | null>(null);
  const [plannerOpen, setPlannerOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const [live, setLive] = useState(false);
  // Deleting a run drops its artifacts for good, so the row asks first.
  const [confirming, setConfirming] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  // Arriving from the data screen carries the chosen dataset, and from a
  // notification the run to open. Both are read once rather than watched, so a
  // later manual selection is not fought by the URL.
  const [params, setParams] = useSearchParams();
  const [presetSource, setPresetSource] = useState<string | null>(null);
  const [options, setOptions] = useState<RunOptions | null>(null);
  useEffect(() => {
    api.runOptions().then(setOptions).catch(() => setOptions(null));
  }, []);
  useEffect(() => {
    const source = params.get("source");
    const run = params.get("run");
    if (source) { setPresetSource(source); setLaunching(true); }
    if (run) setRunId(run);
    if (source || run) setParams({}, { replace: true });
  }, []);

  const refreshRuns = useCallback(async () => {
    try { setRuns(await api.runs()); } catch { /* listing is non-critical */ }
  }, []);

  useEffect(() => { void refreshRuns(); }, [refreshRuns]);

  useEffect(() => { if (runs.length && !runId) setRunId(runs[0].run_id); }, [runs, runId]);

  const refresh = useCallback(async () => {
    try {
      const w = await api.workflow(runId);
      setNodes(w.nodes);
      setStageId((s) => s ?? w.nodes[0]?.id ?? null);
      // Whether to keep polling is decided here, from the response, rather
      // than from the `nodes` array in the effect below. Deriving it there
      // would re-run the effect on every poll — setNodes always yields a new
      // array reference — and turn a 2.5s poll into an unbounded request loop.
      setLive(isRunActive(w.run_status) || w.nodes.some((n) => isActive(n.status)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [runId]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => void refresh(), 2500);
    return () => clearInterval(timer);
  }, [live, refresh]);

  const selected = useMemo(
    () => nodes.find((n) => n.id === stageId) ?? null,
    [nodes, stageId],
  );
  const currentRun = runs.find((r) => r.run_id === runId) ?? null;
  const staged = currentRun?.status === "staged" || currentRun?.status === "staging";
  // A staged run exists to be looked at and configured, and intake is where
  // both happen, so opening one lands there rather than on whatever stage
  // happened to be selected for the previous run.
  useEffect(() => {
    if (staged) setStageId("intake");
  }, [staged, runId]);
  // Sibling runs of the same project, so a branch is visible as a branch rather
  // than as an unrelated row that happens to share a dataset.
  const family = useMemo(() => {
    if (!currentRun) return [];
    const root = currentRun.parent_run_id ?? currentRun.run_id;
    return runs.filter((r) => r.run_id === root || r.parent_run_id === root);
  }, [runs, currentRun]);

  // Model candidates, drawn as parallel nodes on the training column.
  const [pseudoBranches, setPseudoBranches] = useState<Record<string, string[]>>({});
  useEffect(() => {
    if (!runId) { setPseudoBranches({}); return; }
    const trained = nodes.find((n) => n.id === "training" && isSucceeded(n.status));
    if (!trained) { setPseudoBranches({}); return; }
    let cancelled = false;
    api.stage(runId, "training")
      .then((d) => {
        if (cancelled) return;
        const names = d.outputs
          .flatMap((o) => o.story?.model_comparison ?? [])
          .map((m) => m.candidate);
        setPseudoBranches(names.length > 1 ? { training: names } : {});
      })
      .catch(() => setPseudoBranches({}));
    return () => { cancelled = true; };
  }, [runId, nodes.find((n) => n.id === "training")?.status]);
  const done = nodes.filter((n) => isSucceeded(n.status)).length;

  async function onLaunched(newRunId: string) {
    setLaunching(false);
    setStageId(null);
    setNodes([]);
    // Only set the id. `refresh` is a useCallback closed over the *current*
    // runId, so calling it here would fetch the previously selected run's
    // graph and paint it under the new run's header — which is how a brand new
    // run came to display the last run's 11 completed stages. The effect below
    // re-runs when `refresh` is rebuilt with the new id.
    setRunId(newRunId);
    await refreshRuns();
  }

  async function removeRun(id: string) {
    setConfirming(null);
    try {
      await api.deleteRun(id);
      if (id === runId) setRunId(null);
      await refreshRuns();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-line bg-surface px-6 py-2.5">
          <div className="relative">
            <button onClick={() => setShowRuns((s) => !s)} className="btn-ghost !py-1.5 text-xs">
              <span className="font-mono">{runId ?? "no run selected"}</span>
              <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="m6 8 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            {showRuns && (
              <div className="absolute left-0 top-10 z-20 max-h-80 w-[360px] overflow-y-auto rounded-xl border border-line bg-surface p-1.5 shadow-pop">
                {runs.length === 0 && <p className="px-3 py-4 text-center text-xs text-ink-mute">{t("No runs yet.")}</p>}
                {runs.map((r) => (
                  <div key={r.run_id} className={cx("group flex items-center gap-2 rounded-lg px-2.5 py-2 hover:bg-surface-sunken", r.run_id === runId && "bg-brand-50")}>
                    <button className="min-w-0 flex-1 text-left" onClick={() => { setRunId(r.run_id); setShowRuns(false); }}>
                      <span className="block truncate font-mono text-xs text-ink">{r.run_id}</span>
                      <span className="text-[10px] text-ink-mute">{statusLabel(r.status)}</span>
                    </button>
                    {confirming === r.run_id ? (
                      <span className="flex shrink-0 items-center gap-1">
                        <button
                          onClick={() => void removeRun(r.run_id)}
                          className="rounded bg-stop-500 px-2 py-1 text-[11px] font-semibold text-white hover:bg-stop-600"
                        >
                          {t("Delete")}
                        </button>
                        <button
                          onClick={() => setConfirming(null)}
                          className="rounded px-2 py-1 text-[11px] text-ink-mute hover:bg-surface-sunken"
                        >
                          {t("Cancel")}
                        </button>
                      </span>
                    ) : (
                      <>
                        <Badge tone={toneFor(r.status)}>{statusLabel(r.status)}</Badge>
                        <button
                          onClick={() => setConfirming(r.run_id)}
                          title={t("Delete run permanently")}
                          className="rounded p-1 text-ink-faint opacity-0 hover:bg-stop-50 hover:text-stop-600 group-hover:opacity-100"
                        >
                          <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.7">
                            <path d="M4 6h12M8 6V4.5h4V6m-6 0v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V6" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {currentRun && <Badge tone={toneFor(currentRun.status)}>{statusLabel(currentRun.status)}</Badge>}
          {currentRun?.branch_label && <Badge tone="brand">{t("branch")}: {currentRun.branch_label}</Badge>}
          {family.length > 1 && (
            <span className="flex items-center gap-1">
              {family.map((r) => (
                <button
                  key={r.run_id}
                  onClick={() => setRunId(r.run_id)}
                  title={r.branch_label ?? t("original")}
                  className={cx(
                    "rounded-md border px-2 py-0.5 text-[11px]",
                    r.run_id === runId
                      ? "border-brand-500 bg-brand-50 text-ink"
                      : "border-line text-ink-mute hover:bg-surface-sunken",
                  )}
                >
                  {r.branch_label ? r.branch_label.slice(0, 22) : t("original")}
                </button>
              ))}
            </span>
          )}
          <span className="text-xs text-ink-mute">
            {done}/{nodes.length} {t("stages complete")}
          </span>
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${nodes.length ? (done / nodes.length) * 100 : 0}%` }} />
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            {live && <Spinner />}
            {/* The primary way into the product. It sat at the end of a
                non-wrapping row of status widgets and was reported as missing
                entirely, so it now keeps its width and says what it starts. */}
            <button onClick={() => setLaunching(true)} className="btn-primary !py-1.5 whitespace-nowrap text-xs">
              + {t("New run")}
            </button>
          </div>
        </div>

        {error && <p className="mx-6 mt-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}

        <PipelineRail nodes={nodes} selected={stageId} onSelect={setStageId} pseudoBranches={pseudoBranches} />
        <StageWorkspace
          runId={runId}
          node={selected}
          sourceId={currentRun?.dataset ?? null}
          runStatus={currentRun?.status ?? null}
          options={options}
          onAnswered={() => { void refresh(); void refreshRuns(); }}
          onBranched={(ids) => { void refreshRuns(); if (ids[0]) setRunId(ids[0]); }}
          onStarted={() => { setLive(true); void refresh(); void refreshRuns(); }}
          onDiscarded={() => { setRunId(null); setNodes([]); void refreshRuns(); }}
        />
      </div>

      {launching && (
        <LaunchDialog
          initialSourceId={presetSource}
          onClose={() => { setLaunching(false); setPresetSource(null); }}
          onLaunched={(id) => void onLaunched(id)}
        />
      )}

      <PlannerPanel
        runId={runId}
        stageId={stageId}
        sourceId={currentRun?.dataset ?? null}
        open={plannerOpen}
        onToggle={() => setPlannerOpen((o) => !o)}
      />
    </div>
  );
}
