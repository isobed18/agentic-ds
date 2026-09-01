import { useEffect, useMemo, useState } from "react";
import {
  api,
  type ArtifactPreview,
  type RunProgressSnapshot,
  type SourceProfile,
  type StageDetail,
  type StagingWorkspace,
  type Workflow,
  type WorkflowNode,
} from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { elapsedLabel, isActive, isAttention, isSucceeded, statusLabel, isRunActive } from "../lib/status";
import { Badge, Empty, Pause, Play, Reload, cx } from "./ui";
import { ArtifactNodes } from "./ArtifactNodes";
import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip";
import { GROUPS, ML_SELECTIONS } from "./mlPipelineGroups";
import { NodeStatusHeader, StatusBadge, StatusMark } from "./NodeStatus";
import { PlannerPanel } from "./PlannerPanel";
import { stageName } from "./PipelineRail";
import { ResizableNode } from "./ResizableNode";
import { buildStagingRoutingState } from "./stagingRoutingState";
import {
  ArtifactDialog,
  CanvasSurface,
  DockedPanel,
  Inspector,
  RoutingGraph,
  RoutingInspector,
  type CanvasSelection,
} from "./UnderstandingWorkspace";

interface GuidedPipelineProps {
  runId: string;
  profile: SourceProfile;
  workspace: StagingWorkspace;
  /** Whether the plan has been accepted.
   *
   * #214: accepting no longer swaps this canvas for another one. The ML nodes
   * are drawn either way -- faded and pending while the plan is still a
   * proposal, live once it is accepted -- and this flag decides which of the
   * two the single toolbar offers: Accept, or Run/Pause/Retry. */
  accepted: boolean;
  runStatus: string | null;
  busy: boolean;
  onAccept: () => void;
  onWorkspaceUpdated: (workspace: StagingWorkspace) => void;
  // #244/#198: the run carries a chosen ML target column (or null to let
  // problem discovery propose one) so the guided flow can actually be aimed.
  // #241: `problemKind` set skips the planner conversation entirely -- the
  // named problem type is confirmed straight from the selector.
  onRun: (
    runMode: "fully_auto" | "manual",
    targetColumn: string | null,
    problemKind: "predict_column" | "flag_anomalies" | null,
  ) => void;
  onPause: () => void;
  onRetry: () => void;
  // #247: runs the current automation again from its recorded seed as a new
  // execution, deliberately -- not framed as retrying a failure the way
  // onRetry is, and reachable without leaving the workspace for Execution
  // history.
  onRerun: () => void;
  onAdvanced: () => void;
}

function local(value: { en: string; tr: string }): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

/** The whole automation graph, from uploaded files to the final report.
 *
 * #214: this used to be the ML half only. Accepting the plan swapped the
 * understanding canvas for this one, so uploaded files, intake, the routing
 * branches, synthesis and the proposal all disappeared the moment the pipeline
 * appeared, and the run opened on a "Data understood" tile standing in for the
 * seven nodes it had just discarded. There is one graph now: `RoutingGraph`
 * draws the staging half, the ML nodes hang off its "Proposed plan" node, and
 * accepting the plan edges those from faded to live without replacing what is
 * on screen. One canvas (`CanvasSurface`, which has zoom; `PanCanvas` did not),
 * one selection, one docked panel, one toolbar.
 */
