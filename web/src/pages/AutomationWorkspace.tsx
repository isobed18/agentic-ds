import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PipelineBuilder } from "../components/PipelineBuilder";
import { GuidedPipeline } from "../components/GuidedPipeline";
import { ApprovalCard } from "../components/GateApproval";
import { LanguagePicker } from "../components/Shell";
import {
  SourceSummary,
  UnderstandingAndProposal,
  UnderstandingProgress,
} from "../components/UnderstandingWorkspace";
import { automationView, isAbandonedDraft, preferredExecution } from "../components/automationWorkspaceState";
import { Badge, Empty, Spinner, cx } from "../components/ui";
import {
  api,
  type AutomationDefinition,
  type DataSource,
  type GateDecision,
  type PipelineBlueprint,
  type RunSummary,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { t } from "../lib/i18n";

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

  async function remove(item: AutomationDefinition) {
    if (!window.confirm(t("Delete this data project? Its execution history will be kept."))) return;
    setError(null);
    try {
      await api.deleteAutomation(item.automation_id);
      setItems((current) => current.filter((candidate) => candidate.automation_id !== item.automation_id));
    } catch (caught) {
      setError(messageOf(caught));
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
            {items.map((item) => <article key={item.automation_id} className="relative rounded-xl border border-line bg-surface shadow-card transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-pop"><button type="button" onClick={() => onOpen(item.automation_id)} className="w-full p-5 pr-12 text-left"><div className="flex items-start justify-between gap-3"><h2 className="truncate text-sm font-semibold text-ink">{item.name}</h2><Badge tone={item.status === "saved" ? "ok" : item.status === "error" ? "stop" : "neutral"}>{t(item.status === "saved" ? "Saved" : item.status === "error" ? "Error" : "Draft")}</Badge></div><p className="mt-5 text-xs text-ink-mute">{item.execution_ids.length ? t("{count} executions", { count: item.execution_ids.length }) : t("Never executed")}</p><p className="mt-1 text-[10px] text-ink-faint">{new Date(item.updated_at).toLocaleString()}</p></button><button type="button" aria-label={t("Delete data project")} title={t("Delete data project")} onClick={() => void remove(item)} className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-lg text-ink-faint hover:bg-stop-50 hover:text-stop-700">×</button></article>)}
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
  // Deep-links from /experiments, /workflows and notifications all write
  // `view=runs`, while the in-page tab switch writes `view=executions`. The
  // Executions panel only ever recognised "executions", so every external
  // deep-link silently landed on the Editor tab instead (#68). Accept both.
  const [activeView, setActiveView] = useState<"editor" | "executions">(["executions", "runs"].includes(params.get("view") ?? "") ? "executions" : "editor");
  const [executions, setExecutions] = useState<RunSummary[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [sources, setSources] = useState<DataSource[]>([]);
  const [profile, setProfile] = useState<SourceProfile | null>(null);
  const [runId, setRunId] = useState<string | null>(params.get("run"));
  const [runStatus, setRunStatus] = useState<string | null>(null);
  // The gate escalation a run stopped on. StageWorkspace could render it, but
  // that screen was never routed, so a run in `awaiting_human` sat stuck with
  // no way to answer it (#81). Surfaced here, where the run is actually shown.
  const [pendingQuestion, setPendingQuestion] = useState<GateDecision | null>(null);
  const [workspace, setWorkspace] = useState<StagingWorkspace | null>(null);
  const [blueprint, setBlueprint] = useState<PipelineBlueprint | null>(null);
  const [reuseCache, setReuseCache] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  // Per-file upload progress and a handle to abort it, so a large file no
  // longer shows a static "Uploading…" with no percentage and no way out (#84).
  const [uploadProgress, setUploadProgress] = useState<{ index: number; total: number; name: string; fraction: number } | null>(null);
  const uploadAbort = useRef<AbortController | null>(null);
  const [dragging, setDragging] = useState(false);
  const [advancedGraph, setAdvancedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Discard a "+ New data project" that was created but never given a file, so
  // leaving the editor does not strand an empty "Untitled automation" card in
  // the library forever (#83). The cleanup reads the latest state through a ref
  // because an unmount-only effect would otherwise close over stale values.
  const abandonRef = useRef({ automation, sourceId });
  abandonRef.current = { automation, sourceId };
  useEffect(() => () => {
    const { automation, sourceId } = abandonRef.current;
    if (isAbandonedDraft(automation, sourceId)) {
      void api.deleteAutomation(automation!.automation_id).catch(() => { /* best-effort cleanup */ });
    }
  }, []);

  const refreshAutomation = useCallback(async () => {
    const [record, history] = await Promise.all([
      api.automation(automationId),
      api.automationExecutions(automationId),
    ]);
    setAutomation(record); setName(record.name); setSourceId(record.source_id ?? ""); setExecutions(history);
    // Deliberately not part of the Promise.all above. This list only fills the
    // reuse picker, but a rejection there used to reject the whole batch, leave
    // `automation` null, and take uploading down with it -- a convenience
    // feature disabling the page's primary action.
    void api.dataSources().then(setSources).catch(() => setSources([]));
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
    const timer = window.setInterval(() => { void api.runProgress(runId).then(async (progress) => { setRunStatus(String(progress.status ?? runStatus)); setPendingQuestion((progress.pending_question as GateDecision | null) ?? null); if (typeof progress.error === "string" && progress.error) setError(progress.error); const saved = await api.stagingWorkspace(runId).catch(() => null); if (saved) applyWorkspace(saved); }).catch((caught) => setError(messageOf(caught))); }, 2200);
    return () => window.clearInterval(timer);
  }, [runId, runStatus]);

  // Polling stops once a run reaches `awaiting_human`, and a run opened from a
  // deep-link or page refresh never polled at all -- so fetch the pending gate
  // question directly whenever the run is waiting on a person (#81).
  useEffect(() => {
    if (!runId || runStatus !== "awaiting_human") { setPendingQuestion(null); return; }
    let cancelled = false;
    void api.runProgress(runId)
      .then((progress) => { if (!cancelled) setPendingQuestion((progress.pending_question as GateDecision | null) ?? null); })
      .catch((caught) => setError(messageOf(caught)));
    return () => { cancelled = true; };
  }, [runId, runStatus]);

  // A gate answer is accepted once and the run leaves `awaiting_human`, so
  // re-read its status to clear the card and let polling pick up the resume.
  const onGateAnswered = useCallback(() => {
    if (!runId) return;
    void api.runProgress(runId)
      .then((progress) => { setRunStatus(String(progress.status ?? "")); setPendingQuestion((progress.pending_question as GateDecision | null) ?? null); })
      .catch((caught) => setError(messageOf(caught)));
  }, [runId]);

  function applyWorkspace(next: StagingWorkspace) {
    setWorkspace(next); setRunId(next.run_id); if (next.pipeline_blueprint) setBlueprint(next.pipeline_blueprint);
    setParams({ automation: automationId, run: next.run_id }, { replace: true });
    void refreshAutomation().catch((caught) => setError(messageOf(caught)));
  }

  async function persistName() {
    if (!automation || name.trim() === automation.name) { setName(automation?.name ?? name); return; }
    try { const saved = await api.updateAutomationSafely(automationId, automation.revision, { name: name.trim() }); setAutomation(saved); setName(saved.name); }
    catch (caught) { setError(messageOf(caught)); }
  }

  async function upload(files: FileList | File[]) {
    const selected = Array.from(files);
    if (!selected.length) return;
    if (!automation) {
      // This used to `return` silently. Picking files then did nothing at all:
      // no spinner, no error, no upload -- the page looked functional and the
      // button looked dead. The record is null while it is still loading and
      // stays null forever if its fetch failed, so the only honest options are
      // to say so or to disable the control, and the empty state does both.
      setError(t("The data project is still loading. Try again in a moment."));
      return;
    }
    const controller = new AbortController();
    uploadAbort.current = controller;
    setUploading(true); setError(null);
    try {
      // Seed the group from the project's current source so "Add files" appends
      // rather than starting a fresh group and silently dropping the files
      // already attached (#85). Empty means a brand-new project, a new group.
      let group: string | undefined = sourceId || undefined;
      const appending = Boolean(group);
      for (let i = 0; i < selected.length; i++) {
        const file = selected[i];
        setUploadProgress({ index: i, total: selected.length, name: file.name, fraction: 0 });
        const result = await api.upload(file, group, {
          signal: controller.signal,
          onProgress: (fraction) => setUploadProgress({ index: i, total: selected.length, name: file.name, fraction }),
        });
        group = result.source_id;
      }
      if (group) {
        const saved = await api.updateAutomationSafely(automationId, automation.revision, { source_id: group });
        setAutomation(saved); setSourceId(group);
        // Appending leaves sourceId unchanged, so the profile effect does not
        // re-run; refresh it so the new files show in the review list.
        if (appending) await refreshProfile();
      }
    }
    // A cancelled upload is a deliberate action, not an error to report.
    catch (caught) { if ((caught as { name?: string })?.name !== "AbortError") setError(messageOf(caught)); }
    finally { setUploading(false); setUploadProgress(null); uploadAbort.current = null; }
  }

  const refreshProfile = useCallback(async () => {
    if (!sourceId) return;
    const next = await api.sourceProfile(sourceId).catch(() => null);
    if (next) setProfile(next);
  }, [sourceId]);

  async function removeSourceFile(name: string) {
    if (!sourceId || busy) return;
    setBusy(true); setError(null);
    try {
      const result = await api.removeSourceFile(sourceId, name);
      if (result.deleted) {
        // The group's last file is gone, so the source no longer exists: detach
        // it from the project and fall back to the empty upload screen.
        if (automation) { const saved = await api.updateAutomationSafely(automationId, automation.revision, { source_id: null }); setAutomation(saved); }
        setSourceId(""); setProfile(null);
      } else {
        await refreshProfile();
      }
    } catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
  }

  function cancelUpload() { uploadAbort.current?.abort(); }

  async function selectExistingSource(nextSourceId: string) {
    if (!automation || !nextSourceId || busy) return;
    setBusy(true); setError(null);
    try {
      const saved = await api.updateAutomationSafely(automationId, automation.revision, { source_id: nextSourceId });
      setAutomation(saved); setSourceId(nextSourceId); setRunId(null); setRunStatus(null); setWorkspace(null); setAdvancedGraph(false);
      setParams({ automation: automationId }, { replace: true });
    } catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
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

  async function retryRun() {
    if (!runId || busy) return;
    setBusy(true); setError(null);
    try {
      const progress = await api.runProgress(runId);
      const pending = progress.pending_question as { human_prompt?: { options?: Array<{ option_id?: string }> } } | undefined;
      const canRetryComponent = pending?.human_prompt?.options?.some((option) => option.option_id === "retry");
      if (String(progress.status) === "awaiting_human" && canRetryComponent) {
        await api.answer(runId, { decision: "retry", instructions: [] });
        setRunStatus("resuming");
      } else {
        const staged = await api.stageRun(sourceId, reuseCache, blueprint, automationId);
        setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null);
        setParams({ automation: automationId, run: staged.run_id }, { replace: true });
        await refreshAutomation();
      }
    } catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
  }

  async function deleteExecution(targetRunId: string) {
    if (!window.confirm(t("Delete this execution and all of its artifacts?"))) return;
    setError(null);
    try {
      await api.deleteRun(targetRunId);
      if (runId === targetRunId) { setRunId(null); setRunStatus(null); setWorkspace(null); }
      await refreshAutomation();
    } catch (caught) { setError(messageOf(caught)); }
  }

  const lifecycle = automationView({ sourceId, runId, runStatus, workspace, advancedGraph });
  const switchView = (view: "editor" | "executions") => { setActiveView(view); setParams({ automation: automationId, ...(runId ? { run: runId } : {}), ...(view === "executions" ? { view: "executions" } : {}) }, { replace: true }); };

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-sunken" onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files); }}>
      <input ref={fileInput} type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.parquet,.pdf" className="hidden" onChange={(event) => { void upload(event.target.files ?? []); event.target.value = ""; }} />
      <header className="relative flex h-[58px] shrink-0 items-center border-b border-line bg-surface px-4">
        <input value={name} onChange={(event) => setName(event.target.value)} onBlur={() => void persistName()} aria-label={t("Automation name")} className="min-w-0 w-[320px] max-w-[32vw] border-0 bg-transparent text-sm font-semibold text-ink outline-none" />
        <div className="absolute left-1/2 flex -translate-x-1/2 rounded-lg bg-surface-sunken p-1">{(["editor", "executions"] as const).map((view) => <button key={view} type="button" onClick={() => switchView(view)} className={cx("rounded-md px-4 py-1.5 text-xs font-medium", activeView === view ? "bg-surface text-ink shadow-sm" : "text-ink-mute")}>{t(view === "editor" ? "Editor" : "Executions")}</button>)}</div>
        <div className="ml-auto flex items-center gap-2">{activeView === "editor" && <select aria-label={t("Choose uploaded data")} title={t("Choose uploaded data")} value={sourceId} onChange={(event) => void selectExistingSource(event.target.value)} className="h-8 max-w-[220px] rounded-lg border border-line bg-surface px-2 text-[11px] text-ink"><option value="">{t("Choose uploaded data")}</option>{sources.map((source) => <option key={source.source_id} value={source.source_id}>{source.label}{source.files?.length ? ` · ${source.files.length} ${t("files")}` : ""}</option>)}</select>}{activeView === "editor" && <button type="button" disabled={!automation} className="btn-ghost !h-8 !w-8 !p-0 text-lg disabled:opacity-40" title={t("Add files")} onClick={() => fileInput.current?.click()}>+</button>}<LanguagePicker /></div>
      </header>
      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
      {/* A stage gate that escalated to a human: shown here, above the run, so
          it is reachable regardless of tab -- the run cannot resume until it is
          answered (#81). */}
      {runId && pendingQuestion?.human_prompt && <div className="mx-4 mt-3 shrink-0"><ApprovalCard runId={runId} decision={pendingQuestion} onAnswered={onGateAnswered} /></div>}
      {/* Live upload progress with a working cancel: an accidental large file
          can be aborted instead of forcing a tab refresh (#84). */}
      {uploading && uploadProgress && (
        <div className="mx-4 mt-3 flex shrink-0 items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2 text-xs text-ink">
              <span className="truncate">{t("Uploading {name}", { name: uploadProgress.name })}{uploadProgress.total > 1 ? ` (${uploadProgress.index + 1}/${uploadProgress.total})` : ""}</span>
              <span className="shrink-0 text-ink-mute">{Math.round(uploadProgress.fraction * 100)}%</span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-sunken"><div className="h-full rounded-full bg-brand-500 transition-[width]" style={{ width: `${Math.round(uploadProgress.fraction * 100)}%` }} /></div>
          </div>
          <button type="button" className="btn-ghost !py-1 text-xs text-stop-700" onClick={cancelUpload}>{t("Cancel")}</button>
        </div>
      )}
      {dragging && <div className="pointer-events-none absolute inset-4 z-50 grid place-items-center rounded-2xl border-2 border-dashed border-brand-500 bg-brand-50/95 text-sm font-semibold text-brand-700">{t("Drop files to add them as one source")}</div>}
      <main className="min-h-0 flex-1">{activeView === "executions" ? <ExecutionHistory executions={executions} busy={busy} selectedRunId={runId ?? params.get("run")} onPause={(id) => void api.pauseRun(id).then(() => refreshAutomation()).catch((caught) => setError(messageOf(caught)))} onRetry={() => void retryRun()} onDelete={(id) => void deleteExecution(id)} /> : <>{lifecycle === "empty" && <button type="button" disabled={!automation} onClick={() => fileInput.current?.click()} className="grid h-full w-full place-items-center bg-[radial-gradient(#d9e0ea_1px,transparent_1px)] [background-size:20px_20px] p-8 text-left"><div className="w-full max-w-xl rounded-2xl border-2 border-dashed border-line bg-surface px-8 py-12 text-center shadow-card"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Guided data science")}</p><Empty title={t("Start with unfamiliar files")} hint={t("Upload new files here or choose a reusable source from the data selector above.")} /><span className="btn-primary mt-6">{uploading ? t("Uploading…") : `+ ${t("Upload files")}`}</span><ol className="mt-8 grid grid-cols-5 gap-2 text-[9px] text-ink-mute">{[t("Intake"), t("Understand"), t("Choose ML inputs"), t("Accept plan"), t("Run and review")].map((step, index) => <li key={step}><span className="mx-auto mb-1 grid h-5 w-5 place-items-center rounded-full bg-brand-50 font-semibold text-brand-700">{index + 1}</span>{step}</li>)}</ol></div></button>}{lifecycle === "source" && !profile && <div className="grid h-full place-items-center"><Spinner label={t("Inspecting and routing uploaded files…")} /></div>}{lifecycle === "source" && profile && <SourceSummary profile={profile} onStart={() => void startUnderstanding()} busy={busy} reuseCache={reuseCache} onReuseCache={setReuseCache} onRemoveFile={(name) => void removeSourceFile(name)} onAddFiles={() => fileInput.current?.click()} />}{lifecycle === "understanding" && profile && <UnderstandingProgress profile={profile} runId={runId} workspace={workspace} onRetry={() => void retryRun()} />}{lifecycle === "proposal" && profile && workspace && runId && <UnderstandingAndProposal profile={profile} workspace={workspace} sourceId={sourceId} runId={runId} onWorkspaceUpdated={applyWorkspace} onAccept={() => void acceptPlan()} onAdvanced={() => setAdvancedGraph(true)} busy={busy} />}{lifecycle === "guided_pipeline" && profile && workspace && runId && <GuidedPipeline runId={runId} profile={profile} workspace={workspace} componentOutputs={workspace.component_outputs ?? []} runStatus={runStatus} busy={busy} onRun={() => void runAcceptedWorkflow()} onPause={() => void pauseAcceptedWorkflow()} onRetry={() => void retryRun()} onAdvanced={() => setAdvancedGraph(true)} onOpenExecutions={() => switchView("executions")} />}{lifecycle === "workflow" && blueprint && <PipelineBuilder runId={runId} baseArtifactId={workspace?.artifact_id ?? null} blueprint={blueprint} layout={workspace?.pipeline_layout ?? automation?.pipeline_layout} componentOutputs={workspace?.component_outputs ?? []} onChange={(next) => setBlueprint(next)} onSaved={(next) => applyWorkspace(next)} onExitAdvanced={() => setAdvancedGraph(false)} />}</>}</main>
    </div>
  );
}

function ExecutionHistory({ executions, busy, selectedRunId, onPause, onRetry, onDelete }: { executions: RunSummary[]; busy: boolean; selectedRunId: string | null; onPause: (runId: string) => void; onRetry: (runId: string) => void; onDelete: (runId: string) => void }) {
  const [selected, setSelected] = useState<RunSummary | null>(() => preferredExecution(executions, selectedRunId));
  // #68: honour the run named in the deep-link URL over executions[0]. The list
  // loads asynchronously, so the URL run may only appear on a later render;
  // apply it once it does, and again if the URL names a different run. A manual
  // pick in the list does not change selectedRunId, so it is never overridden.
  const appliedRun = useRef<string | null>(selected?.run_id === selectedRunId ? selectedRunId : null);
  useEffect(() => {
    const urlMatch = selectedRunId ? executions.find((item) => item.run_id === selectedRunId) : undefined;
    if (urlMatch && appliedRun.current !== selectedRunId) { setSelected(urlMatch); appliedRun.current = selectedRunId; return; }
    if (!selected || !executions.some((item) => item.run_id === selected.run_id)) setSelected(preferredExecution(executions, selectedRunId));
  }, [executions, selectedRunId, selected]);
  const active = selected && ["queued", "staging", "running", "resuming"].includes(selected.status);
  const retryable = selected && ["failed", "interrupted", "aborted", "awaiting_human"].includes(selected.status);
  return <div className="h-full overflow-y-auto bg-surface-sunken p-6"><div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[1fr_1.4fr]"><section><h2 className="text-sm font-semibold text-ink">{t("Execution history")}</h2><div className="mt-3 space-y-2">{executions.map((item, index) => <button key={item.run_id} type="button" onClick={() => setSelected(item)} className={cx("flex w-full items-center gap-3 rounded-xl border bg-surface px-4 py-3 text-left", selected?.run_id === item.run_id ? "border-brand-400 ring-2 ring-brand-100" : "border-line")}><span className="text-xs font-semibold text-ink">#{executions.length - index}</span><Badge tone={item.status === "completed" ? "ok" : item.status === "failed" ? "stop" : "brand"}>{t(item.status)}</Badge><span className="ml-auto text-[10px] text-ink-faint">{item.last_activity ? new Date(item.last_activity).toLocaleString() : "—"}</span></button>)}{!executions.length && <Empty title={t("Never executed")} hint={t("Accepted workflow runs will appear here without changing the saved editor graph.")} />}</div></section>{selected && <section className="rounded-xl border border-line bg-surface p-5 shadow-card"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Selected execution")}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><ExecutionFact label={t("Status")} value={t(selected.status)} /><ExecutionFact label={t("Artifacts")} value={selected.artifact_count ?? 0} /><ExecutionFact label={t("Completed stages")} value={selected.stages?.length ?? 0} /><ExecutionFact label={t("Last activity")} value={selected.last_activity ? new Date(selected.last_activity).toLocaleString() : "—"} /></div><div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">{active && <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => onPause(selected.run_id)}>{t("Pause after current stage")}</button>}{retryable && <button type="button" className="btn-primary text-xs" disabled={busy} onClick={() => onRetry(selected.run_id)}>{t(selected.status === "awaiting_human" ? "Retry current component" : "Retry run")}</button>}{!active && <button type="button" className="btn-ghost text-xs text-stop-700" disabled={busy} onClick={() => onDelete(selected.run_id)}>{t("Delete execution")}</button>}</div><p className="mt-4 text-xs text-ink-mute">{t("Select a completed node in the Editor to inspect its readable artifacts and evidence.")}</p></section>}</div></div>;
}

function ExecutionFact({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-[10px] text-ink-faint">{label}</p><p className="mt-1 text-sm font-semibold text-ink">{value}</p></div>; }
function messageOf(caught: unknown): string { return caught instanceof Error ? caught.message : String(caught); }
