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
import { automationOrigin, automationParams, automationView, availableProjectViews, preferredExecution, projectReturnParams, projectView, type WorkspaceView } from "../components/automationWorkspaceState";
import { ProjectContentsPanel } from "../components/ProjectContents";
import { Badge, Empty, Spinner, cx } from "../components/ui";
import {
  api,
  type AutomationContents,
  type AutomationDefinition,
  type GateDecision,
  type PipelineBlueprint,
  type RunSummary,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { t } from "../lib/i18n";
import { AutomationInputSelector, ProjectLibrary, ProjectWorkspace } from "./ProjectWorkspace";

export function AutomationWorkspace() {
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project");
  const automationId = params.get("automation");
  if (!projectId) {
    return <ProjectLibrary onOpen={(id) => setParams({ project: id })} />;
  }
  if (!automationId) {
    return <ProjectWorkspace projectId={projectId} view={projectView(params.get("view"))} onView={(view) => setParams({ project: projectId, ...(view === "overview" ? {} : { view }) })} onBack={() => setParams({})} onOpenAutomation={(id) => setParams(automationParams(projectId, id, automationOrigin(params.get("view"))))} />;
  }
  return <AutomationEditor projectId={projectId} automationId={automationId} />;
}

function AutomationEditor({ projectId, automationId }: { projectId: string; automationId: string }) {
  const [params, setParams] = useSearchParams();
  // The project tab this automation was opened from (#199). Every rewrite of
  // the automation's URL below goes through `automationParams` so the origin
  // survives tab switches and run selection, and "← Project overview" can put
  // the person back where they started instead of on the overview.
  const origin = automationOrigin(params.get("from"));
  const [automation, setAutomation] = useState<AutomationDefinition | null>(null);
  const [name, setName] = useState("");
  // Deep-links from /experiments, /workflows and notifications all write
  // `view=runs`, while the in-page tab switch writes `view=executions`. The
  // Executions panel only ever recognised "executions", so every external
  // deep-link silently landed on the Editor tab instead (#68). Accept both, and
  // the project-contained tabs (#80) by their own names.
  const [activeView, setActiveView] = useState<WorkspaceView>(() => initialView(params.get("view")));
  const [executions, setExecutions] = useState<RunSummary[]>([]);
  // The project's own data/models/reports, owned through its execution history
  // (#111). Drives both the project-contained tabs and which of them appear.
  const [contents, setContents] = useState<AutomationContents | null>(null);
  const [sourceId, setSourceId] = useState("");
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
  const [advancedGraph, setAdvancedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAutomation = useCallback(async () => {
    const [record, history] = await Promise.all([
      api.automation(automationId),
      api.automationExecutions(automationId),
    ]);
    setAutomation(record); setName(record.name); setSourceId(record.source_id ?? ""); setExecutions(history);
    // #157: this endpoint is automation-scoped. Project aggregation lives on
    // the parent screen and must never leak a sibling automation's output here.
    void api.automationContents(automationId).then(setContents).catch(() => setContents(null));
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
    setParams({ ...automationParams(projectId, automationId, origin), run: next.run_id }, { replace: true });
    void refreshAutomation().catch((caught) => setError(messageOf(caught)));
  }

  async function persistName() {
    if (!automation || name.trim() === automation.name) { setName(automation?.name ?? name); return; }
    try { const saved = await api.updateAutomationSafely(automationId, automation.revision, { name: name.trim() }); setAutomation(saved); setName(saved.name); }
    catch (caught) { setError(messageOf(caught)); }
  }

  async function startUnderstanding() {
    if (!sourceId || busy) return; setBusy(true); setError(null);
    try { const staged = await api.stageRun(sourceId, reuseCache, blueprint, automationId); setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null); setParams({ ...automationParams(projectId, automationId, origin), run: staged.run_id }, { replace: true }); await refreshAutomation(); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  async function acceptPlan() {
    if (!runId || !workspace || busy) return; setBusy(true); setError(null);
    try { applyWorkspace(await api.acceptStagingPlan(runId, workspace.artifact_id)); setAdvancedGraph(false); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  // #166 second path: `runMode` carries the person's choice to approve every
  // stage. `manual` declares every stage a checkpoint, so the run stops after
  // each one for a human decision instead of the agent deciding the gate.
  async function runAcceptedWorkflow(runMode: "fully_auto" | "manual" = "fully_auto") {
    if (!runId || !workspace || !blueprint || busy) return; setBusy(true); setError(null);
    try { if (!profile?.tables.length) { setError(t("This accepted plan is document/report analysis only; no ML run is implied.")); return; } if (blueprint.components.some((component) => component.enabled && component.branch_id)) { await api.startAutomationBranches(runId); setRunStatus("branches_running"); } else { await api.startStaged(runId, { run_mode: runMode }); setRunStatus("running"); } }
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
        setParams({ ...automationParams(projectId, automationId, origin), run: staged.run_id }, { replace: true });
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
  const views = availableProjectViews(contents);
  // The automation level always exposes its graph, selected data, runs, models,
  // and reports. Empty output pages are useful truth, not tabs that disappear.
  useEffect(() => {
    if (contents && !views.includes(activeView)) setActiveView("editor");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contents]);
  const switchView = (view: WorkspaceView) => { setActiveView(view); setParams({ ...automationParams(projectId, automationId, origin), ...(runId ? { run: runId } : {}), ...(view !== "editor" ? { view } : {}) }, { replace: true }); };

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-sunken">
      <header className="relative flex h-[58px] shrink-0 items-center border-b border-line bg-surface px-4">
        <button type="button" className="btn-ghost mr-2 !px-2 text-xs" onClick={() => setParams(projectReturnParams(projectId, origin))}>← {t("Project overview")}</button>
        <input value={name} onChange={(event) => setName(event.target.value)} onBlur={() => void persistName()} aria-label={t("Automation name")} className="min-w-0 w-[260px] max-w-[26vw] rounded-lg border border-line bg-surface-sunken px-3 py-1.5 text-sm font-semibold text-ink outline-none" />
        <div className="absolute left-1/2 flex -translate-x-1/2 rounded-lg bg-surface-sunken p-1">{views.map((view) => <button key={view} type="button" onClick={() => switchView(view)} className={cx("rounded-md px-4 py-1.5 text-xs font-medium", activeView === view ? "bg-surface text-ink shadow-sm" : "text-ink-mute")}>{viewLabel(view)}</button>)}</div>
        {/* #157/#165: uploaded data is managed by the project. The contradictory
            top-right global source picker and upload button are intentionally gone. */}
        <div className="ml-auto"><LanguagePicker /></div>
      </header>
      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
      {/* A stage gate that escalated to a human: shown here, above the run, so
          it is reachable regardless of tab -- the run cannot resume until it is
          answered (#81). */}
      {runId && pendingQuestion?.human_prompt && <div className="mx-4 mt-3 shrink-0"><ApprovalCard runId={runId} decision={pendingQuestion} onAnswered={onGateAnswered} /></div>}
      <main className="min-h-0 flex-1">{activeView === "executions" ? <ExecutionHistory executions={executions} busy={busy} selectedRunId={runId ?? params.get("run")} onPause={(id) => void api.pauseRun(id).then(() => refreshAutomation()).catch((caught) => setError(messageOf(caught)))} onRetry={() => void retryRun()} onDelete={(id) => void deleteExecution(id)} /> : activeView === "data" || activeView === "models" || activeView === "reports" ? <ProjectContentsPanel view={activeView} contents={contents} loading={busy} /> : <>{lifecycle === "empty" && automation && <AutomationInputSelector projectId={projectId} automation={automation} onSelected={(saved) => { setAutomation(saved); setSourceId(saved.source_id ?? ""); void refreshAutomation(); }} onProjectData={() => setParams({ project: projectId, view: "data" })} />}{lifecycle === "source" && !profile && <div className="grid h-full place-items-center"><Spinner label={t("Inspecting and routing selected files…")} /></div>}{lifecycle === "source" && profile && <SourceSummary profile={profile} onStart={() => void startUnderstanding()} busy={busy} reuseCache={reuseCache} onReuseCache={setReuseCache} />}{lifecycle === "understanding" && profile && <UnderstandingProgress profile={profile} runId={runId} workspace={workspace} onRetry={() => void retryRun()} />}{lifecycle === "proposal" && profile && workspace && runId && <UnderstandingAndProposal profile={profile} workspace={workspace} sourceId={sourceId} runId={runId} onWorkspaceUpdated={applyWorkspace} onAccept={() => void acceptPlan()} onAdvanced={() => setAdvancedGraph(true)} busy={busy} />}{lifecycle === "guided_pipeline" && profile && workspace && runId && <GuidedPipeline runId={runId} profile={profile} workspace={workspace} componentOutputs={workspace.component_outputs ?? []} runStatus={runStatus} busy={busy} onRun={(runMode) => void runAcceptedWorkflow(runMode)} onPause={() => void pauseAcceptedWorkflow()} onRetry={() => void retryRun()} onAdvanced={() => setAdvancedGraph(true)} onOpenExecutions={() => switchView("executions")} />}{lifecycle === "workflow" && blueprint && <PipelineBuilder runId={runId} sourceId={sourceId} baseArtifactId={workspace?.artifact_id ?? null} blueprint={blueprint} layout={workspace?.pipeline_layout ?? automation?.pipeline_layout} componentOutputs={workspace?.component_outputs ?? []} onChange={(next) => setBlueprint(next)} onSaved={(next) => applyWorkspace(next)} onExitAdvanced={() => setAdvancedGraph(false)} />}</>}</main>
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

/** The tab a deep-link opens on. `runs` is the legacy alias for executions (#68). */
function initialView(view: string | null): WorkspaceView {
  if (view === "runs") return "executions";
  if (view === "data" || view === "executions" || view === "models" || view === "reports") return view;
  return "editor";
}

// Each label is a literal inside t() so the catalogue scanner can see it -- a
// label reaching t() through a variable would sit untranslated unnoticed (#38).
function viewLabel(view: WorkspaceView): string {
  switch (view) {
    case "data":
      return t("Data");
    case "executions":
      return t("Executions");
    case "models":
      return t("Models");
    case "reports":
      return t("Reports");
    default:
      return t("Editor");
  }
}
