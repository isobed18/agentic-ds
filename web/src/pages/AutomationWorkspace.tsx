import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PipelineBuilder } from "../components/PipelineBuilder";
import { PlannerPanel } from "../components/PlannerPanel";
import { Badge, Empty, Spinner } from "../components/ui";
import {
  api,
  type DataSource,
  type PipelineBlueprint,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { t } from "../lib/i18n";

export function AutomationWorkspace() {
  const [params, setParams] = useSearchParams();
  const [sources, setSources] = useState<DataSource[]>([]);
  const [sourceId, setSourceId] = useState(params.get("source") ?? "");
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [runId, setRunId] = useState<string | null>(params.get("run"));
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<StagingWorkspace | null>(null);
  const [blueprint, setBlueprint] = useState<PipelineBlueprint | null>(null);
  const [reuseCache, setReuseCache] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [branchRuns, setBranchRuns] = useState<Array<{ branch_id: string; run_id: string }>>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshSources = useCallback(async () => {
    const next = await api.dataSources();
    setSources(next);
    return next;
  }, []);

  useEffect(() => { void refreshSources().catch(showError); }, [refreshSources]);

  useEffect(() => {
    if (!sourceId) {
      setProfile(null);
      setBlueprint(null);
      setWorkspace(null);
      setRunId(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    Promise.all([api.sourceProfile(sourceId), api.defaultStagingPipeline(sourceId), api.runs()])
      .then(async ([nextProfile, defaultBlueprint, runs]) => {
        if (cancelled) return;
        setProfile(nextProfile);
        setBlueprint(defaultBlueprint);
        const requested = params.get("run");
        const previous = runs.find((item) => item.run_id === requested)
          ?? runs.find((item) => item.source_id === sourceId && (item.stages ?? []).includes("staging"));
        if (!previous) {
          setRunId(null);
          setRunStatus(null);
          setWorkspace(null);
          return;
        }
        const saved = await api.stagingWorkspace(previous.run_id).catch(() => null);
        if (cancelled || !saved) return;
        applyWorkspace(saved);
        setRunStatus(previous.status);
      })
      .catch(showError)
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [sourceId]);

  useEffect(() => {
    if (!runId || !["staging", "running"].includes(runStatus ?? "")) return;
    const timer = window.setInterval(() => {
      void api.runProgress(runId).then(async (progress) => {
        const status = String(progress.status ?? runStatus);
        setRunStatus(status);
        if (["staged", "completed", "failed"].includes(status)) {
          const saved = await api.stagingWorkspace(runId).catch(() => null);
          if (saved) applyWorkspace(saved);
        }
      }).catch(showError);
    }, 2200);
    return () => window.clearInterval(timer);
  }, [runId, runStatus]);

  function applyWorkspace(next: StagingWorkspace) {
    setWorkspace(next);
    setRunId(next.run_id);
    if (next.pipeline_blueprint) setBlueprint(next.pipeline_blueprint);
    setParams({ source: next.source_id, run: next.run_id }, { replace: true });
  }

  function showError(caught: unknown) {
    setError(caught instanceof Error ? caught.message : String(caught));
  }

  async function upload(files: FileList | File[]) {
    const selected = Array.from(files);
    if (!selected.length) return;
    setUploading(true);
    setError(null);
    try {
      let group: string | undefined;
      for (const file of selected) {
        group = (await api.upload(file, group)).source_id;
      }
      await refreshSources();
      if (group) {
        setSourceId(group);
        setParams({ source: group }, { replace: true });
      }
    } catch (caught) {
      showError(caught);
    } finally {
      setUploading(false);
    }
  }

  async function runGraph() {
    if (!sourceId || !blueprint || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!runId || !workspace) {
        const staged = await api.stageRun(sourceId, reuseCache, blueprint);
        setRunId(staged.run_id);
        setRunStatus(staged.status);
        setParams({ source: sourceId, run: staged.run_id }, { replace: true });
        return;
      }
      if (profile?.documents?.length) {
        const withDocuments = await api.runDocumentUnderstanding(runId);
        applyWorkspace(withDocuments);
      }
      await api.compileAutomation(runId);
      if (!profile?.tables.length) {
        setRunStatus("staged");
        return;
      }
      if (blueprint.components.some((component) => component.enabled && component.branch_id)) {
        const launched = await api.startAutomationBranches(runId);
        setBranchRuns(launched.branches);
        setRunStatus("branches_running");
        return;
      }
      await api.startStaged(runId, {
        run_mode: workspace.recommended_plan ? "fully_auto" : "auto",
      });
      setRunStatus("running");
    } catch (caught) {
      showError(caught);
    } finally {
      setBusy(false);
    }
  }

  const actionLabel = !runId || !workspace
    ? "Run data understanding"
    : workspace.recommended_plan
      ? "Run accepted automation"
      : "Run configured graph";

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-surface-sunken"
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files); }}
    >
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
        <div className="mr-2">
          <h1 className="text-sm font-semibold text-ink">{t("Automation workspace")}</h1>
          <p className="text-[10px] text-ink-mute">{t("Upload, understand, decide, and run on one typed graph.")}</p>
        </div>
        <select
          value={sourceId}
          onChange={(event) => { setSourceId(event.target.value); setParams(event.target.value ? { source: event.target.value } : {}, { replace: true }); }}
          className="field !w-56 !py-1.5 text-xs"
        >
          <option value="">{t("Choose data…")}</option>
          {sources.map((source) => <option key={source.source_id} value={source.source_id}>{source.label}</option>)}
        </select>
        <input ref={fileInput} type="file" multiple accept=".csv,.tsv,.xlsx,.xls,.parquet,.pdf" className="hidden" onChange={(event) => { void upload(event.target.files ?? []); event.target.value = ""; }} />
        <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => fileInput.current?.click()} disabled={uploading}>
          {uploading ? t("Uploading…") : `+ ${t("Add data")}`}
        </button>
        <label className="flex items-center gap-1.5 text-[10px] text-ink-mute">
          <input type="checkbox" checked={reuseCache} onChange={(event) => setReuseCache(event.target.checked)} />
          {t("Reuse matching staging cache")}
        </label>
        {runStatus && <Badge tone={runStatus === "failed" ? "stop" : runStatus === "completed" ? "ok" : "brand"}>{runStatus}</Badge>}
        {runId && <span className="font-mono text-[9px] text-ink-faint">{runId}</span>}
        {branchRuns.map((branch) => (
          <Badge key={branch.run_id} tone="brand">{branch.branch_id} · {branch.run_id}</Badge>
        ))}
        <button type="button" onClick={() => void runGraph()} disabled={!sourceId || !blueprint || busy || runStatus === "staging" || runStatus === "running"} className="btn-primary ml-auto !py-1.5 text-xs">
          {busy ? t("Working…") : t(actionLabel)}
        </button>
      </header>

      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
      {dragging && <div className="pointer-events-none absolute inset-4 z-50 grid place-items-center rounded-2xl border-2 border-dashed border-brand-500 bg-brand-50/90 text-sm font-semibold text-brand-700">{t("Drop files to add them to one source")}</div>}

      <main className="min-h-0 flex-1 overflow-y-auto p-3">
        {busy && !blueprint && <Spinner label={t("Preparing the automation graph…")} />}
        {!busy && !blueprint && (
          <button type="button" onClick={() => fileInput.current?.click()} className="grid h-full min-h-96 w-full place-items-center rounded-xl border-2 border-dashed border-line bg-surface text-left hover:border-brand-300">
            <Empty title={t("Start with data")} hint={t("Upload CSV, Excel, Parquet, and PDF files. The default understanding graph will be recommended automatically.")} />
          </button>
        )}
        {blueprint && (
          <PipelineBuilder
            runId={runId}
            baseArtifactId={workspace?.artifact_id ?? null}
            blueprint={blueprint}
            componentOutputs={workspace?.component_outputs ?? []}
            onChange={setBlueprint}
            onSaved={applyWorkspace}
            plannerPanel={(
              <PlannerPanel
                runId={runId}
                sourceId={sourceId}
                open
                onWorkspaceUpdated={applyWorkspace}
                starterPrompts={[
                  "Explain how these files relate and what I should verify.",
                  "Create the reports needed to understand this data.",
                  "Recommend a pipeline and choose where we should pause.",
                  "Add two independent ML problem branches if the evidence supports them.",
                ]}
              />
            )}
          />
        )}
      </main>
    </div>
  );
}
