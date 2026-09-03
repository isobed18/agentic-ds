import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PipelineBuilder } from "../components/PipelineBuilder";
import { GuidedPipeline } from "../components/GuidedPipeline";
import { ApprovalCard } from "../components/GateApproval";
import { ProblemTargetDialog } from "../components/ProblemTargetDialog";
import { targetColumnGroups } from "../components/targetColumns";
import { LanguagePicker } from "../components/Shell";
import { Notifications } from "../components/Notifications";
import {
  DockedPanel,
  SourceSummary,
  UnderstandingProgress,
} from "../components/UnderstandingWorkspace";
import { PlannerPanel } from "../components/PlannerPanel";
import { automationOrigin, automationParams, automationView, availableProjectViews, preferredExecution, projectReturnParams, projectView, type WorkspaceView } from "../components/automationWorkspaceState";
import { ProjectContentsPanel } from "../components/ProjectContents";
import { runErrorText } from "../components/stageFailure";
import { Badge, Empty, NAME_FIELD_WIDTH, Reload, Spinner, cx } from "../components/ui";
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
import { isRunActive } from "../lib/status";
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
  const [busy, setBusy] = useState(false);
  const [advancedGraph, setAdvancedGraph] = useState(false);
  // #464: the gate card is the screen that asks the question, and the manual
  // target selector lived somewhere else entirely -- nested in the failure box
  // of a docked panel, reachable only for a *failed* run. Same dialog, opened
  // from here too.
  const [reframing, setReframing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // #465: the banner is fed from the run in two places and both wrote it
  // conditionally -- `if (message) setError(message)` -- so an error that had
  // *gone away* on the server could not clear it. Pinning a target restarts the
  // run and clears `runtime.error`, and the red box stayed exactly as it was
  // until a page refresh.
  //
  // Cleared only when the banner is still showing the run's own last error.
  // A blanket `setError(message || null)` would also wipe a client-side failure
  // -- a pause that was refused, an artifact that would not load -- two seconds
  // after it appeared, which is the opposite defect.
  const shownRunError = useRef<string | null>(null);
  function applyRunError(message: string) {
    setError((current) => (message ? message : current === shownRunError.current ? null : current));
    shownRunError.current = message || null;
  }

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
      if (!cancelled) applyRunError(runErrorText(progress?.error) ?? "");
      const saved = await api.stagingWorkspace(previous.run_id).catch(() => null);
      if (!cancelled && saved) applyWorkspace(saved);
    }).catch((caught) => setError(messageOf(caught))).finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [automationId, sourceId, automation?.automation_id]);

  useEffect(() => {
    if (!runId || !isRunActive(runStatus)) return;
    const timer = window.setInterval(() => { void api.runProgress(runId).then(async (progress) => { setRunStatus(String(progress.status ?? runStatus)); setPendingQuestion((progress.pending_question as GateDecision | null) ?? null); applyRunError(runErrorText(progress.error) ?? ""); const saved = await api.stagingWorkspace(runId).catch(() => null); if (saved) applyWorkspace(saved); }).catch((caught) => setError(messageOf(caught))); }, 2200);
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

  // #462: promoting tables is not a synchronous state change.
  // `_replan_after_promotion` puts the run back into `staging` and re-enters
  // the graph at `intake` on a background worker, so the re-authored plan does
  // not exist yet when the promote call returns. Every promotion callback
  // re-read the staging workspace once, immediately -- storing the
  // pre-promotion snapshot and never looking again -- and
  // `UnderstandingProgress`'s callback was a literal no-op, on the assumption
  // that this page polls while understanding is live. It does not: the poll is
  // gated on `isRunActive`, and `staged`/`awaiting_human` are not active, so it
  // has already stopped by the time the review dialog is reachable.
  //
  // Re-reading the *run* is what fixes it. The server flips the status back to
  // `staging` under its lock before starting the worker, so it is already
  // `staging` when this response lands -- and `staging` is an active status, so
  // the poll effect above resumes on its own and carries the canvas through to
  // the re-authored plan. `applyWorkspace` never touched `runStatus`, which is
  // exactly why a page refresh was the only thing that worked.
  //
  // Sequential, run first: concurrent reads could take the workspace while the
  // worker was still re-profiling and the status after it finished, leaving a
  // stale plan with no poll to correct it. Reading the run first means a
  // `staged` answer proves the worker is done, because it writes the workspace
  // before it flips the status.
  //
  // The terminal `replan` outcomes -- `plan_accepted`, `run_active`,
  // `nothing_promoted`, `unavailable` -- start no worker, so the status comes
  // back unchanged and this is the single refresh they need. Deliberately not
  // branching on the returned string: the run's own status is the truth, and a
  // client that reasons about the string breaks the moment a new outcome is
  // added server-side.
  //
  // #465: pinning a target is the same shape of event -- the server clears the
  // run's error, sets it `resuming` and re-enters the graph at
  // `problem_discovery` -- and had the same three failures: nothing told this
  // component, so the banner kept its stale text, `runStatus` stayed `failed`,
  // and both polls stayed parked. One function, used by both, named for what it
  // does rather than for one of the two things that cause it.
  const refreshRun = useCallback(async () => {
    if (!runId) return;
    const progress = await api.runProgress(runId).catch(() => null);
    if (progress) {
      setRunStatus(String(progress.status ?? ""));
      setPendingQuestion((progress.pending_question as GateDecision | null) ?? null);
      applyRunError(runErrorText(progress.error) ?? "");
    }
    const saved = await api.stagingWorkspace(runId).catch(() => null);
    if (saved) applyWorkspace(saved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

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

  // #425: an empty field is a cancelled edit, not a rename to "". Blurring it
  // used to PUT `{ name: "" }`, which fails `min_length=1` on the contract, so
  // the reader got a stringified pydantic error -- field path, error code and
  // an errors.pydantic.dev link, in English, on a Turkish screen -- and the
  // box stayed blank, hiding the automation's real name until a reload while
  // every further click away fired the same failing request again. Restoring on
  // failure covers the renames that do reach the server and are rejected
  // there, for the same reason: the name on screen should never be one the
  // automation does not have. `ProjectWorkspace.persistName` has both guards.
  async function persistName() {
    const trimmed = name.trim();
    if (!automation || !trimmed || trimmed === automation.name) { setName(automation?.name ?? name); return; }
    try { const saved = await api.updateAutomationSafely(automationId, automation.revision, { name: trimmed }); setAutomation(saved); setName(saved.name); }
    catch (caught) { setError(messageOf(caught)); setName(automation.name); }
  }

  async function startUnderstanding() {
    if (!sourceId || busy) return; setBusy(true); setError(null);
    try { const staged = await api.stageRun(sourceId, false, blueprint, automationId); setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null); setParams({ ...automationParams(projectId, automationId, origin), run: staged.run_id }, { replace: true }); await refreshAutomation(); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  async function acceptPlan() {
    if (!runId || !workspace || busy) return; setBusy(true); setError(null);
    try { applyWorkspace(await api.acceptStagingPlanSafely(runId, workspace.artifact_id)); setAdvancedGraph(false); }
    catch (caught) { setError(messageOf(caught)); } finally { setBusy(false); }
  }

  // #166 second path: `runMode` carries the person's choice to approve every
  // stage. `manual` declares every stage a checkpoint, so the run stops after
  // each one for a human decision instead of the agent deciding the gate.
  async function runAcceptedWorkflow(runMode: "fully_auto" | "manual" = "fully_auto", targetColumn: string | null = null, problemKind: "predict_column" | null = null, checkpointStages: string[] | null = null) {
    if (!runId || !workspace || !blueprint || busy) return; setBusy(true); setError(null);
    // #244/#198: the guided run used to pass nothing but the run mode, so a
    // chosen target never reached the pipeline. Carry the picker's column into
    // the run configuration; problem discovery honours it as guidance.
    // #241: `problemKind` is a quick-pick, not a hint -- it makes
    // problem_discovery build the ProblemDefinition straight from it, with no
    // planner conversation, for the common shapes a person can just name.
    try {
      const hasTrainableTables = Boolean(profile?.tables.length || workspace?.promoted_document_tables?.length);
      if (!hasTrainableTables) { setError(t("This accepted plan is document/report analysis only; no ML run is implied.")); return; }
      if (blueprint.components.some((component) => component.enabled && component.branch_id)) { await api.startAutomationBranches(runId); setRunStatus("branches_running"); } else { await api.startStaged(runId, { run_mode: runMode, ...(targetColumn ? { target_column: targetColumn } : {}), ...(problemKind ? { problem_selection: { kind: problemKind, target_column: targetColumn } } : {}), ...(checkpointStages ? { supervision: { checkpoint_stages: checkpointStages } } : {}) }); setRunStatus("running"); }
    }
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
        const staged = await api.stageRun(sourceId, false, blueprint, automationId);
        setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null);
        setParams({ ...automationParams(projectId, automationId, origin), run: staged.run_id }, { replace: true });
        await refreshAutomation();
      }
    } catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
  }

  // #247: a deliberate second execution of the current automation, not error
  // recovery -- same shape as startUnderstanding/retryRun's staged-run path,
  // but carries the original run's recorded seed forward (`api.rerun`) and
  // leaves both the saved graph and the run being repeated untouched.
  async function rerunAutomation() {
    if (!runId || busy) return;
    setBusy(true); setError(null);
    try {
      const staged = await api.rerun(runId);
      setRunId(staged.run_id); setRunStatus(staged.status); setWorkspace(null);
      setParams({ ...automationParams(projectId, automationId, origin), run: staged.run_id }, { replace: true });
      await refreshAutomation();
    } catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
  }

  // #443: Execution history could describe a past run but never open one. The
  // detail panel's action row held Pause and Retry, so a *completed* run --
  // the common case -- rendered an empty row and offered nothing at all, while
  // its own footer told the reader to "select a completed node in the Editor"
  // and gave no way to point the Editor at the run being described. Clicking a
  // row moved a local highlight and no more.
  //
  // The workspace's active run is `runId` here, so switching it is these two
  // fetches plus the state they fill. It cannot be left to the mount effect
  // that already makes them: that effect is keyed on
  // `[automationId, sourceId, automation?.automation_id]` and does not re-run
  // when the run param changes, so writing the URL alone would change the link
  // and nothing on screen. Every field is set from the resolved responses in
  // one pass rather than cleared first, so the graph never flashes empty
  // between the run being left and the one being opened.
  async function openExecution(targetRunId: string) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const [progress, saved] = await Promise.all([
        api.runProgress(targetRunId).catch(() => null),
        api.stagingWorkspace(targetRunId).catch(() => null),
      ]);
      const chosen = executions.find((item) => item.run_id === targetRunId);
      setRunId(targetRunId);
      setRunStatus(String(progress?.status ?? chosen?.status ?? ""));
      setPendingQuestion((progress?.pending_question as GateDecision | null) ?? null);
      setWorkspace(saved);
      setBlueprint(saved?.pipeline_blueprint ?? automation?.pipeline_blueprint ?? null);
      setAdvancedGraph(false);
      // A failure recorded on the run being opened, and only that one -- the
      // banner otherwise keeps showing the error of the run just left.
      setError(runErrorText(progress?.error) || null);
      setActiveView("editor");
      // #68's contract, written the same way `applyWorkspace` writes it, so an
      // external `?view=runs&run=<id>` link and this button agree on which run
      // the workspace is showing.
      setParams({ ...automationParams(projectId, automationId, origin), run: targetRunId }, { replace: true });
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

  // #214: `proposal` and `guided_pipeline` are still two states, but they are no
  // longer two screens. Both render the one automation canvas; the state decides
  // whether its toolbar offers Accept or Run, and whether the ML nodes on that
  // canvas are faded or live.
  const lifecycle = automationView({ sourceId, runId, runStatus, workspace, advancedGraph });
  const views = availableProjectViews(contents);
  // The automation level always exposes its graph, selected data, runs, models,
  // and reports. Empty output pages are useful truth, not tabs that disappear.
  useEffect(() => {
    if (contents && !views.includes(activeView)) setActiveView("editor");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contents]);
  // #97: the Planner is consultable while the pipeline runs and while a gate
  // waits on a human, not only during staging.
  // #378: the Planner opener and its panel used to live inside GuidedPipeline,
  // which AutomationWorkspace only mounts for two of its five lifecycle states.
  // So during input selection, source routing and understanding there was no
  // way to open the Planner at all -- exactly when a person has questions about
  // their files. PlannerPanel already copes: it accepts a null runId and
  // re-titles itself "Ask about this data" when it has only a source.
  const [plannerOpen, setPlannerOpen] = useState(false);
  // Until the plan is accepted, what the reader is deciding is the plan, so the
  // staging prompts stand (#97).
  const plannerPrompts = lifecycle === "guided_pipeline"
    ? [t("What columns are in this data?"), t("Rank the best target columns and ML problems."), t("Which relationships matter for prediction?")]
    : [t("What are these files?"), t("Which relationships are measured?"), t("Are the PDFs contextual evidence?"), t("Stop after EDA so I can inspect it.")];
  // Re-running needs a recorded execution to re-run, and cannot start a second
  // one on top of a live run. Disabled rather than hidden, so the control stays
  // in one predictable place and its state explains itself.
  const canRerun = Boolean(runId) && !busy && !isRunActive(runStatus);

  const switchView = (view: WorkspaceView) => { setActiveView(view); setParams({ ...automationParams(projectId, automationId, origin), ...(runId ? { run: runId } : {}), ...(view !== "editor" ? { view } : {}) }, { replace: true }); };

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface-sunken">
      <header className="relative flex h-[3.625rem] shrink-0 items-center border-b border-line bg-surface px-4">
        <button type="button" className="btn-ghost mr-2 !px-2 text-xs" onClick={() => setParams(projectReturnParams(projectId, origin))}>← {t("Project overview")}</button>
        <input value={name} onChange={(event) => setName(event.target.value)} onBlur={() => void persistName()} aria-label={t("Automation name")} title={name} className={cx("truncate rounded-lg border border-line bg-surface-sunken px-3 py-1.5 text-sm font-semibold text-ink outline-none", NAME_FIELD_WIDTH)} />
        {/* #378: an automation-level action -- a new execution recorded in
            Execution history (#247) -- so it belongs beside the automation's
            name rather than in one canvas's toolbar pill, where it existed only
            after the plan was accepted and vanished again during every run. */}
        <button type="button" aria-label={t("Re-run this automation")} title={t("Re-run this automation")} className="ml-2 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-soft transition hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40" onClick={() => void rerunAutomation()} disabled={!canRerun}><Reload /></button>
        <div className="absolute left-1/2 flex -translate-x-1/2 rounded-lg bg-surface-sunken p-1">{views.map((view) => <button key={view} type="button" onClick={() => switchView(view)} className={cx("rounded-md px-4 py-1.5 text-xs font-medium", activeView === view ? "bg-surface text-ink shadow-sm" : "text-ink-mute")}>{viewLabel(view)}</button>)}</div>
        {/* #157/#165: uploaded data is managed by the project. The contradictory
            top-right global source picker and upload button are intentionally gone. */}
        {/* #286: the shared top bar is suppressed for every page inside a
            project, so this contextual header is the only place notifications
            can live here. Composed in rather than duplicated: it is the same
            component the shell renders everywhere else. */}
        <div className="ml-auto flex items-center gap-1"><LanguagePicker /><Notifications /></div>
      </header>
      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      {/* A stage gate that escalated to a human: shown here, above the run, so
          it is reachable regardless of tab -- the run cannot resume until it is
          answered (#81). */}
      {runId && pendingQuestion?.human_prompt && <div className="mx-4 mt-3 shrink-0"><ApprovalCard key={`${pendingQuestion.stage_id}:${pendingQuestion.attempt}`} runId={runId} decision={pendingQuestion} onAnswered={onGateAnswered} onReframe={profile ? () => setReframing(true) : undefined} /></div>}
      {/* #378: the Planner docks beside whatever the workspace is showing, for
          every lifecycle state, rather than only beside the guided canvas. */}
      <div className="flex min-h-0 flex-1">
      <main className="min-h-0 min-w-0 flex-1">{activeView === "executions" ? <ExecutionHistory executions={executions} busy={busy} selectedRunId={runId ?? params.get("run")} onOpen={(id) => void openExecution(id)} onPause={(id) => void api.pauseRun(id).then(() => refreshAutomation()).catch((caught) => setError(messageOf(caught)))} onRetry={() => void retryRun()} onDelete={(id) => void deleteExecution(id)} /> : activeView === "data" || activeView === "models" || activeView === "reports" ? <ProjectContentsPanel view={activeView} contents={contents} loading={busy} onChanged={() => void refreshAutomation()} /> : <>{lifecycle === "empty" && automation && <AutomationInputSelector projectId={projectId} automation={automation} onSelected={(saved) => { setAutomation(saved); setSourceId(saved.source_id ?? ""); void refreshAutomation(); }} onProjectData={() => setParams({ project: projectId, view: "data" })} />}{lifecycle === "source" && !profile && <div className="grid h-full place-items-center"><Spinner label={t("Inspecting and routing selected files…")} /></div>}{lifecycle === "source" && profile && <SourceSummary profile={profile} onStart={() => void startUnderstanding()} busy={busy} />}{lifecycle === "understanding" && profile && <UnderstandingProgress profile={profile} runId={runId} workspace={workspace} onWorkspaceUpdated={applyWorkspace} onPromoted={() => void refreshRun()} />}{(lifecycle === "proposal" || lifecycle === "guided_pipeline") && profile && workspace && runId && <GuidedPipeline runId={runId} profile={profile} workspace={workspace} onPromoted={() => void refreshRun()} onReframed={() => void refreshRun()} accepted={lifecycle === "guided_pipeline"} runStatus={runStatus} busy={busy} onAccept={() => void acceptPlan()} onWorkspaceUpdated={applyWorkspace} onRun={(runMode, target, problemKind, checkpointStages) => void runAcceptedWorkflow(runMode, target, problemKind, checkpointStages)} onPause={() => void pauseAcceptedWorkflow()} onRetry={() => void retryRun()} onOpenPlanner={() => setPlannerOpen(true)} onAdvanced={() => setAdvancedGraph(true)} />}{lifecycle === "workflow" && blueprint && <PipelineBuilder runId={runId} sourceId={sourceId} baseArtifactId={workspace?.artifact_id ?? null} blueprint={blueprint} layout={workspace?.pipeline_layout ?? automation?.pipeline_layout} componentOutputs={workspace?.component_outputs ?? []} onChange={(next) => setBlueprint(next)} onSaved={(next) => applyWorkspace(next)} onExitAdvanced={() => setAdvancedGraph(false)} />}</>}</main>
      {plannerOpen && <div className="min-h-0 w-[min(390px,94vw)] shrink-0"><DockedPanel><PlannerPanel runId={runId} sourceId={sourceId || profile?.source_id || null} open onToggle={() => setPlannerOpen(false)} onWorkspaceUpdated={applyWorkspace} starterPrompts={plannerPrompts} /></DockedPanel></div>}
      </div>
      {/* #464: mounted at the page so the gate card can open it. The failed-run
          path opens the same component from inside the canvas -- one panel,
          two ways in, as the issue asks. */}
      {reframing && runId && profile && <ProblemTargetDialog runId={runId} groups={targetColumnGroups(profile, workspace)} onClose={() => setReframing(false)} onPinned={() => void onGateAnswered()} />}
      {/* One opener, one place on screen, whatever the workspace is doing. The
          other tabs are project-contents lists rather than this automation's
          work, so it keeps to the editor. */}
      {activeView === "editor" && <div className="fixed bottom-6 left-1/2 z-30 -translate-x-1/2">
        <button type="button" className="btn-primary text-xs shadow-pop" aria-expanded={plannerOpen} onClick={() => setPlannerOpen((open) => !open)}>{t("Chat with Planner")}</button>
      </div>}
    </div>
  );
}

function ExecutionHistory({ executions, busy, selectedRunId, onOpen, onPause, onRetry, onDelete }: { executions: RunSummary[]; busy: boolean; selectedRunId: string | null; onOpen: (runId: string) => void; onPause: (runId: string) => void; onRetry: (runId: string) => void; onDelete: (runId: string) => void }) {
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
  const active = selected && isRunActive(selected.status);
  const retryable = selected && ["failed", "interrupted", "aborted", "awaiting_human"].includes(selected.status);
  return <div className="h-full overflow-y-auto bg-surface-sunken p-6"><div className="mx-auto grid max-w-5xl gap-5 lg:grid-cols-[1fr_1.4fr]"><section><h2 className="text-sm font-semibold text-ink">{t("Execution history")}</h2><div className="mt-3 space-y-2">{executions.map((item, index) => <div key={item.run_id} className={cx("flex items-center gap-1 rounded-xl border bg-surface pr-2", selected?.run_id === item.run_id ? "border-brand-400 ring-2 ring-brand-100" : "border-line")}><button type="button" onClick={() => setSelected(item)} className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left"><span className="text-xs font-semibold text-ink">#{executions.length - index}</span><Badge tone={item.status === "completed" ? "ok" : item.status === "failed" ? "stop" : "brand"}>{t(item.status)}</Badge><span className="ml-auto text-3xs text-ink-faint">{item.last_activity ? new Date(item.last_activity).toLocaleString() : "—"}</span></button>{/* #443: the row click selects, as it always did -- reading a run's
                facts and switching the whole workspace to it are different
                intentions, and one click cannot mean both. This is the second
                one, beside Delete, so the list is not a dead end either. */}
                <button type="button" aria-label={t("Open this execution in the Editor")} title={t("Open this execution in the Editor")} disabled={busy} onClick={() => onOpen(item.run_id)} className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-faint transition hover:bg-brand-50 hover:text-brand-700"><OpenIcon /></button>
                {!isRunActive(item.status) && <button type="button" aria-label={t("Delete execution")} title={t("Delete execution")} disabled={busy} onClick={() => onDelete(item.run_id)} className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700"><TrashIcon /></button>}</div>)}{!executions.length && <Empty title={t("Never executed")} hint={t("Accepted workflow runs will appear here without changing the saved editor graph.")} />}</div></section>{selected && <section className="rounded-xl border border-line bg-surface p-5 shadow-card"><p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("Selected execution")}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><ExecutionFact label={t("Status")} value={t(selected.status)} /><ExecutionFact label={t("Artifacts")} value={selected.artifact_count ?? 0} /><ExecutionFact label={t("Completed stages")} value={selected.stages?.length ?? 0} /><ExecutionFact label={t("Last activity")} value={selected.last_activity ? new Date(selected.last_activity).toLocaleString() : "—"} /></div><div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">{/* #443: the action every run has, listed first and styled as the
              primary one. It is the only one a completed run has at all, and
              on a failed run it is also the safer first move -- "Retry run"
              stages a new execution, so it is a decision to take after looking
              at this one rather than instead of it. */}
              <button type="button" className="btn-primary text-xs" disabled={busy} onClick={() => onOpen(selected.run_id)}>{t("Open in the Editor")}</button>{active && <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => onPause(selected.run_id)}>{t("Pause after current stage")}</button>}{retryable && <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => onRetry(selected.run_id)}>{t(selected.status === "awaiting_human" ? "Retry current component" : "Retry run")}</button>}</div><p className="mt-4 text-xs text-ink-mute">{t("Open this execution to draw its graph, artifacts and evidence in the Editor.")}</p></section>}</div></div>;
}

function ExecutionFact({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-3xs text-ink-faint">{label}</p><p className="mt-1 text-sm font-semibold text-ink">{value}</p></div>; }

function OpenIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M11.5 4.5h4v4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M15.5 4.5L9 11" strokeLinecap="round" />
      <path d="M14 12.25v2.25c0 .69-.56 1.25-1.25 1.25h-7c-.69 0-1.25-.56-1.25-1.25v-7c0-.69.56-1.25 1.25-1.25H8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3.75 5.5h12.5M8 5.5V4.25c0-.41.34-.75.75-.75h2.5c.41 0 .75.34.75.75V5.5" strokeLinecap="round" />
      <path d="M5.75 5.5l.62 9.9c.04.61.55 1.1 1.16 1.1h4.94c.61 0 1.12-.49 1.16-1.1l.62-9.9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8.5 8.5v5M11.5 8.5v5" strokeLinecap="round" />
    </svg>
  );
}
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
