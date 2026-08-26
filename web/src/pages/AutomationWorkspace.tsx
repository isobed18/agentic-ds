import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PipelineBuilder } from "../components/PipelineBuilder";
import { GuidedPipeline } from "../components/GuidedPipeline";
import { LanguagePicker } from "../components/Shell";
import {
  SourceSummary,
  UnderstandingAndProposal,
  UnderstandingProgress,
} from "../components/UnderstandingWorkspace";
import { automationView } from "../components/automationWorkspaceState";
import { Badge, Empty, Spinner, cx } from "../components/ui";
import {
  api,
  type AutomationDefinition,
  type PipelineBlueprint,
  type RunSummary,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { t } from "../lib/i18n";

type SaveState = "saved" | "saving" | "unsaved";

export function AutomationWorkspace() {
  const [params, setParams] = useSearchParams();
  const automationId = params.get("automation");
  if (!automationId) {
    return <AutomationLibrary sourceId={params.get("source")} onOpen={(id) => setParams({ automation: id })} />;
  }
  return <AutomationEditor automationId={automationId} />;
}

function AutomationLibrary({ sourceId, onOpen }: { sourceId: string | null; onOpen: (id: string) => void }) {
  const [items, setItems] = useState<AutomationDefinition[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const importedSource = useRef(false);

  useEffect(() => {
    void api.automations().then(setItems).catch((caught) => setError(messageOf(caught)));
  }, []);

  useEffect(() => {
    if (!sourceId || importedSource.current) return;
    importedSource.current = true;
    setBusy(true);
    void api.createAutomation(t("Untitled automation"))
      .then((created) => api.updateAutomation(created.automation_id, created.revision, { source_id: sourceId }))
      .then((saved) => onOpen(saved.automation_id))
      .catch((caught) => setError(messageOf(caught)))
      .finally(() => setBusy(false));
  }, [sourceId, onOpen]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAutomation(t("Untitled automation"));
      onOpen(created.automation_id);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-surface-sunken px-6 py-8 lg:px-10">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div><h1 className="text-2xl font-semibold text-ink">{t("Data projects")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Understand unfamiliar files, agree on an ML plan, then run it transparently.")}</p></div>
          <div className="flex items-center gap-3"><LanguagePicker /><button type="button" className="btn-primary" onClick={() => void create()} disabled={busy}>{busy ? t("Creating…") : `+ ${t("New data project")}`}</button></div>
        </header>
        {error && <p className="mt-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
        {busy && !items.length ? <div className="mt-16"><Spinner label={t("Opening automation…")} /></div> : (
          <div className="mt-7 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => <button key={item.automation_id} type="button" onClick={() => onOpen(item.automation_id)} className="rounded-xl border border-line bg-surface p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-pop"><div className="flex items-start justify-between gap-3"><h2 className="truncate text-sm font-semibold text-ink">{item.name}</h2><Badge tone={item.status === "saved" ? "ok" : "neutral"}>{t(item.status === "saved" ? "Saved" : "Draft")}</Badge></div><p className="mt-5 text-xs text-ink-mute">{item.execution_ids.length ? t("{count} executions", { count: item.execution_ids.length }) : t("Never executed")}</p><p className="mt-1 text-[10px] text-ink-faint">{new Date(item.updated_at).toLocaleString()}</p></button>)}
            {!items.length && !busy && <div className="col-span-full rounded-2xl border border-dashed border-line bg-surface py-16"><Empty title={t("No data projects yet")} hint={t("Add unfamiliar files. Agentic DS will route them, explain what is usable, and propose the base ML pipeline.")} /></div>}
          </div>
        )}
      </div>
    </div>
  );
}

function AutomationEditor({ automationId }: { automationId: string }) {
  const [params, setParams] = useSearchParams();
  const [automation, setAutomation] = useState<AutomationDefinition | null>(null);
  const [name, setName] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [activeView, setActiveView] = useState<"editor" | "executions">(params.get("view") === "executions" ? "executions" : "editor");
  const [executions, setExecutions] = useState<RunSummary[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [runId, setRunId] = useState<string | null>(params.get("run"));
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<StagingWorkspace | null>(null);
  const [blueprint, setBlueprint] = useState<PipelineBlueprint | null>(null);
  const [reuseCache, setReuseCache] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [advancedGraph, setAdvancedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refreshAutomation = useCallback(async () => {
    const [record, history] = await Promise.all([api.automation(automationId), api.automationExecutions(automationId)]);
    setAutomation(record); setName(record.name); setSourceId(record.source_id ?? ""); setExecutions(history);
    return record;
  }, [automationId]);

  useEffect(() => {
    setBusy(true);
    refreshAutomation().catch((caught) => setError(messageOf(caught))).finally(() => setBusy(false));
  }, [refreshAutomation]);

  useEffect(() => {
    if (!sourceId || !automation) { setProfile(null); setBlueprint(automation?.pipeline_blueprint ?? null); setWorkspace(null); setRunId(null); setRunStatus(null); setAdvancedGraph(false); return; }
    let cancelled = false;
    setBusy(true); setError(null);
    Promise.all([api.sourceProfile(sourceId), api.automationExecutions(automationId)]).then(async ([nextProfile, history]) => {
      if (cancelled) return;
      setProfile(nextProfile); setExecutions(history);
      const requested = params.get("run");
      const previous = history.find((item) => item.run_id === requested) ?? history[0];
      if (!previous) { setRunId(null); setRunStatus(null); setWorkspace(null); setBlueprint(automation.pipeline_blueprint ?? null); return; }
      setRunId(previous.run_id); setRunStatus(previous.status);
      const progress = await api.runProgress(previous.run_id).catch(() => null);
      if (!cancelled && typeof progress?.error === "string" && progress.error) setError(progress.error);
      const saved = await api.stagingWorkspace(previous.run_id).catch(() => null);
      if (!cancelled && saved) applyWorkspace(saved);
    }).catch((caught) => setError(messageOf(caught))).finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [automationId, sourceId, automation?.automation_id]);

  useEffect(() => {
    if (!runId || !["queued", "staging", "running"].includes(runStatus ?? "")) return;
    const timer = window.setInterval(() => { void api.runProgress(runId).then(async (progress) => { setRunStatus(String(progress.status ?? runStatus)); if (typeof progress.error === "string" && progress.error) setError(progress.error); const saved = await api.stagingWorkspace(runId).catch(() => null); if (saved) applyWorkspace(saved); }).catch((caught) => setError(messageOf(caught))); }, 2200);
    return () => window.clearInterval(timer);
  }, [runId, runStatus]);

  function applyWorkspace(next: StagingWorkspace) {
    setWorkspace(next); setRunId(next.run_id); if (next.pipeline_blueprint) setBlueprint(next.pipeline_blueprint);
    setParams({ automation: automationId, run: next.run_id }, { replace: true });
    void refreshAutomation().catch((caught) => setError(messageOf(caught)));
  }

  async function persistName() {
    if (!automation || name.trim() === automation.name) { setName(automation?.name ?? name); setSaveState("saved"); return; }
    setSaveState("saving");
    try { const saved = await api.updateAutomation(automationId, automation.revision, { name: name.trim() }); setAutomation(saved); setName(saved.name); setSaveState("saved"); }
    catch (caught) { setSaveState("unsaved"); setError(messageOf(caught)); }
  }

  async function upload(files: FileList | File[]) {
    const selected = Array.from(files); if (!selected.length || !automation) return;
    setUploading(true); setError(null);
    try { let group: string | undefined; for (const file of selected) group = (await api.upload(file, group)).source_id; if (group) { const saved = await api.updateAutomation(automationId, automation.revision, { source_id: group }); setAutomation(saved); setSourceId(group); setSaveState("saved"); } }
    catch (caught) { setError(messageOf(caught)); } finally { setUploading(false); }
  }

  async function startUnderstanding() {
    if (!sourceId || busy) return; setBusy(true); setError(null);
    try { const staged = await api.stageRun(sourceId, reuseCache, blueprint, automationId); setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null); setParams({ automation: automationId, run: staged.run_id }, { replace: true }); await refreshAutomation(); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  async function acceptPlan() {
    if (!runId || !workspace || busy) return; setBusy(true); setError(null);
    try { applyWorkspace(await api.acceptStagingPlan(runId, workspace.artifact_id)); setAdvancedGraph(false); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  async function runAcceptedWorkflow() {
    if (!runId || !workspace || !blueprint || busy) return; setBusy(true); setError(null);
    try { if (!profile?.tables.length) { setError(t("This accepted plan is document/report analysis only; no ML run is implied.")); return; } if (blueprint.components.some((component) => component.enabled && component.branch_id)) { await api.startAutomationBranches(runId); setRunStatus("branches_running"); } else { await api.startStaged(runId, { run_mode: "fully_auto" }); setRunStatus("running"); } }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  async function pauseAcceptedWorkflow() {
    if (!runId || busy) return; setBusy(true); setError(null);
    try { await api.pauseRun(runId); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  const lifecycle = automationView({ sourceId, runId, runStatus, workspace, advancedGraph });
  const switchView = (view: "editor" | "executions") => { setActiveView(view); setParams({ automation: automationId, ...(runId ? { run: runId } : {}), ...(view === "executions" ? { view: "executions" } : {}) }, { replace: true }); };

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-sunken" onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files); }}>
      <input ref={fileInput} type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.parquet,.pdf" className="hidden" onChange={(event) => { void upload(event.target.files ?? []); event.target.value = ""; }} />
      <header className="relative flex h-[58px] shrink-0 items-center border-b border-line bg-surface px-4">
        <input value={name} onChange={(event) => { setName(event.target.value); setSaveState("unsaved"); }} onBlur={() => void persistName()} aria-label={t("Automation name")} className="min-w-0 w-[320px] max-w-[32vw] border-0 bg-transparent text-sm font-semibold text-ink outline-none" />
        <div className="absolute left-1/2 flex -translate-x-1/2 rounded-lg bg-surface-sunken p-1">{(["editor", "executions"] as const).map((view) => <button key={view} type="button" onClick={() => switchView(view)} className={cx("rounded-md px-4 py-1.5 text-xs font-medium", activeView === view ? "bg-surface text-ink shadow-sm" : "text-ink-mute")}>{t(view === "editor" ? "Editor" : "Executions")}</button>)}</div>
        <div className="ml-auto flex items-center gap-2"><LanguagePicker /><span className={cx("text-[11px]", saveState === "unsaved" ? "text-warn-700" : "text-ink-faint")}>{t(saveState === "saving" ? "Saving…" : saveState === "unsaved" ? "Unsaved changes" : "Saved")}</span>{activeView === "editor" && <button type="button" className="btn-ghost !h-8 !w-8 !p-0 text-lg" title={t("Add files")} onClick={() => fileInput.current?.click()}>+</button>}</div>
      </header>
      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
      {dragging && <div className="pointer-events-none absolute inset-4 z-50 grid place-items-center rounded-2xl border-2 border-dashed border-brand-500 bg-brand-50/95 text-sm font-semibold text-brand-700">{t("Drop files to add them as one source")}</div>}
      <main className="min-h-0 flex-1">{activeView === "executions" ? <ExecutionHistory executions={executions} /> : <>{lifecycle === "empty" && <button type="button" onClick={() => fileInput.current?.click()} className="grid h-full w-full place-items-center bg-[radial-gradient(#d9e0ea_1px,transparent_1px)] [background-size:20px_20px] p-8 text-left"><div className="w-full max-w-xl rounded-2xl border-2 border-dashed border-line bg-surface px-8 py-12 text-center shadow-card"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Guided data science")}</p><Empty title={t("Start with unfamiliar files")} hint={t("Upload PDFs, CSV, Excel, Parquet, or TXT files. Intake will show where every file goes before any ML decision is made.")} /><span className="btn-primary mt-6">{uploading ? t("Uploading…") : `+ ${t("Upload files")}`}</span><ol className="mt-8 grid grid-cols-5 gap-2 text-[9px] text-ink-mute">{["Intake", "Understand", "Choose ML inputs", "Accept plan", "Run and review"].map((step, index) => <li key={step}><span className="mx-auto mb-1 grid h-5 w-5 place-items-center rounded-full bg-brand-50 font-semibold text-brand-700">{index + 1}</span>{t(step)}</li>)}</ol></div></button>}{lifecycle === "source" && !profile && <div className="grid h-full place-items-center"><Spinner label={t("Inspecting and routing uploaded files…")} /></div>}{lifecycle === "source" && profile && <SourceSummary profile={profile} onStart={() => void startUnderstanding()} busy={busy} />}{lifecycle === "understanding" && profile && <UnderstandingProgress profile={profile} runId={runId} workspace={workspace} />}{lifecycle === "proposal" && profile && workspace && runId && <UnderstandingAndProposal profile={profile} workspace={workspace} sourceId={sourceId} runId={runId} onWorkspaceUpdated={applyWorkspace} onAccept={() => void acceptPlan()} onAdvanced={() => setAdvancedGraph(true)} busy={busy} />}{lifecycle === "guided_pipeline" && profile && workspace && runId && <GuidedPipeline runId={runId} profile={profile} workspace={workspace} componentOutputs={workspace.component_outputs ?? []} runStatus={runStatus} busy={busy} onRun={() => void runAcceptedWorkflow()} onPause={() => void pauseAcceptedWorkflow()} onRetry={() => void startUnderstanding()} onAdvanced={() => setAdvancedGraph(true)} onOpenExecutions={() => switchView("executions")} />}{lifecycle === "workflow" && blueprint && <PipelineBuilder runId={runId} baseArtifactId={workspace?.artifact_id ?? null} blueprint={blueprint} layout={workspace?.pipeline_layout ?? automation?.pipeline_layout} componentOutputs={workspace?.component_outputs ?? []} onChange={(next) => { setBlueprint(next); setSaveState("unsaved"); }} onSaved={(next) => { applyWorkspace(next); setSaveState("saved"); }} onExitAdvanced={() => setAdvancedGraph(false)} />}</>}</main>
      {!runId && sourceId && activeView === "editor" && <label className="absolute bottom-3 left-4 flex items-center gap-1.5 rounded-lg bg-surface px-2 py-1.5 text-[10px] text-ink-mute shadow-card"><input type="checkbox" checked={reuseCache} onChange={(event) => setReuseCache(event.target.checked)} />{t("Reuse matching understanding")}</label>}
    </div>
  );
}

function ExecutionHistory({ executions }: { executions: RunSummary[] }) {
  const [selected, setSelected] = useState<RunSummary | null>(executions[0] ?? null);
  useEffect(() => { if (!selected && executions.length) setSelected(executions[0]); }, [executions, selected]);
  return <div className="h-full overflow-y-auto bg-surface-sunken p-6"><div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[1fr_1.4fr]"><section><h2 className="text-sm font-semibold text-ink">{t("Execution history")}</h2><div className="mt-3 space-y-2">{executions.map((item, index) => <button key={item.run_id} type="button" onClick={() => setSelected(item)} className={cx("flex w-full items-center gap-3 rounded-xl border bg-surface px-4 py-3 text-left", selected?.run_id === item.run_id ? "border-brand-400 ring-2 ring-brand-100" : "border-line")}><span className="text-xs font-semibold text-ink">#{executions.length - index}</span><Badge tone={item.status === "completed" ? "ok" : item.status === "failed" ? "stop" : "brand"}>{t(item.status)}</Badge><span className="ml-auto text-[10px] text-ink-faint">{item.last_activity ? new Date(item.last_activity).toLocaleString() : "—"}</span></button>)}{!executions.length && <Empty title={t("Never executed")} hint={t("Accepted workflow runs will appear here without changing the saved editor graph.")} />}</div></section>{selected && <section className="rounded-xl border border-line bg-surface p-5 shadow-card"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Selected execution")}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><ExecutionFact label={t("Status")} value={t(selected.status)} /><ExecutionFact label={t("Artifacts")} value={selected.artifact_count ?? 0} /><ExecutionFact label={t("Completed stages")} value={selected.stages?.length ?? 0} /><ExecutionFact label={t("Last activity")} value={selected.last_activity ? new Date(selected.last_activity).toLocaleString() : "—"} /></div><p className="mt-4 text-xs text-ink-mute">{t("Select a completed node in the Editor to inspect its readable artifacts and evidence.")}</p></section>}</div></div>;
}

function ExecutionFact({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-[10px] text-ink-faint">{label}</p><p className="mt-1 text-sm font-semibold text-ink">{value}</p></div>; }
function messageOf(caught: unknown): string { return caught instanceof Error ? caught.message : String(caught); }
