import { useEffect, useMemo, useState } from "react";
import {
  api,
  type ArtifactPreview,
  type ProfiledColumn,
  type PromotedDocumentTable,
  type RunProgressSnapshot,
  type SourceProfile,
  type StageDetail,
  type StagingWorkspace,
  type Workflow,
  type WorkflowNode,
} from "../lib/api";
import { diagnosticIdsOf, setShowDiagnostics, useShowDiagnostics, withoutDiagnostics } from "../lib/diagnostics";
import { activeLanguage, t } from "../lib/i18n";
import { elapsedLabel, isActive, isAttention, isSucceeded, statusLabel, isRunActive } from "../lib/status";
import { Badge, Empty, Pause, Play, cx } from "./ui";
import { ArtifactNodes } from "./ArtifactNodes";
import { uniqueIds } from "./artifactReveal";
import { DocumentTableReview } from "./DocumentTableReview";
import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip";
import { GROUPS, ML_SELECTIONS } from "./mlPipelineGroups";
import { NodeStatusHeader, StatusBadge, StatusMark } from "./NodeStatus";
import { activeArrows } from "./pipelineArrows";
import { SensitivityOverride } from "./SensitivityOverride";
import { stageName } from "./PipelineRail";
import { checkText, runErrorText, stageFailure, type StageFailure } from "./stageFailure";
import { ResizableNode } from "./ResizableNode";
import { buildStagingRoutingState } from "./stagingRoutingState";
import {
  ArtifactDialog,
  CanvasSurface,
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
  // #447: the checkpoints were never an argument here, so the set the canvas
  // showed and the set the run used were only ever the same by coincidence --
  // and the person had no way to change either. Passed explicitly now, so
  // pressing Run sends what the canvas says will happen.
  onRun: (
    runMode: "fully_auto" | "manual",
    targetColumn: string | null,
    problemKind: "predict_column" | "flag_anomalies" | null,
    checkpointStages: string[],
  ) => void;
  onPause: () => void;
  onRetry: () => void;
  // #378: the Planner opener and its panel live on the page now, so the canvas
  // only needs to be able to *ask* for the Planner -- a staging node's
  // "Ask the Planner to reconsider" still has to work from in here.
  onOpenPlanner: () => void;
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
export function GuidedPipeline({ runId, profile, workspace, accepted, runStatus, busy, onAccept, onWorkspaceUpdated, onRun, onPause, onRetry, onOpenPlanner, onAdvanced }: GuidedPipelineProps) {
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
  const [error, setError] = useState<string | null>(null);
  // #244/#198: the guided flow had no way to choose the ML target, and the run
  // passed none at all -- the free-text gate box was never read on approve. The
  // picker is a dropdown of the base table's profiled columns, defaulting to
  // whatever target the plan already carries, shown before the run starts. Its
  // value is passed to the run as guidance for problem discovery.
  const planConfig = (workspace.recommended_plan?.configuration ?? {}) as Record<string, unknown>;
  const baseTableName = String(planConfig.base_table ?? profile.tables[0]?.name ?? "");
  const baseTable = profile.tables.find((table) => table.name === baseTableName) ?? profile.tables[0];
  // #448: this was `baseTable?.columns ?? []`, so on a multi-table source the
  // fact table's measure was unreachable -- on MovieLens the agent could
  // propose predicting `rating` and the planner could be asked for it in chat,
  // and the one control named "Target" could not offer it, because `ratings` is
  // not the base table. Everything the source profiled is offered instead,
  // grouped by the table it came from so a bare column name is not the only
  // thing distinguishing `movies.title` from `tags.tag`.
  //
  // Base table first, and a name already claimed is not offered twice: the
  // value sent is the bare column name, which is what the integrated ABT will
  // call it, and the base table's column is the one a join resolves that name
  // to. A column that the accepted joins do not actually bring into the ABT is
  // refused with measured blocking reasons rather than silently mis-aimed
  // (#427), which is a far better answer than not being able to ask.
  const targetGroups = useMemo(() => {
    const ordered = [
      ...(baseTable ? [baseTable] : []),
      ...profile.tables.filter((table) => table.name !== baseTable?.name),
    ];
    const claimed = new Set<string>();
    const groups: Array<{ table: string; columns: ProfiledColumn[] }> = [];
    for (const table of ordered) {
      const columns = (table.columns ?? []).filter((column) => !claimed.has(column.name));
      for (const column of columns) claimed.add(column.name);
      if (columns.length) groups.push({ table: table.name, columns });
    }
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.tables, baseTable?.name]);
  const targetColumns = useMemo(() => targetGroups.flatMap((group) => group.columns), [targetGroups]);
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
  const diagnosticIds = useMemo(() => diagnosticIdsOf(progress), [progress]);
  // #424: the preference is the view's, not this component's. It was local
  // state here, so the six ML cards below were the only artifact list in the
  // app that could see it -- including the stage inspector one click away,
  // which listed the diagnostics this canvas had just filtered out of the very
  // node that opened it. `ArtifactNodes` and the inspector read the shared
  // store now, so the toggle reaches every surface that draws artifacts.
  const showDiagnostics = useShowDiagnostics();
  // The whole run's diagnostics, not the ML stages' share of them. The count
  // used to be taken over ML stage attempts alone while the staging half of
  // this same canvas drew diagnostics of its own, so the number on the control
  // described neither what was hidden nor what pressing it would reveal.
  const diagnosticCount = diagnosticIds.size;
  const nodesById = useMemo(() => new Map((workflow?.nodes ?? []).map((node) => [node.id, node])), [workflow]);
  const groups = GROUPS.map((group) => ({ ...group, nodes: group.stages.map((stage) => nodesById.get(stage)).filter(Boolean) as WorkflowNode[] }));
  // #447: the plan's checkpoints are the starting point, not the last word. A
  // person could see "Review after eda" in the plan summary and had no control
  // anywhere to add one, remove one, or find out which stage it applied to.
  // `null` means "whatever the plan says", so a planner turn that changes the
  // recommendation is still followed until someone decides otherwise.
  const planCheckpoints = workspace.recommended_plan?.checkpoint_stages ?? [];
  const [chosenCheckpoints, setChosenCheckpoints] = useState<string[] | null>(null);
  const checkpointSet = useMemo(
    () => new Set(chosenCheckpoints ?? planCheckpoints),
    [chosenCheckpoints, planCheckpoints.join("|")],
  );
  function toggleCheckpoint(stage: string) {
    setChosenCheckpoints(() => {
      const next = new Set(checkpointSet);
      if (next.has(stage)) next.delete(stage);
      else next.add(stage);
      return [...next].sort();
    });
  }
  // The staging half of the graph is built from the same run progress this
  // component already polls, so the two halves cannot disagree about what
  // happened upstream.
  const routing = useMemo(() => buildStagingRoutingState(profile, progress, workspace), [profile, progress, workspace]);
  const structured = (profile.source_files ?? []).filter((file) => file.route === "structured");
  const documents = (profile.source_files ?? []).filter((file) => file.route === "documents");
  // #361: both of these used to describe the state before a promotion and stay
  // that way. `structured` reads `profile`, a prop the parent never re-fetches
  // -- and could not have helped anyway, since the profile is a walk of the
  // uploaded files and a promoted table is not a file. The workspace now
  // records what was promoted, and the workspace *is* re-read on promotion, so
  // both the ML inputs list and the remaining-candidate count follow.
  const promotedTables = workspace.promoted_document_tables ?? [];
  const hasTrainableTables = profile.tables.length > 0 || promotedTables.length > 0;
  const extractedCandidates = workspace.document_extractions?.reduce((sum, extraction) => sum + extraction.table_candidates, 0) ?? 0;
  const candidateTables = Math.max(0, extractedCandidates - promotedTables.length);
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
  const active = accepted && isRunActive(activeStatus);
  const currentStage = String((progress as Record<string, unknown> | null)?.current_stage ?? "");
  const failed = accepted && ["failed", "interrupted", "aborted"].includes(activeStatus);
  const complete = accepted && activeStatus === "completed";
  // #194: one status per group, computed once so the node, its incoming arrow,
  // and the "waiting to start" hint all agree about what is happening.
  const groupStatuses = groups.map((group, index) => groupStatus(group.nodes, activeStatus, index, group.stages, currentStage, active));
  // "Review plan" opens the proposal while the plan is still one, and the
  // accepted summary once it is accepted: one control across both phases.
  const planPanel = accepted ? "summary" : "proposal";
  const sensitivitySelected = selected === "sensitivity";
  const mlSelected = selected && ML_SELECTIONS.includes(selected) ? selected : null;
  const stagingSelected = selected && !mlSelected && !sensitivitySelected ? selected as Exclude<CanvasSelection, null> : null;
  const selectedGroup = groups.find((group) => group.id === mlSelected);
  const personalColumns = profile.tables.reduce(
    (count, table) => count + table.columns.filter((column) => column.sensitivity === "pii").length,
    0,
  );
  // #295: a failed group used to render its own description as the hint of an
  // empty state -- prose about what the stage does, in a panel opened to find
  // out why it did not. The evidence exists; it was two clicks away inside the
  // per-stage attempt history.
  const selectedGroupStatus = selectedGroup ? groupStatuses[groups.indexOf(selectedGroup)] : null;
  const selectedFailure = isAttention(selectedGroupStatus)
    ? stageFailure(detail, runErrorText(progress?.error))
    : null;

  async function inspectGroup(groupId: string) {
    setSelected(groupId); setDetail(null); setError(null);
    const nodes = groups.find((group) => group.id === groupId)?.nodes.filter((node) => node.attempt_count > 0) ?? [];
    // #295: the failed node, not simply the first one. A group's earlier stages
    // succeed and its last one fails, so loading the first attempted stage
    // fetched a clean history and the panel had no failure to report.
    const first = nodes.find((node) => isAttention(node.status)) ?? nodes[0];
    if (!first) return;
    try { setDetail(await api.stage(runId, first.id)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
  }

  // #428: when problem discovery fails there is exactly one thing to do about
  // it -- "Retry from Intake" -- which starts a whole new run from the
  // beginning with no target guidance, so it fails the same way. Nothing about
  // intake, schema discovery or integration was wrong; the framing was. This
  // re-enters the graph at problem discovery with the person's column pinned,
  // keeping every artifact the run already produced.
  const [pinning, setPinning] = useState(false);
  async function pinProblem(kind: "predict_column" | "flag_anomalies", column: string, taskType: string) {
    setPinning(true); setError(null);
    try {
      await api.pinProblemFraming(runId, { kind, target_column: kind === "predict_column" ? column : null, task_type: taskType || null });
      setDetail(null);
      setProgress(await api.runProgress(runId));
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setPinning(false); }
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
  // #313: an arrow pulses for the group it points *into*, and nothing else.
  // The inter-group arrows used to light for the group behind them as well, so
  // a running group animated both its incoming and its outgoing edge.
  const arrowActive = activeArrows(groupStatuses);
  const mlPipeline = <>
    <Arrow active={arrowActive[0]} complete={accepted} dimmed={!accepted} />
    {groups.map((group, index) => {
      const status = groupStatuses[index];
      // Every id the group produced, diagnostics included. `ArtifactNodes` is
      // given the diagnostic set and does the hiding, the marking and the
      // #408 self-opening in one place, for this card and for every other
      // artifact list on the canvas alike (#424).
      // #446: `artifactIdsByStage` dedupes within one stage and this flattens
      // across the stages of a group, so the guard stopped one level short. A
      // stage that produced the same content as its neighbour listed the id
      // twice, and the second copy could never be revealed.
      const groupArtifactIds = uniqueIds(group.stages.flatMap((stage) => artifactIdsByStage.get(stage) ?? []));
      // #194: an accepted-but-unstarted pipeline showed every group as
      // "pending" -- identical to a running pipeline's unreached stages. Mark
      // the group the run will start with as "waiting to start" so it points
      // at the Run control instead of masquerading as work in progress.
      const waiting = canStart && (currentStage ? group.stages.includes(currentStage) : index === 0);
      const activeNode = group.nodes.find((node) => isActive(node.status));
      const checkpointStages = group.stages.filter((stage) => checkpointSet.has(stage));
      const footer = status === "running" ? elapsedLabel(activeNode?.elapsed_seconds) ?? t("Working…") : undefined;
      // A group folds several stages into one card, so the note comes from
      // whichever member stage composed one. Only the external feature search
      // does today, and it is the one result a person needs on the card itself:
      // "the service was unreachable" is otherwise invisible until they open
      // the panel.
      const note = group.nodes.find((node) => node.note)?.note ?? undefined;
      return <div key={group.id} className="contents"><GuidedNode title={t(group.title)} subtitle={t(group.description)} status={status} waiting={waiting} dimmed={!accepted} checkpointStages={checkpointStages} footer={footer} note={note} artifactIds={groupArtifactIds} activeArtifactId={preview?.artifact_id ?? null} diagnosticIds={diagnosticIds} onClick={() => void inspectGroup(group.id)} onOpenArtifact={(id) => void openArtifact(id)} />{index < groups.length - 1 && <Arrow active={arrowActive[index + 1]} complete={isSucceeded(status)} dimmed={!accepted} />}</div>;
    })}
  </>;

  return <CanvasSurface docked={selected !== null} overlay={<>
    {/* #197/#248: the run control is a distinct primary button docked top-centre
        -- where the eye lands on the canvas -- carrying a stroked vector play or
        pause icon that matches the rest of the chrome and renders identically on
        every platform. One button swaps Run/Continue -> Pause (no separate ghost
        pause button, no loose lowercase status label). The secondary actions sit
        in their own quieter bar below, not as more chips in the same row.
        #214: Accept lives here too, so the primary control becomes Run in place
        instead of the whole screen becoming a different one. */}
    <div data-no-pan className="fixed top-[4.375rem] left-1/2 z-10 flex -translate-x-1/2 flex-col items-center gap-2">
      <div className="flex items-center gap-2">
        {!accepted && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onAccept} disabled={busy}>{busy ? t("Accepting…") : t("Accept and add base pipeline")}</button>}
        {/* #241: for an ordinary problem, name it directly instead of opening
            a planner conversation and waiting for a proposal. "Ask the
            planner" keeps today's behaviour unchanged -- the target below
            stays a hint, not a confirmed choice. */}
        {canStart && !currentStage && (
          <label data-no-pan className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-2xs font-medium text-ink-soft shadow-card backdrop-blur" title={t("State the ML problem directly, or ask the planner to propose one.")}>
            <span className="text-ink-faint">{t("Problem")}</span>
            <select value={problemKind} onChange={(event) => setProblemKind(event.target.value as typeof problemKind)} className="max-w-[10rem] bg-transparent text-2xs font-medium text-ink outline-none">
              <option value="ask_planner">{t("Ask the planner")}</option>
              {targetColumns.length > 0 && <option value="predict_column">{t("Predict a column")}</option>}
              <option value="flag_anomalies">{t("Flag unusual rows")}</option>
            </select>
          </label>
        )}
        {canStart && !currentStage && targetColumns.length > 0 && problemKind !== "flag_anomalies" && (
          // #428: the two modes treat this picker differently and the control
          // used to say so nowhere. Under "Predict a column" the choice is
          // built and measured directly; under "Ask the planner" it reaches the
          // agent as prose to rank first, which the agent may still not
          // propose -- so a person whose run then failed on a different
          // framing had no way to tell their choice had been advisory.
          <label data-no-pan className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-2xs font-medium text-ink-soft shadow-card backdrop-blur" title={problemKind === "ask_planner" ? t("A hint for the agent to rank first, not a decision — it may still propose another framing.") : t("The model predicts this column. The choice is used as given. A column from a table other than the base needs the plan's joins to bring it in.")}>
            <span className="text-ink-faint">{t("Target")}</span>
            <select value={targetColumn} onChange={(event) => setTargetColumn(event.target.value)} className="max-w-[11.25rem] bg-transparent text-2xs font-medium text-ink outline-none">
              {problemKind === "ask_planner" && <option value="">{t("Let the agent decide")}</option>}
              {/* One group per table. A single-table source has exactly one, so
                  the picker looks as it always did there. */}
              {targetGroups.map((group) => <optgroup key={group.table} label={group.table}>{group.columns.map((column) => <option key={column.name} value={column.name}>{column.name}{column.candidate_target ? " ★" : ""}</option>)}</optgroup>)}
            </select>
          </label>
        )}
        {canStart && !currentStage && (
          <label className="flex items-center gap-1.5 rounded-lg border border-line bg-surface/95 px-2.5 py-2 text-2xs font-medium text-ink-soft shadow-card backdrop-blur" title={t("The agent decides each gate on its own signals unless you take that over.")}>
            <input type="checkbox" checked={approveEachStage} onChange={(event) => setApproveEachStage(event.target.checked)} className="h-3.5 w-3.5" />
            {t("Approve at every stage")}
          </label>
        )}
        {canStart && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={() => onRun(approveEachStage ? "manual" : "fully_auto", targetColumn || null, problemKind === "ask_planner" ? null : problemKind, [...checkpointSet])} disabled={busy || !hasTrainableTables || (problemKind === "predict_column" && !targetColumn)}><Play />{busy ? t("Working…") : t(currentStage ? "Continue" : "Run")}</button>}
        {active && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onPause} disabled={busy || Boolean(progress?.pause_requested)}><Pause />{progress?.pause_requested ? t("Pause requested…") : t("Pause")}</button>}
        {failed && <button type="button" className="btn-primary inline-flex items-center gap-2 shadow-pop" onClick={onRetry} disabled={busy}>{t("Retry from Intake")}</button>}
        {accepted && !canStart && !active && !failed && !complete && <StatusBadge status={activeStatus} />}
      </div>
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 rounded-lg border border-line bg-surface/95 px-1.5 py-1 shadow-card backdrop-blur">
          {/* #378: the re-run button used to sit here. It is an automation-level
              action -- a new execution in Execution history -- not a fact about
              the graph currently drawn, and gating it on `accepted && !active`
              made it absent for the whole first half of an automation's life
              and again whenever a run was in flight. It now lives beside the
              automation's name, visible always and disabled when it cannot
              run. */}
          <button type="button" className="btn-ghost text-xs" aria-expanded={selected === planPanel} onClick={() => setSelected((current) => (current === planPanel ? null : planPanel))}>{t("Review plan")}</button>
          {!accepted && profile.tables.length > 0 && <button type="button" className="btn-ghost inline-flex items-center gap-1.5 text-xs" aria-expanded={sensitivitySelected} onClick={() => setSelected((current) => (current === "sensitivity" ? null : "sensitivity"))}>{t("Review personal data")}{personalColumns > 0 && <Badge tone="warn">{personalColumns}</Badge>}</button>}
          {/* #305: only offered when there is something to reveal, so the normal
              run has no developer affordance cluttering its toolbar at all. */}
          {/* #423: this used to be a ghost button whose label swapped between
              "show" and "hide", so the control read as ambiguous -- the text
              could be the current state or the action -- it resized the
              toolbar under the pointer on every press, and the count vanished
              in one of the two states. It is a view preference, not an action,
              so it takes the same labelled-checkbox shape as "Approve at every
              stage": a fixed label carrying the count, with the state in the
              switch rather than in the wording. */}
          {diagnosticCount > 0 && <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-surface px-3.5 py-2 text-xs font-medium text-ink-soft transition-colors hover:bg-surface-sunken" title={t("Engineering records — agent audits and measurement bundles — kept out of the run view by default.")}>
            <input type="checkbox" checked={showDiagnostics} onChange={(event) => setShowDiagnostics(event.target.checked)} className="h-3.5 w-3.5" />
            {t("Diagnostics ({count})", { count: diagnosticCount })}
          </label>}
          <button type="button" className="btn-ghost text-xs" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button>
        </div>
      </div>
    </div>

    {/* #378: the Planner opener used to be here, which meant it existed only
        once this canvas was on screen -- so during input selection, source
        routing and understanding there was no way to open the Planner at all,
        which is exactly when a person has questions about their files. It is
        rendered by AutomationWorkspace now, in one place for every lifecycle
        state, and #284's reasoning about `fixed` holds there too since it is no
        longer inside a transformed ancestor. */}

    {/* One panel for the whole graph. A staging node opens the staging
        inspector, an ML node opens the stage panel, and both dock into the same
        column the canvas gives up for them (#40/#48/#214). */}
    {stagingSelected && <RoutingInspector selection={stagingSelected} profile={profile} workspace={workspace} routing={routing} onClose={() => setSelected(null)} onOpenArtifact={(id) => void openArtifact(id)} onAdvanced={onAdvanced} onOpenPlanner={onOpenPlanner} busy={busy} runId={runId} onWorkspaceUpdated={onWorkspaceUpdated} />}
    {sensitivitySelected && <Inspector eyebrow={t("Staging")} title={t("Personal data")} onClose={() => setSelected(null)}>
      <SensitivityOverride runId={runId} tables={profile.tables} />
    </Inspector>}
    {mlSelected && <Inspector eyebrow={t(mlSelected === "summary" ? "Accepted ML plan" : "Base ML pipeline")} title={t(mlSelected === "summary" ? "What will run" : selectedGroup?.title ?? "Stage details")} onClose={() => setSelected(null)}>
      {mlSelected === "summary"
        ? <PlanSummary runId={runId} profile={profile} workspace={workspace} structured={structured.map((file) => file.name)} documents={documents.map((file) => file.name)} promoted={promotedTables} candidateTables={candidateTables} onWorkspaceUpdated={onWorkspaceUpdated} />
        : <div className="space-y-4">
            {/* #295: the diagnosis leads. Below it the stage rows still hold
                the full attempt history for anyone who wants it. */}
            {selectedFailure && <FailureNotice failure={selectedFailure} stageId={detail?.stage.id ?? selectedGroup?.nodes.find((node) => isAttention(node.status))?.id ?? null} columns={targetColumns} busy={busy || pinning} onPin={pinProblem} />}
            {selectedGroup?.nodes.length
              ? <>{selectedGroup.nodes.map((node) => <StageRow key={node.id} node={node} artifactIds={artifactIdsByStage.get(node.id) ?? []} diagnosticIds={diagnosticIds} checkpoint={checkpointSet.has(node.id)} canSetCheckpoint={canStart} onToggleCheckpoint={() => toggleCheckpoint(node.id)} onInspect={() => void inspectStage(node.id)} onOpenArtifact={(id) => void openArtifact(id)} />)}{detail && <StageEvidence detail={detail} onOpenArtifact={(id) => void openArtifact(id)} />}</>
              : selectedFailure
                ? null
                : isAttention(selectedGroupStatus)
                  // Failed, and the run recorded nothing about it. Saying so is
                  // still better than describing what the stage would have done.
                  ? <Empty title={t("This stage failed")} hint={t("No error was recorded for it.")} />
                  : <>
                      <Empty title={t("Not started yet")} hint={accepted ? t(selectedGroup?.description ?? "") : t("This stage runs once the plan is accepted.")} />
                      {/* #447: "Not started yet" was the whole panel, so the one
                          moment when setting a review checkpoint is still useful
                          -- before the stage runs -- was the moment the panel
                          offered nothing. The stage list comes from the group,
                          not from the run, so it exists before any node does. */}
                      {canStart && selectedGroup && <div className="space-y-2">{selectedGroup.stages.map((stage) => <CheckpointToggle key={stage} stage={stage} checked={checkpointSet.has(stage)} onToggle={() => toggleCheckpoint(stage)} />)}</div>}
                    </>}
          </div>}
    </Inspector>}

    {/* Whatever failed -- a stage inspection, an artifact preview opened from a
        staging node -- says so over the canvas rather than inside whichever of
        the two panels happens to be docked. */}
    {error && <p data-no-pan className="absolute bottom-5 left-5 z-40 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
    {preview && <ArtifactDialog preview={preview} onClose={() => setPreview(null)} />}
  </>}>
    {/* Once the plan is accepted the plan node opens the summary of what will
        run, not the proposal it no longer is. */}
    <RoutingGraph routing={routing} workspace={workspace} onSelect={(selection) => setSelected(selection === "proposal" && accepted ? "summary" : selection)} proposal={routing.proposal === "failed" ? "blocked" : accepted ? "accepted" : "ready"} onOpenArtifact={(id) => void openArtifact(id)} activeArtifactId={preview?.artifact_id ?? null} diagnosticIds={diagnosticIds} trailing={mlPipeline} />
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

function PlanSummary({ runId, profile, workspace, structured, documents, promoted, candidateTables, onWorkspaceUpdated }: { runId: string; profile: SourceProfile; workspace: StagingWorkspace; structured: string[]; documents: string[]; promoted: PromotedDocumentTable[]; candidateTables: number; onWorkspaceUpdated: (workspace: StagingWorkspace) => void }) {
  // #311: the note below said candidates "remain review-only until explicitly
  // promoted" and offered no way to promote them -- the only Review button was
  // on the staging document panel, a canvas away. The same dialog opens here.
  const extractionId = workspace.document_extractions?.at(-1)?.artifact_id;
  const [reviewing, setReviewing] = useState(false);
  const plan = workspace.recommended_plan!;
  const config = plan.configuration;
  const target = String(config.target_column ?? "");
  const objective = String(config.problem_title ?? (target ? t("Model {target}", { target }) : t("The objective will be finalized during problem discovery")));
  const baseTable = String(config.base_table ?? profile.tables[0]?.name ?? "—");
  const baseGrain = Array.isArray(config.base_grain) ? config.base_grain.map(String).join(", ") : "—";
  // #361 listed promoted tables here as ML inputs. #390 measured that they are
  // not: the ML run resumes at the stage after schema_discovery, so intake never
  // re-runs and no promoted TableAsset is ever loaded back -- `load_table_asset`
  // has no production caller at all. They get their own line below rather than
  // being counted among the files that really do reach the model. Named by the
  // document and page they came from, not by the candidate id, which is a
  // store key.
  const mlInputs = structured;
  const promotedNames = promoted.map((table) => (table.page_number
    ? t("{file} · page {page}", { file: table.source_file, page: table.page_number })
    : table.source_file));
  return <div className="space-y-5">
    <section className="rounded-xl border border-ok-200 bg-ok-50/50 p-4"><p className="text-3xs font-semibold uppercase tracking-wide text-ok-700">{t("ML inputs")}</p><p className="mt-2 text-sm font-semibold text-ink">{baseTable}</p><p className="mt-1 text-2xs text-ink-mute">{t("Grain")}: {baseGrain}</p><FileRoles title={t("Enters ML now")} files={mlInputs} tone="ok" empty={t("No trusted structured input is selected.")} />{promotedNames.length > 0 && <><FileRoles title={t("Promoted from documents")} files={promotedNames} tone="neutral" empty="" /><p className="mt-2 text-3xs leading-relaxed text-ink-mute">{t("Saved as reviewed tables with their provenance, and joined into the ML training data for this run.")}</p></>}</section>
    {documents.length > 0 && <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4"><p className="text-3xs font-semibold uppercase tracking-wide text-violet-700">{t("Context only")}</p><FileRoles title={t("Document context")} files={documents} tone="neutral" empty="" /><p className="mt-3 text-3xs leading-relaxed text-ink-mute">{candidateTables ? t("{count} extracted table candidates remain review-only until explicitly promoted.", { count: candidateTables }) : t("Documents inform understanding but do not silently become training rows.")}</p>{candidateTables > 0 && extractionId && <button type="button" className="btn-primary mt-3 w-full justify-center text-xs" onClick={() => setReviewing(true)}>{t("Review {count} extracted tables", { count: candidateTables })}</button>}</section>}
    {/* Promoting turns candidates into real tables, so the count above and the
        ML inputs beside it are both stale afterwards. Re-read the workspace
        rather than leaving the panel describing what was true before. #361:
        this callback was already here and already correct -- what was missing
        is that promotion left no trace on the workspace to re-read. */}
    {reviewing && extractionId && <DocumentTableReview runId={runId} extractionArtifactId={extractionId} promotedCandidateIds={promoted.map((table) => table.candidate_id)} onClose={() => setReviewing(false)} onPromoted={() => { void api.stagingWorkspace(runId).then(onWorkspaceUpdated).catch(() => undefined); }} />}
    <section><p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("ML objective")}</p><p className="mt-2 text-sm font-semibold text-ink">{objective}</p>{target && <p className="mt-1 text-2xs text-ink-mute">{t("Target")}: {target}</p>}</section>
    <section><p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("Execution scope")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{t("Run the established base pipeline through integration, analysis, training, evaluation, and reporting.")}</p>{plan.checkpoint_stages.length > 0 ? <div className="mt-3 flex flex-wrap gap-1.5">{plan.checkpoint_stages.map((stage) => <Badge key={stage} tone="warn">{t("Review after {stage}", { stage: stage.replaceAll("_", " ") })}</Badge>)}</div> : <p className="mt-2 text-3xs text-ink-faint">{t("No optional human checkpoints; hard safety gates still apply.")}</p>}</section>
    {plan.rationale.length > 0 && <section className="rounded-xl bg-violet-50 p-4"><p className="text-3xs font-semibold uppercase tracking-wide text-violet-700">{t("Planner rationale")}</p><ul className="mt-2 space-y-2">{plan.rationale.map((reason) => <li key={reason.en} className="text-2xs leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></section>}
  </div>;
}

function FileRoles({ title, files, tone, empty }: { title: string; files: string[]; tone: "ok" | "neutral"; empty: string }) { return <div className="mt-3"><p className="text-3xs font-medium text-ink-mute">{title}</p>{files.length ? <div className="mt-2 flex flex-wrap gap-1.5">{files.map((file, index) => <Badge key={`${file}-${index}`} tone={tone} title={file} truncate>{file}</Badge>)}</div> : <p className="mt-1 text-3xs text-warn-700">{empty}</p>}</div>; }

function GuidedNode({ title, subtitle, status, waiting = false, dimmed = false, checkpointStages, footer, note, artifactIds, activeArtifactId, diagnosticIds, onClick, onOpenArtifact }: { title: string; subtitle: string; status: WorkflowNode["status"]; waiting?: boolean; dimmed?: boolean; checkpointStages: string[]; footer?: string; note?: NonNullable<WorkflowNode["note"]>; artifactIds: string[]; activeArtifactId?: string | null; diagnosticIds?: ReadonlySet<string>; onClick: () => void; onOpenArtifact: (id: string) => void }) {
  // #194: a group waiting to be started reads distinctly -- a dashed brand ring
  // and its own "Waiting to start" badge -- instead of the neutral "pending"
  // it shares with stages a running pipeline simply has not reached. A running
  // group shows its elapsed footer so a long stage looks long, not hung.
  // #214: `dimmed` is the pre-acceptance state. The node is on the canvas and
  // still opens its panel -- it is the pipeline that will run -- but it reads
  // as not-yet-live so the row says "this is next", not "this is happening".
  return <ResizableNode className={cx("relative shrink-0", dimmed && "opacity-60")} defaultWidth={205}><button type="button" onClick={onClick} className={cx("h-full w-full overflow-hidden rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300", dimmed && "border-dashed", isActive(status) && "border-brand-400 ring-4 ring-brand-50", isAttention(status) && "border-stop-300", waiting && "border-dashed border-brand-400 ring-2 ring-brand-100")}>{waiting ? <div className="flex items-center justify-between gap-3"><StatusMark status="pending" /><Badge tone="brand">{t("Waiting to start")}</Badge></div> : <NodeStatusHeader status={status} />}{checkpointStages.length > 0 && <div className="mt-2"><Badge tone="warn" title={checkpointStages.map(stageName).join(", ")}>{t("Human approval")}</Badge></div>}<p className="mt-3 truncate text-sm font-semibold text-ink">{title}</p><p className="mt-1 line-clamp-2 min-h-[2rem] text-3xs leading-relaxed text-ink-mute">{subtitle}</p>{(waiting || footer) && <p className="mt-2 text-3xs font-medium text-brand-700">{waiting ? t("Press Run above to start") : footer}</p>}{note && <p className={cx("mt-2 text-3xs font-medium", note.tone === "warn" ? "text-warn-700" : "text-ink-soft")}>{note.text}</p>}</button><ArtifactNodes ids={artifactIds} activeId={activeArtifactId} diagnosticIds={diagnosticIds} onOpen={onOpenArtifact} /></ResizableNode>;
}

// The head tracks the line: once #196 made `bg-ok-300` a real class, a
// completed arrow drew an emerald line into a grey head. Each half is one stop
// deeper than its line so the point stays legible against it.
function Arrow({ active, complete, dimmed = false }: { active: boolean; complete: boolean; dimmed?: boolean }) { return <div className={cx("relative h-px w-8 shrink-0", complete ? "bg-ok-300" : "bg-slate-300", dimmed && "opacity-60")}><span className={cx("absolute -right-1 -top-[3px] h-2 w-2 rotate-45 border-r border-t", complete ? "border-ok-400" : "border-slate-400")} />{active && <span className="absolute inset-y-[-1px] left-0 w-5 animate-pulse rounded-full bg-brand-400" />}</div>; }

/** The diagnosis, at the top of a failed group's panel (#295).
 *
 * The stage's own error is prose from the run; the checks below it are the
 * mechanical criteria the attempt did not meet, which are usually the more
 * specific answer -- "the plan failed a trial execution against the real
 * tables" says what to change, where a stack trace does not.
 */
function FailureNotice({ failure, stageId, columns, busy, onPin }: { failure: StageFailure;
  /** Which stage failed, so the box can offer the correction that stage takes. */
  stageId?: string | null;
  columns?: ProfiledColumn[];
  busy?: boolean;
  onPin?: (kind: "predict_column" | "flag_anomalies", column: string, taskType: string) => void;
}) {
  return <section className="rounded-xl border border-stop-200 bg-stop-50 p-4">
    <p className="text-3xs font-semibold uppercase tracking-wide text-stop-700">{t("Why it failed")}</p>
    {failure.error && <p className="mt-2 break-words text-xs leading-relaxed text-stop-800">{failure.error}</p>}
    {/* #427: the sentence, and under it the measurements that explain it. The
        panel used to show only the first, and only as the server's English
        "Mechanical check failed: <the condition that should hold>" -- so the
        reason a framing was rejected stayed in the artifact while the reader
        was shown a tautology. `measurements` are recorded facts about this
        run, so they render as they are rather than through the catalogue. */}
    {failure.checks.length > 0 && <ul className="mt-2 space-y-1.5">{failure.checks.map((check) => <li key={check.id} className="text-2xs leading-snug text-stop-700">· {t(checkText(check.id, check.evidence))}
      {check.measurements.length > 0 && <ul className="mt-1 space-y-0.5 pl-3">{check.measurements.map((measurement) => <li key={measurement} className="break-words font-mono text-3xs leading-snug text-stop-800">{measurement}</li>)}</ul>}
    </li>)}</ul>}
    {stageId === "problem_discovery" && onPin && <ProblemReframe columns={columns ?? []} busy={busy ?? false} onPin={onPin} />}
  </section>;
}

/** Name the framing a failed problem discovery could not find on its own.
 *
 * #428: the Target picker exists on the toolbar, gated on `canStart`, which is
 * false for a failed run -- so the control was present in the state where
 * nothing had gone wrong yet and absent in the one where a person knows
 * exactly what to fix. The column is pinned as a constraint rather than passed
 * as prose the agent may rank first and then ignore: the candidate is built
 * directly and measured, so an unviable column comes back as the blocking
 * reasons for *that* column, which is an answer somebody can act on.
 */
function ProblemReframe({ columns, busy, onPin }: { columns: ProfiledColumn[]; busy: boolean; onPin: (kind: "predict_column" | "flag_anomalies", column: string, taskType: string) => void }) {
  const [kind, setKind] = useState<"predict_column" | "flag_anomalies">("predict_column");
  // Empty means "read it off the column's measured shape", which is the right
  // default. It is offered because inference cannot tell a 0/1 label from a
  // 0/1 quantity and the person looking at their own data can.
  const [taskType, setTaskType] = useState("");
  const [column, setColumn] = useState(() => (columns.find((item) => item.candidate_target) ?? columns[0])?.name ?? "");
  const ready = kind === "flag_anomalies" || Boolean(column);
  return <div className="mt-4 border-t border-stop-200 pt-3">
    <p className="text-3xs font-semibold uppercase tracking-wide text-stop-700">{t("Name the problem yourself")}</p>
    <p className="mt-1 text-3xs leading-relaxed text-stop-700">{t("This re-runs problem discovery on this run with your choice pinned. Intake, schema discovery and integration are kept.")}</p>
    <div className="mt-3 flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1 text-3xs font-medium text-stop-800">{t("Problem")}
        <select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)} className="rounded-lg border border-stop-200 bg-surface px-2 py-1.5 text-2xs font-medium text-ink outline-none">
          <option value="predict_column">{t("Predict a column")}</option>
          <option value="flag_anomalies">{t("Flag unusual rows")}</option>
        </select>
      </label>
      {kind === "predict_column" && <label className="flex flex-col gap-1 text-3xs font-medium text-stop-800">{t("Target")}
        <select value={column} onChange={(event) => setColumn(event.target.value)} className="max-w-[11.25rem] rounded-lg border border-stop-200 bg-surface px-2 py-1.5 text-2xs font-medium text-ink outline-none">
          {columns.map((item) => <option key={item.name} value={item.name}>{item.name}{item.candidate_target ? " ★" : ""}</option>)}
        </select>
      </label>}
      {kind === "predict_column" && <label className="flex flex-col gap-1 text-3xs font-medium text-stop-800">{t("Task type")}
        <select value={taskType} onChange={(event) => setTaskType(event.target.value)} className="rounded-lg border border-stop-200 bg-surface px-2 py-1.5 text-2xs font-medium text-ink outline-none">
          <option value="">{t("From the column's shape")}</option>
          <option value="regression">{t("Regression")}</option>
          <option value="binary_classification">{t("Binary classification")}</option>
          <option value="multiclass_classification">{t("Multiclass classification")}</option>
        </select>
      </label>}
      <button type="button" className="btn-primary text-xs" disabled={busy || !ready} onClick={() => onPin(kind, column, taskType)}>{busy ? t("Working…") : t("Re-run problem discovery")}</button>
    </div>
  </div>;
}

/** #424: the per-stage chips inside the docked group panel, filtered against
 *  the same preference as the canvas node above them. */
/** The per-stage review checkpoint control (#447).
 *
 * A checkpoint could be requested of the planner and read back off the plan
 * summary, and that was the whole of it: no way to add one, remove one, or see
 * which stage in a group it applied to. The group node's "Human approval" badge
 * says a group has one somewhere; this says which stage, and lets it change.
 *
 * Only before a run: mid-run the engine has already been handed its gate
 * policy, so a control that appeared to change it would be lying.
 */
function CheckpointToggle({ stage, checked, onToggle }: { stage: string; checked: boolean; onToggle: () => void }) {
  return <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-3xs text-ink-soft transition-colors hover:bg-surface-sunken" title={t("The run stops after this stage and waits for your decision.")}>
    <input type="checkbox" checked={checked} onChange={onToggle} className="h-3.5 w-3.5" />
    <span className="font-medium text-ink">{stageName(stage)}</span>
    <span className="ml-auto text-ink-faint">{checked ? t("Pauses for review") : t("Runs through")}</span>
  </label>;
}

function StageRow({ node, artifactIds, diagnosticIds, checkpoint, canSetCheckpoint, onToggleCheckpoint, onInspect, onOpenArtifact }: { node: WorkflowNode; artifactIds: string[]; diagnosticIds: ReadonlySet<string>; checkpoint: boolean; canSetCheckpoint: boolean; onToggleCheckpoint: () => void; onInspect: () => void; onOpenArtifact: (id: string) => void }) { const showDiagnostics = useShowDiagnostics(); const visible = withoutDiagnostics(artifactIds, (id) => diagnosticIds.has(id), showDiagnostics); return <section className="rounded-xl border border-line p-3"><button type="button" className="flex w-full items-start gap-3 text-left" onClick={onInspect}><StatusMark status={node.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-ink">{stageName(node.id)}</span><span className="mt-1 block text-3xs text-ink-mute">{statusLabel(node.status)}{elapsedLabel(node.elapsed_seconds) ? ` · ${elapsedLabel(node.elapsed_seconds)}` : ""}</span></span>{checkpoint && <Badge tone="warn">{t("Review")}</Badge>}</button>{canSetCheckpoint && <div className="mt-3 border-t border-line pt-3"><CheckpointToggle stage={node.id} checked={checkpoint} onToggle={onToggleCheckpoint} /></div>}{visible.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">{visible.map((id, index) => <button type="button" key={id} onClick={() => onOpenArtifact(id)} className="rounded-md bg-brand-50 px-2 py-1 text-4xs font-semibold text-brand-700">▣ {t("Artifact {number}", { number: index + 1 })}</button>)}</div>}</section>; }

/** #424: the panel that opens when you click the node whose chips were just
 *  filtered. It rendered `detail.outputs` straight from the API, so the same
 *  run showed two different artifact lists one click apart -- and the toggle
 *  looked broken from the surface most likely to be read after pressing it.
 *  Same preference, same closed set: the backend already marks each output
 *  `diagnostic`, so nothing here re-derives which kinds those are. */
function StageEvidence({ detail, onOpenArtifact }: { detail: StageDetail; onOpenArtifact: (id: string) => void }) { const showDiagnostics = useShowDiagnostics(); const outputs = withoutDiagnostics(detail.outputs ?? [], (output) => output.diagnostic === true, showDiagnostics); const panels = (detail.panels ?? []) as AnalysisPanel[]; return <section className="rounded-xl bg-surface-sunken p-4"><p className="text-3xs font-semibold uppercase tracking-wide text-ink-faint">{t("Inspection")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{detail.stage.description}</p>{/* #304: the measured analysis charts -- distributions, missingness, a correlation heatmap, target relationships -- shown here in the guided stage inspector, not only on the retired workflows screen. */}{panels.length > 0 && <div className="mt-3"><AnalysisStrip panels={panels} /></div>}{outputs.length ? <div className="mt-3 space-y-2">{outputs.map((output) => <button type="button" key={output.artifact_id} onClick={() => onOpenArtifact(output.artifact_id)} className={cx("w-full rounded-lg border bg-surface px-3 py-2 text-left text-3xs font-semibold text-brand-700", output.diagnostic ? "border-dashed border-slate-300" : "border-line")}>{output.name || output.type} · {t("Open artifact")}{output.diagnostic && <span className="ml-1 font-medium text-ink-faint">· {t("Diagnostic")}</span>}</button>)}</div> : <p className="mt-3 text-3xs text-ink-faint">{t("No artifacts produced yet.")}</p>}</section>; }

