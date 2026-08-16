/**
 * The main screen: pipeline rail on top, stage workspace centre, planner right.
 *
 * Progress is polled while a run is active so node status, attempts and
 * approval requests surface on the rail without a manual refresh.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { LaunchDialog } from "../components/LaunchDialog";
import { PipelineRail } from "../components/PipelineRail";
import { PlannerPanel } from "../components/PlannerPanel";
import { StageWorkspace } from "../components/StageWorkspace";
import { Badge, Spinner, cx, toneFor } from "../components/ui";
import { api, type RunSummary, type WorkflowNode } from "../lib/api";
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
        <div className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-6 py-2.5">
          <div className="relative">
            <button onClick={() => setShowRuns((s) => !s)} className="btn-ghost !py-1.5 text-xs">
              <span className="font-mono">{runId ?? "no run selected"}</span>
              <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="m6 8 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            {showRuns && (
              <div className="absolute left-0 top-10 z-20 max-h-80 w-[360px] overflow-y-auto rounded-xl border border-line bg-surface p-1.5 shadow-pop">
                {runs.length === 0 && <p className="px-3 py-4 text-center text-xs text-ink-mute">No runs yet.</p>}
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
                          Delete
                        </button>
                        <button
                          onClick={() => setConfirming(null)}
                          className="rounded px-2 py-1 text-[11px] text-ink-mute hover:bg-surface-sunken"
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <>
                        <Badge tone={toneFor(r.status)}>{statusLabel(r.status)}</Badge>
                        <button
                          onClick={() => setConfirming(r.run_id)}
                          title="Delete run permanently"
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
          <span className="text-xs text-ink-mute">{done}/{nodes.length} stages complete</span>
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${nodes.length ? (done / nodes.length) * 100 : 0}%` }} />
          </div>

          <div className="ml-auto flex items-center gap-2">
            {live && <Spinner />}
            <button onClick={() => setLaunching(true)} className="btn-primary !py-1.5 text-xs">
              Run pipeline
            </button>
          </div>
        </div>

        {error && <p className="mx-6 mt-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}

        <PipelineRail nodes={nodes} selected={stageId} onSelect={setStageId} />
        <StageWorkspace
          runId={runId}
          node={selected}
          onAnswered={() => { void refresh(); void refreshRuns(); }}
        />
      </div>

      {launching && (
        <LaunchDialog
          onClose={() => setLaunching(false)}
          onLaunched={(id) => void onLaunched(id)}
        />
      )}

      <PlannerPanel
        runId={runId}
        stageId={stageId}
        open={plannerOpen}
        onToggle={() => setPlannerOpen((o) => !o)}
      />
    </div>
  );
}