export function GuidedPipeline({ runId, profile, workspace, accepted, runStatus, busy, onAccept, onWorkspaceUpdated, onRun, onPause, onRetry, onRerun, onAdvanced }: GuidedPipelineProps) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [progress, setProgress] = useState<RunProgressSnapshot | null>(null);
  // One selection for both halves of the graph. Staging ids ("discovery",
  // "synthesis", "proposal"…) and ML ids ("summary" plus the group ids) are
  // disjoint, so a click on Intake and a click on "Train and evaluate" reach
  // the same panel through the same piece of state (#214).
  const [selected, setSelected] = useState<string | null>(accepted ? "summary" : "synthesis");
  // #166 second path: before starting, a person may override the agent's gate
  // authority and demand approval at every stage. `manual` declares every stage
  // a checkpoint on the backend, so the run stops after each one for a human.
  const [approveEachStage, setApproveEachStage] = useState(false);
  const [detail, setDetail] = useState<StageDetail | null>(null);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  // #97: the "Chat with Planner" panel existed only in the staging/proposal
  // view and vanished the moment a plan was accepted -- including when a stage
  // gate escalates and asks for a human decision, exactly when consulting the
  // planner matters most. It lives here now too, reachable from the run toolbar.
  const [plannerOpen, setPlannerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // #244/#198: the guided flow had no way to choose the ML target, and the run
  // passed none at all -- the free-text gate box was never read on approve. The
  // picker is a dropdown of the base table's profiled columns, defaulting to
  // whatever target the plan already carries, shown before the run starts. Its
  // value is passed to the run as guidance for problem discovery.
  const planConfig = (workspace.recommended_plan?.configuration ?? {}) as Record<string, unknown>;
  const baseTableName = String(planConfig.base_table ?? profile.tables[0]?.name ?? "");
  const baseTable = profile.tables.find((table) => table.name === baseTableName) ?? profile.tables[0];
  const targetColumns = baseTable?.columns ?? [];
  const [targetColumn, setTargetColumn] = useState<string>(String(planConfig.target_column ?? ""));
  // #241: the common problem shapes named straight from the selector, skipping
  // the planner conversation. "ask_planner" is the pre-existing behaviour --
  // the column above is only a hint the agent may take or leave.
  const [problemKind, setProblemKind] = useState<"ask_planner" | "predict_column" | "flag_anomalies">("ask_planner");
  // Accepting is a state change on one canvas, not a navigation, so the panel
  // follows the plan it was reviewing into the summary of what will run.
  useEffect(() => { if (accepted) setSelected("summary"); }, [accepted]);
  // "Predict a column" needs an actual column, unlike the planner hint above
  // which is free to stay empty ("let the agent decide"). Land on a sensible
  // one automatically rather than leaving Run disabled on an empty picker.
  useEffect(() => {
    if (problemKind !== "predict_column" || targetColumn) return;
    const preferred = targetColumns.find((column) => column.candidate_target) ?? targetColumns[0];
    if (preferred) setTargetColumn(preferred.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [problemKind]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const refresh = async () => {
      try {
        // The workflow only describes an accepted pipeline. Asking for it while
        // the plan is still a proposal would put an error in the panel and tell
        // the reader nothing, so the ML nodes stay pending until there is one.
        const [nextWorkflow, nextProgress] = await Promise.all([accepted ? api.workflow(runId) : Promise.resolve(null), api.runProgress(runId)]);
        if (cancelled) return;
        setWorkflow(nextWorkflow);
        setProgress(nextProgress);
        const status = String(nextProgress.status ?? "");
        if (isRunActive(status)) timer = window.setTimeout(refresh, 1500);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void refresh();
    return () => { cancelled = true; if (timer !== null) window.clearTimeout(timer); };
    // #197: also re-run when the caller's runStatus changes. Pressing Run stops
    // the button being the only thing that moved: the poll had already parked
    // itself (it only re-arms while a run is live), so without this the fresh
    // "running" never arrived and the control kept reading "staged" until a
    // reload remounted the component. #214 adds `accepted` for the same reason:
    // accepting a plan does not move the run's status, so nothing else would
    // fetch the workflow it just created.
  }, [runId, runStatus, accepted]);

  const attempts = useMemo(() => Array.isArray(progress?.attempts) ? progress.attempts as Array<Record<string, unknown>> : [], [progress]);
  const artifactIdsByStage = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const attempt of attempts) {
      const stage = String(attempt.stage_id ?? "");
      const ids = Array.isArray(attempt.artifact_ids) ? attempt.artifact_ids.map(String) : [];
      map.set(stage, [...new Set([...(map.get(stage) ?? []), ...ids])]);
    }
    return map;
  }, [attempts]);
  // #305: every stage attempt emits an agent audit and a measurement bundle, so
  // the raw artifact chips were mostly engineering records a person has to read
  // past to find their data, model, or report. Hide the diagnostic ids by
  // default; the toolbar toggle brings them back for anyone who wants them.
  const diagnosticIds = useMemo(() => new Set(progress?.diagnostic_artifact_ids ?? []), [progress]);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const diagnosticCount = useMemo(() => {
    let seen = 0;
    for (const ids of artifactIdsByStage.values()) seen += ids.filter((id) => diagnosticIds.has(id)).length;
    return seen;
  }, [artifactIdsByStage, diagnosticIds]);
  const visibleArtifactIdsByStage = useMemo(() => {
    if (showDiagnostics) return artifactIdsByStage;
    const map = new Map<string, string[]>();
    for (const [stage, ids] of artifactIdsByStage) map.set(stage, ids.filter((id) => !diagnosticIds.has(id)));
    return map;
  }, [artifactIdsByStage, diagnosticIds, showDiagnostics]);
  const nodesById = useMemo(() => new Map((workflow?.nodes ?? []).map((node) => [node.id, node])), [workflow]);
  const groups = GROUPS.map((group) => ({ ...group, nodes: group.stages.map((stage) => nodesById.get(stage)).filter(Boolean) as WorkflowNode[] }));
  // The staging half of the graph is built from the same run progress this
  // component already polls, so the two halves cannot disagree about what
  // happened upstream.
  const routing = useMemo(() => buildStagingRoutingState(profile, progress, workspace), [profile, progress, workspace]);
  const structured = (profile.source_files ?? []).filter((file) => file.route === "structured");
  const documents = (profile.source_files ?? []).filter((file) => file.route === "documents");
  const candidateTables = workspace.document_extractions?.reduce((sum, extraction) => sum + extraction.table_candidates, 0) ?? 0;
  // #197: right after Run is clicked the poll has not refetched, so `progress`
  // still holds the pre-click "staged" and used to outrank the fresh prop. Trust
  // a non-staged polled status (the freshest truth while a run is live), but
  // otherwise defer to the caller's runStatus so the control flips the instant
  // the run starts instead of only after a reload.
  const polledStatus = progress?.status ? String(progress.status) : null;
  const activeStatus = polledStatus && polledStatus !== "staged" ? polledStatus : String(runStatus ?? polledStatus ?? "staged");
  // Every run control is gated on acceptance. The staging run is already
  // "staged" while its plan is still a proposal, so without this the toolbar
  // would offer Run for a pipeline nobody had agreed to yet.
  const canStart = accepted && activeStatus === "staged";
  const active = accepted && ["running", "resuming"].includes(activeStatus);
  const currentStage = String((progress as Record<string, unknown> | null)?.current_stage ?? "");
  const failed = accepted && ["failed", "interrupted", "aborted"].includes(activeStatus);
  const complete = accepted && activeStatus === "completed";
  // #194: one status per group, computed once so the node, its incoming arrow,
  // and the "waiting to start" hint all agree about what is happening.
  const groupStatuses = groups.map((group, index) => groupStatus(group.nodes, activeStatus, index, group.stages, currentStage, active));
  // "Review plan" opens the proposal while the plan is still one, and the
  // accepted summary once it is accepted: one control across both phases.
  const planPanel = accepted ? "summary" : "proposal";
  const mlSelected = selected && ML_SELECTIONS.includes(selected) ? selected : null;
  const stagingSelected = selected && !mlSelected ? selected as Exclude<CanvasSelection, null> : null;
  const selectedGroup = groups.find((group) => group.id === mlSelected);

  async function inspectGroup(groupId: string) {
    setSelected(groupId); setDetail(null); setError(null);
    const first = groups.find((group) => group.id === groupId)?.nodes.find((node) => node.attempt_count > 0);
    if (!first) return;
    try { setDetail(await api.stage(runId, first.id)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function inspectStage(stageId: string) {
    setError(null);
    try { setDetail(await api.stage(runId, stageId)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function openArtifact(id: string) {
    setError(null);
    try { setPreview(await api.artifactPreview(id)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  }

  // The ML half of the row, hanging off the "Proposed plan" node rather than
  // off a "Data understood" tile that stood in for the graph this canvas used
  // to discard. The first arrow is a real edge out of the plan (#214).
  const mlPipeline = <>
    <Arrow active={groupStatuses[0] === "running"} complete={accepted} dimmed={!accepted} />
    {groups.map((group, index) => {
      const status = groupStatuses[index];
      const groupArtifactIds = group.stages.flatMap((stage) => visibleArtifactIdsByStage.get(stage) ?? []);
      // #194: an accepted-but-unstarted pipeline showed every group as
      // "pending" -- identical to a running pipeline's unreached stages. Mark
      // the group the run will start with as "waiting to start" so it points
      // at the Run control instead of masquerading as work in progress.
      const waiting = canStart && (currentStage ? group.stages.includes(currentStage) : index === 0);
      const activeNode = group.nodes.find((node) => isActive(node.status));
      const footer = status === "running" ? elapsedLabel(activeNode?.elapsed_seconds) ?? t("Working…") : undefined;
      return <div key={group.id} className="contents"><GuidedNode title={t(group.title)} subtitle={t(group.description)} status={status} waiting={waiting} dimmed={!accepted} footer={footer} artifactIds={groupArtifactIds} onClick={() => void inspectGroup(group.id)} onOpenArtifact={(id) => void openArtifact(id)} />{index < groups.length - 1 && <Arrow active={status === "running" || status === "retry" || groupStatuses[index + 1] === "running"} complete={isSucceeded(status)} dimmed={!accepted} />}</div>;
    })}
  </>;

  return <CanvasSurface docked={selected !== null} plannerDocked={plannerOpen} overlay={<>
    {/* #197/#248: the run control is a distinct primary button docked top-centre
        -- where the eye lands on the canvas -- carrying a stroked vector play or
        pause icon that matches the rest of the chrome and renders identically on
        every platform. One button swaps Run/Continue -> Pause (no separate ghost
        pause button, no loose lowercase status label). The secondary actions sit
        in their own quieter bar below, not as more chips in the same row.
        #214: Accept lives here too, so the primary control becomes Run in place
        instead of the whole screen becoming a different one. */}
    <div data-no-pan className="fixed top-[70px] left-1/2 z-10 flex -translate-x-1/2 flex-col items-center gap-2">
      <div className="flex items-center gap-2">
        {!accepted && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onAccept} disabled={busy}>{busy ? t("Accepting…") : t("Accept and add base pipeline")}</button>}
        {/* #241: for an ordinary problem, name it directly instead of opening
            a planner conversation and waiting for a proposal. "Ask the
            planner" keeps today's behaviour unchanged -- the target below
            stays a hint, not a confirmed choice. */}
        {canStart && !currentStage && (
          <label data-no-pan className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-[11px] font-medium text-ink-soft shadow-card backdrop-blur" title={t("State the ML problem directly, or ask the planner to propose one.")}>
            <span className="text-ink-faint">{t("Problem")}</span>
            <select value={problemKind} onChange={(event) => setProblemKind(event.target.value as typeof problemKind)} className="max-w-[160px] bg-transparent text-[11px] font-medium text-ink outline-none">
              <option value="ask_planner">{t("Ask the planner")}</option>
              {targetColumns.length > 0 && <option value="predict_column">{t("Predict a column")}</option>}
              <option value="flag_anomalies">{t("Flag unusual rows")}</option>
            </select>
          </label>
        )}
        {canStart && !currentStage && targetColumns.length > 0 && problemKind !== "flag_anomalies" && (
          <label data-no-pan className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-[11px] font-medium text-ink-soft shadow-card backdrop-blur" title={t("Choose which column the model should predict, or let problem discovery propose one.")}>
            <span className="text-ink-faint">{t("Target")}</span>
            <select value={targetColumn} onChange={(event) => setTargetColumn(event.target.value)} className="max-w-[180px] bg-transparent text-[11px] font-medium text-ink outline-none">
              {problemKind === "ask_planner" && <option value="">{t("Let the agent decide")}</option>}
              {targetColumns.map((column) => <option key={column.name} value={column.name}>{column.name}{column.candidate_target ? " ★" : ""}</option>)}
            </select>
          </label>
        )}
        {canStart && !currentStage && (
          <label className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-[11px] font-medium text-ink-soft shadow-card backdrop-blur" title={t("The agent decides each gate on its own signals unless you take that over.")}>
            <input type="checkbox" checked={approveEachStage} onChange={(event) => setApproveEachStage(event.target.checked)} className="h-3.5 w-3.5" />
            {t("Approve at every stage")}
          </label>
        )}
        {canStart && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={() => onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind)} disabled={busy || !profile.tables.length || (problemKind === "predict_column" && !targetColumn)}><Play />{busy ? t("Working…") : t(currentStage ? "Continue" : "Run")}</button>}
        {active && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onPause} disabled={busy || Boolean(progress?.pause_requested)}><Pause />{progress?.pause_requested ? t("Pause requested…") : t("Pause")}</button>}
        {failed && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onRetry} disabled={busy}>{t("Retry from Intake")}</button>}
        {accepted && !canStart && !active && !failed && !complete && <StatusBadge status={activeStatus} />}
        {/* #247: runs the current automation again, deliberately -- as a new
            execution recorded in Execution history, not as recovery from a
            failure the way Retry above is framed. */}
        {accepted && !active && <button type="button" aria-label={t("Re-run this automation")} title={t("Re-run this automation")} className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line bg-surface/95 text-ink-soft shadow-card backdrop-blur transition hover:bg-surface-sunken disabled:opacity-50" onClick={onRerun} disabled={busy}><Reload /></button>}
      </div>
      <div className="flex items-center gap-2">
        <button type="button" className="btn-primary text-xs shadow-pop" aria-expanded={plannerOpen} onClick={() => setPlannerOpen((open) => !open)}>{t("Chat with Planner")}</button>
        <div className="flex items-center gap-1 rounded-lg border border-line bg-surface/95 px-1.5 py-1 shadow-card backdrop-blur">
          <button type="button" className="btn-ghost text-xs" aria-expanded={selected === planPanel} onClick={() => setSelected((current) => (current === planPanel ? null : planPanel))}>{t("Review plan")}</button>
          {/* #305: only offered when there is something to reveal, so the normal
              run has no developer affordance cluttering its toolbar at all. */}
          {diagnosticCount > 0 && <button type="button" className="btn-ghost text-xs" aria-pressed={showDiagnostics} onClick={() => setShowDiagnostics((open) => !open)}>{showDiagnostics ? t("Hide diagnostics") : t("Show diagnostics ({count})", { count: diagnosticCount })}</button>}
          <button type="button" className="btn-ghost text-xs" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button>
        </div>
      </div>
    </div>

    {/* One panel for the whole graph. A staging node opens the staging
        inspector, an ML node opens the stage panel, and both dock into the same
        column the canvas gives up for them (#40/#48/#214). */}
    {stagingSelected && <RoutingInspector selection={stagingSelected} profile={profile} workspace={workspace} routing={routing} onClose={() => setSelected(null)} onOpenArtifact={(id) => void openArtifact(id)} onAdvanced={onAdvanced} onOpenPlanner={() => setPlannerOpen(true)} busy={busy} runId={runId} />}
    {mlSelected && <Inspector eyebrow={t(mlSelected === "summary" ? "Accepted ML plan" : "Base ML pipeline")} title={t(mlSelected === "summary" ? "What will run" : selectedGroup?.title ?? "Stage details")} onClose={() => setSelected(null)}>
      {mlSelected === "summary"
        ? <PlanSummary profile={profile} workspace={workspace} structured={structured.map((file) => file.name)} documents={documents.map((file) => file.name)} candidateTables={candidateTables} />
        : selectedGroup?.nodes.length
          ? <div className="space-y-4">{selectedGroup.nodes.map((node) => <StageRow key={node.id} node={node} artifactIds={visibleArtifactIdsByStage.get(node.id) ?? []} onInspect={() => void inspectStage(node.id)} onOpenArtifact={(id) => void openArtifact(id)} />)}{detail && <StageEvidence detail={detail} onOpenArtifact={(id) => void openArtifact(id)} />}</div>
          : <Empty title={t("Not started yet")} hint={accepted ? t(selectedGroup?.description ?? "") : t("This stage runs once the plan is accepted.")} />}
    </Inspector>}

    {/* #97: the planner is consultable while the pipeline runs and while a gate
        waits on a human, not only during staging. It keeps the staging starter
        prompts until the plan is accepted, because until then what the reader
        is deciding is the plan, and it can still revise the one on screen. */}
    {plannerOpen && <DockedPanel><PlannerPanel runId={runId} sourceId={profile.source_id} open onToggle={() => setPlannerOpen(false)} onWorkspaceUpdated={onWorkspaceUpdated} starterPrompts={accepted ? [t("What columns are in this data?"), t("Rank the best target columns and ML problems."), t("Which relationships matter for prediction?")] : [t("What are these files?"), t("Which relationships are measured?"), t("Are the PDFs contextual evidence?"), t("Stop after EDA so I can inspect it.")]} /></DockedPanel>}
    {/* Whatever failed -- a stage inspection, an artifact preview opened from a
        staging node -- says so over the canvas rather than inside whichever of
        the two panels happens to be docked. */}
    {error && <p data-no-pan className="absolute bottom-5 left-5 z-40 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
    {preview && <ArtifactDialog preview={preview} onClose={() => setPreview(null)} />}
  </>}>
    {/* Once the plan is accepted the plan node opens the summary of what will
        run, not the proposal it no longer is. */}
    <RoutingGraph routing={routing} workspace={workspace} onSelect={(selection) => setSelected(selection === "proposal" && accepted ? "summary" : selection)} proposal={routing.proposal === "failed" ? "blocked" : accepted ? "accepted" : "ready"} onOpenArtifact={(id) => void openArtifact(id)} trailing={mlPipeline} />
  </CanvasSurface>;
}

function groupStatus(nodes: WorkflowNode[], runStatus: string, groupIndex: number, stages: string[], currentStage: string, active: boolean): WorkflowNode["status"] {
  if (nodes.some((node) => node.status === "failed" || node.status === "blocked")) return nodes.find((node) => isAttention(node.status))!.status;
  if (nodes.some((node) => isActive(node.status))) return nodes.find((node) => isActive(node.status))!.status;
  if (nodes.length && nodes.every((node) => isSucceeded(node.status))) return "succeeded";
  if (["failed", "interrupted", "aborted"].includes(runStatus) && groupIndex === 0 && !nodes.length) return "failed";
  // #194: a live run whose current stage falls in this group has not reported a
  // workflow node yet, so it used to share the unreached "pending" look with
  // every later stage and the canvas read as idle. Drive it from the run's own
  // signal instead: current_stage when the runner has named one, otherwise the
  // first not-yet-started group of a running pipeline.
  if (active && (stages.includes(currentStage) || (!currentStage && groupIndex === 0))) return "running";
  return "pending";
}

function PlanSummary({ profile, workspace, structured, documents, candidateTables }: { profile: SourceProfile; workspace: StagingWorkspace; structured: string[]; documents: string[]; candidateTables: number }) {
  const plan = workspace.recommended_plan!;
  const config = plan.configuration;
  const target = String(config.target_column ?? "");
  const objective = String(config.problem_title ?? (target ? t("Model {target}", { target }) : t("The objective will be finalized during problem discovery")));
  const baseTable = String(config.base_table ?? profile.tables[0]?.name ?? "—");
  const baseGrain = Array.isArray(config.base_grain) ? config.base_grain.map(String).join(", ") : "—";
  return <div className="space-y-5">
    <section className="rounded-xl border border-ok-200 bg-ok-50/50 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ok-700">{t("ML inputs")}</p><p className="mt-2 text-sm font-semibold text-ink">{baseTable}</p><p className="mt-1 text-[11px] text-ink-mute">{t("Grain")}: {baseGrain}</p><FileRoles title={t("Enters ML now")} files={structured} tone="ok" empty={t("No trusted structured input is selected.")} /></section>
    {documents.length > 0 && <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Context only")}</p><FileRoles title={t("Document context")} files={documents} tone="neutral" empty="" /><p className="mt-3 text-[10px] leading-relaxed text-ink-mute">{candidateTables ? t("{count} extracted table candidates remain review-only until explicitly promoted.", { count: candidateTables }) : t("Documents inform understanding but do not silently become training rows.")}</p></section>}
    <section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("ML objective")}</p><p className="mt-2 text-sm font-semibold text-ink">{objective}</p>{target && <p className="mt-1 text-[11px] text-ink-mute">{t("Target")}: {target}</p>}</section>
    <section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Execution scope")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{t("Run the established base pipeline through integration, analysis, training, evaluation, and reporting.")}</p>{plan.checkpoint_stages.length > 0 ? <div className="mt-3 flex flex-wrap gap-1.5">{plan.checkpoint_stages.map((stage) => <Badge key={stage} tone="warn">{t("Review after {stage}", { stage: stage.replaceAll("_", " ") })}</Badge>)}</div> : <p className="mt-2 text-[10px] text-ink-faint">{t("No optional human checkpoints; hard safety gates still apply.")}</p>}</section>
    {plan.rationale.length > 0 && <section className="rounded-xl bg-violet-50 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Planner rationale")}</p><ul className="mt-2 space-y-2">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></section>}
  </div>;
}

function FileRoles({ title, files, tone, empty }: { title: string; files: string[]; tone: "ok" | "neutral"; empty: string }) { return <div className="mt-3"><p className="text-[10px] font-medium text-ink-mute">{title}</p>{files.length ? <div className="mt-2 flex flex-wrap gap-1.5">{files.map((file) => <Badge key={file} tone={tone} title={file} truncate>{file}</Badge>)}</div> : <p className="mt-1 text-[10px] text-warn-700">{empty}</p>}</div>; }

function GuidedNode({ title, subtitle, status, waiting = false, dimmed = false, footer, artifactIds, onClick, onOpenArtifact }: { title: string; subtitle: string; status: WorkflowNode["status"]; waiting?: boolean; dimmed?: boolean; footer?: string; artifactIds: string[]; onClick: () => void; onOpenArtifact: (id: string) => void }) {
  // #194: a group waiting to be started reads distinctly -- a dashed brand ring
  // and its own "Waiting to start" badge -- instead of the neutral "pending"
  // it shares with stages a running pipeline simply has not reached. A running
  // group shows its elapsed footer so a long stage looks long, not hung.
  // #214: `dimmed` is the pre-acceptance state. The node is on the canvas and
  // still opens its panel -- it is the pipeline that will run -- but it reads
  // as not-yet-live so the row says "this is next", not "this is happening".
  return <ResizableNode className={cx("relative shrink-0", dimmed && "opacity-60")} defaultWidth={205}><button type="button" onClick={onClick} className={cx("h-full w-full overflow-hidden rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300", dimmed && "border-dashed", isActive(status) && "border-brand-400 ring-4 ring-brand-50", isAttention(status) && "border-stop-300", waiting && "border-dashed border-brand-400 ring-2 ring-brand-100")}>{waiting ? <div className="flex items-center justify-between gap-3"><StatusMark status="pending" /><Badge tone="brand">{t("Waiting to start")}</Badge></div> : <NodeStatusHeader status={status} />}<p className="mt-3 truncate text-sm font-semibold text-ink">{title}</p><p className="mt-1 line-clamp-2 min-h-[2rem] text-[10px] leading-relaxed text-ink-mute">{subtitle}</p>{(waiting || footer) && <p className="mt-2 text-[10px] font-medium text-brand-700">{waiting ? t("Press Run above to start") : footer}</p>}</button><ArtifactNodes ids={artifactIds} onOpen={onOpenArtifact} /></ResizableNode>;
}

// The head tracks the line: once #196 made `bg-ok-300` a real class, a
// completed arrow drew an emerald line into a grey head. Each half is one stop
// deeper than its line so the point stays legible against it.
function Arrow({ active, complete, dimmed = false }: { active: boolean; complete: boolean; dimmed?: boolean }) { return <div className={cx("relative h-px w-8 shrink-0", complete ? "bg-ok-300" : "bg-slate-300", dimmed && "opacity-60")}><span className={cx("absolute -right-1 -top-[3px] h-2 w-2 rotate-45 border-r border-t", complete ? "border-ok-400" : "border-slate-400")} />{active && <span className="absolute inset-y-[-1px] left-0 w-5 animate-pulse rounded-full bg-brand-400" />}</div>; }

function StageRow({ node, artifactIds, onInspect, onOpenArtifact }: { node: WorkflowNode; artifactIds: string[]; onInspect: () => void; onOpenArtifact: (id: string) => void }) { return <section className="rounded-xl border border-line p-3"><button type="button" className="flex w-full items-start gap-3 text-left" onClick={onInspect}><StatusMark status={node.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-ink">{stageName(node.id)}</span><span className="mt-1 block text-[10px] text-ink-mute">{statusLabel(node.status)}{elapsedLabel(node.elapsed_seconds) ? ` · ${elapsedLabel(node.elapsed_seconds)}` : ""}</span></span></button>{artifactIds.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">{artifactIds.map((id, index) => <button type="button" key={id} onClick={() => onOpenArtifact(id)} className="rounded-md bg-brand-50 px-2 py-1 text-[9px] font-semibold text-brand-700">▣ {t("Artifact {number}", { number: index + 1 })}</button>)}</div>}</section>; }

function StageEvidence({ detail, onOpenArtifact }: { detail: StageDetail; onOpenArtifact: (id: string) => void }) { const outputs = detail.outputs ?? []; const panels = (detail.panels ?? []) as AnalysisPanel[]; return <section className="rounded-xl bg-surface-sunken p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Inspection")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{detail.stage.description}</p>{/* #304: the measured analysis charts -- distributions, missingness, a correlation heatmap, target relationships -- shown here in the guided stage inspector, not only on the retired workflows screen. */}{panels.length > 0 && <div className="mt-3"><AnalysisStrip panels={panels} /></div>}{outputs.length ? <div className="mt-3 space-y-2">{outputs.map((output) => <button type="button" key={output.artifact_id} onClick={() => onOpenArtifact(output.artifact_id)} className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-left text-[10px] font-semibold text-brand-700">{output.name || output.type} · {t("Open artifact")}</button>)}</div> : <p className="mt-3 text-[10px] text-ink-faint">{t("No artifacts produced yet.")}</p>}</section>; }

