import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  api,
  type ArtifactPreview,
  type LocalizedText,
  type RunProgressSnapshot,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { activeLanguage, localizedList, t } from "../lib/i18n";
import { isRunActive } from "../lib/status";
import { NodeStatusHeader, StatusMark } from "./NodeStatus";
import { sourceCounts, visibleWorkflowSteps } from "./automationWorkspaceState";
import {
  buildStagingRoutingState,
  fileNeedsAttention,
  pageOfFiles,
  type ProgressStatus,
  type RoutedSourceFile,
  type RoutingSubstep,
  type StagingOutcome,
  type StagingRoutingState,
} from "./stagingRoutingState";
import { Badge, Chevron, Empty, Spinner, cx } from "./ui";
import { GROUPS } from "./mlPipelineGroups";
import { CANVAS_BASE_HEIGHT, CANVAS_BASE_WIDTH, CANVAS_MAX_STEP, CANVAS_MIN_STEP, clampStep, isZoomGesture, scaledBox, zoomForStep, zoomPercent } from "./canvasZoom";
import { ArtifactNodes } from "./ArtifactNodes";
import { AnalysisStrip, type AnalysisPanel } from "./AnalysisStrip";
import { visibleFileChips } from "./branchNodeChips";
import { ArtifactMetadata, hasArtifactMetadata } from "./ArtifactMetadata";
import { artifactTitle } from "./artifactTitle";
import { artifactTypeLabel } from "./artifactTypeLabel";
import { isCanvasPanBlocked, releaseCanvasPointer } from "./canvasPan";
import { ResizableNode } from "./ResizableNode";
import { DocumentTableReview } from "./DocumentTableReview";
import { FileInsight } from "./FileInsight";

/** What the "Proposed plan" node is showing.
 *
 * `accepted` exists because the node stays on the canvas once the plan is
 * accepted (#214) -- before that the whole screen was replaced, so the node
 * never had to describe a plan it had already handed on. */
export type ProposalStatus = "pending" | "ready" | "accepted" | "blocked";

export type CanvasSelection = "source" | "discovery" | "structured" | "documents" | "synthesis" | "proposal" | null;

function local(value: LocalizedText): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

function useRunProgress(runId: string | null): RunProgressSnapshot | null {
  const [progress, setProgress] = useState<RunProgressSnapshot | null>(null);
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: number | null = null;
    const refresh = () => { void api.runProgress(runId).then((next) => {
      if (cancelled) return;
      setProgress(next);
      if (isRunActive(String(next.status ?? ""))) {
        timer = window.setTimeout(refresh, 1400);
      }
    }).catch(() => {
      if (!cancelled) timer = window.setTimeout(refresh, 1400);
    }); };
    refresh();
    return () => { cancelled = true; if (timer !== null) window.clearTimeout(timer); };
  }, [runId]);
  return progress;
}

export function SourceSummary({ profile, onStart, busy, onRemoveFile, onAddFiles }: { profile: SourceProfile; onStart: () => void; busy: boolean; onRemoveFile?: (name: string) => void; onAddFiles?: () => void }) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const counts = sourceCounts(profile);
  return (
    <CanvasSurface docked={sourceOpen} overlay={sourceOpen ? <Inspector title={t("Uploaded files")} eyebrow={t("Source")} onClose={() => setSourceOpen(false)}><SourceOverview profile={profile} onRemoveFile={onRemoveFile} onAddFiles={onAddFiles} busy={busy} /></Inspector> : undefined}>
      <div className="flex min-w-[570px] items-center gap-10 px-10 py-16">
        <PhaseNode title={t("Uploaded files")} subtitle={t("{count} files", { count: counts.files })} status="complete" onClick={() => setSourceOpen(true)} footer={t("Click to inspect files")} />
        <GraphEdge status="pending" />
        <div className="w-[270px] rounded-2xl border-2 border-dashed border-brand-300 bg-brand-50/80 p-5 text-left shadow-card">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Recommended next step")}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{t("Classify and route every file, then understand each source on the right path.")}</p>
          {/* A real button, full width of the card. It used to be bare text with
              no padding, so the hit area was the glyphs of "Run Intake" itself:
              the card looked like the control and swallowed every click that
              missed the word, so the button grows to fill the card instead.
              (#314 removed the reuse checkbox that used to sit under it, which
              was the other reason the card could not become the button.) */}
          <button type="button" onClick={onStart} disabled={busy} className="btn-primary mt-3 w-full justify-center text-sm">{busy ? t("Starting…") : t("Run Intake")}</button>
        </div>
      </div>
    </CanvasSurface>
  );
}

// #362: `onRetry` went with the yellow banner -- it was that banner's only
// caller, and a run stopped at a gate is answered on the ApprovalCard (which
// offers "Stop the run" among its options) rather than restarted from here.
export function UnderstandingProgress({ profile, runId, workspace }: { profile: SourceProfile; runId: string | null; workspace?: StagingWorkspace | null }) {
  const progress = useRunProgress(runId);
  const routing = useMemo(() => buildStagingRoutingState(profile, progress, workspace ?? null), [profile, progress, workspace]);
  const [selection, setSelection] = useState<CanvasSelection>(null);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  return (
    <CanvasSurface docked={selection !== null} overlay={<>
      {routing.error && <div role="alert" className="fixed left-1/2 top-[72px] z-20 w-[min(680px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-stop-300 bg-stop-50 px-4 py-3 shadow-pop"><div className="flex items-start gap-3"><StatusMark status="failed" /><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-stop-700">{t("Staging stopped")}</p><p className="mt-1 break-words text-[11px] leading-relaxed text-stop-700">{routing.error}</p></div><button type="button" className="shrink-0 text-[10px] font-semibold text-stop-700 hover:underline" onClick={() => setSelection(routing.documents.some((step) => step.status === "failed" && step.id !== "explain") ? "documents" : "synthesis")}>{t("Inspect failure")}</button></div></div>}
      {/* #362: a yellow "Understanding needs review" banner used to sit here,
          built from the same `human_prompt.question` + `context_summary` the
          red ApprovalCard above the run already renders in full -- with the
          stage name, the reason list, and the three action buttons. A person
          read the same explanation twice and then had to work out which of the
          two boxes could actually answer it, since only one of them could. The
          card is the one that can, so the banner is gone. */}
      {!routing.error && routing.outcome && <OutcomeNotice outcome={routing.outcome} files={routing.files.map((file) => file.name)} onInspect={() => setSelection("synthesis")} />}
      {selection && <RoutingInspector selection={selection} profile={profile} workspace={workspace ?? null} routing={routing} onClose={() => setSelection(null)} onOpenArtifact={(id) => { void api.artifactPreview(id).then(setPreview); }} runId={runId} />}
      {preview && <ArtifactDialog preview={preview} onClose={() => setPreview(null)} />}
    </>}>
      <RoutingGraph routing={routing} workspace={workspace ?? null} onSelect={setSelection} proposal={routing.outcome || routing.proposal === "failed" ? "blocked" : "pending"} onOpenArtifact={(id) => { void api.artifactPreview(id).then(setPreview); }} activeArtifactId={preview?.artifact_id ?? null} />
    </CanvasSurface>
  );
}

/**
 * Understanding finished and there is no plan to accept -- say so (#365).
 *
 * This is the whole reported bug: a file was accepted, nothing went red, and
 * the flow never reached the suggested-plan node. The proposal node has only
 * "pending" and "blocked", and every way of ending without a proposal rendered
 * as pending, so a run that had already stopped was indistinguishable from one
 * still working. People waited, reloaded, and started again.
 *
 * The two decision cases are not faults and are not dressed as ones: the
 * planner is asked to weigh `defer_pipeline` and `no_pipeline`, and it records
 * its reason in `decision_summary` -- which nothing rendered until now. The
 * third case is the one we cannot explain from here, so it says only what is
 * true: understanding finished, no plan came out of it, and these are the files
 * it was working on.
 */
function OutcomeNotice({ outcome, files, onInspect }: { outcome: StagingOutcome; files: string[]; onInspect: () => void }) {
  const declined = outcome.kind === "declined";
  const unexplained = outcome.kind === "no_plan";
  const tone = unexplained
    ? { border: "border-stop-300", bg: "bg-stop-50", head: "text-stop-700", body: "text-stop-700" }
    : declined
      ? { border: "border-line", bg: "bg-surface", head: "text-ink", body: "text-ink-mute" }
      : { border: "border-warn-300", bg: "bg-warn-50", head: "text-warn-800", body: "text-warn-700" };
  const title = unexplained
    ? t("Understanding finished without a plan")
    : declined
      ? t("No ML pipeline is proposed for this source")
      : t("A review is needed before a plan can be proposed");
  return (
    <div role="alert" className={cx("fixed left-1/2 top-[72px] z-20 w-[min(720px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border px-4 py-3 shadow-pop", tone.border, tone.bg)}>
      <div className="flex items-start gap-3">
        <StatusMark status={unexplained ? "failed" : "blocked"} />
        <div className="min-w-0 flex-1">
          <p className={cx("text-xs font-semibold", tone.head)}>{title}</p>
          {outcome.summary && <p className={cx("mt-1 break-words text-[11px] leading-relaxed", tone.body)}>{local(outcome.summary)}</p>}
          {unexplained && !outcome.summary && (
            <p className={cx("mt-1 break-words text-[11px] leading-relaxed", tone.body)}>
              {t("The run reached the end of understanding and produced no proposal, and it recorded no reason. Nothing further will happen on its own.")}
            </p>
          )}
          {outcome.rationale.length > 0 && (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4">
              {outcome.rationale.slice(0, 4).map((reason) => (
                <li key={reason.en} className={cx("break-words text-[11px] leading-relaxed", tone.body)}>{local(reason)}</li>
              ))}
            </ul>
          )}
          {/* Naming the files is the point: the reported stall was per-file,
              and a message that does not say which file leaves the same
              guessing behind. */}
          {files.length > 0 && (
            <p className="mt-1.5 break-words text-[10px] leading-relaxed text-ink-faint">
              {t("Files understood: {files}", { files: files.slice(0, 8).join(", ") })}
              {files.length > 8 && ` +${files.length - 8}`}
            </p>
          )}
        </div>
        <button type="button" className={cx("shrink-0 text-[10px] font-semibold hover:underline", tone.head)} onClick={onInspect}>{t("Inspect understanding")}</button>
      </div>
    </div>
  );
}

/** The fixed ML pipeline, outlined while there is not yet a plan to attach.
 *
 * Staging used to end at a single "Proposed plan" node and then jump to the
 * editing canvas, which made the pipeline look like something being authored --
 * a different screen, an editor, a blank-ish canvas -- when it is fixed and
 * already known. Showing its real shape here, greyed out, says the opposite:
 * this is what will run, staging is deciding what feeds it, and nothing is
 * waiting on you to draw it.
 *
 * Once the planner has proposed something, the graph carries the real ML nodes
 * instead of this outline and accepting the plan lights them up in place
 * (#214). This is only the placeholder for the stretch before that, so it has
 * no "ready" state of its own any more.
 *
 * The shape comes from the shared GROUPS spine rather than a list of its own,
 * so the outline cannot drift from what actually executes.
 */
function ProposedPipelinePreview() {
  return (
    <div
      aria-label={t("Fixed ML pipeline")}
      aria-busy="true"
      className="flex w-[210px] shrink-0 flex-col gap-2 rounded-2xl border border-dashed border-line bg-surface/40 p-3 opacity-60"
    >
      <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
        <span className="h-2.5 w-2.5 shrink-0 animate-spin rounded-full border border-brand-400 border-t-transparent motion-reduce:animate-none" aria-hidden="true" />
        {t("Preparing the plan…")}
      </p>
      <ol className="flex flex-col gap-1">
        {GROUPS.map((group, index) => (
          <li key={group.id} className="flex items-center gap-2 rounded-lg bg-surface/70 px-2 py-1">
            <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-surface-sunken text-[8px] font-semibold tabular-nums text-ink-faint">{index + 1}</span>
            <span className="truncate text-[10px] text-ink-mute">{t(group.title)}</span>
          </li>
        ))}
      </ol>
      <p className="text-[9px] leading-relaxed text-ink-faint">
        {t("This pipeline is fixed. Staging is deciding which data feeds it.")}
      </p>
    </div>
  );
}

/** The staging half of the automation graph: files -> intake -> branches ->
 *  synthesis -> proposed plan.
 *
 * `trailing` is what the plan node leads into. Before a plan exists that is a
 * faded outline of the fixed ML pipeline; once one does, the caller passes the
 * real ML nodes, so accepting the plan lights them up in place instead of
 * swapping the canvas for a different screen (#214). Either way it is the same
 * flex row, so the edge out of "Proposed plan" is a real edge rather than a
 * boundary between two components.
 */
export function RoutingGraph({ routing, workspace, onSelect, proposal, onOpenArtifact, activeArtifactId = null, trailing }: { routing: StagingRoutingState; workspace: StagingWorkspace | null; onSelect: (selection: CanvasSelection) => void; proposal: ProposalStatus; onOpenArtifact: (id: string) => void; activeArtifactId?: string | null; trailing?: React.ReactNode }) {
  const branches = [
    routing.structured.length ? { id: "structured" as const, title: t("Structured data"), files: routing.files.filter((file) => file.route === "structured"), steps: routing.structured } : null,
    routing.documents.length ? { id: "documents" as const, title: t("Documents"), files: routing.files.filter((file) => file.route === "documents"), steps: routing.documents } : null,
    routing.files.some((file) => file.route === "unsupported") ? { id: "discovery" as const, title: t("Needs review"), files: routing.files.filter((file) => file.route === "unsupported"), steps: [{ id: "explain", label: "Explain why", status: "complete" as const }] } : null,
  ].filter(Boolean) as Array<{ id: "structured" | "documents" | "discovery"; title: string; files: RoutedSourceFile[]; steps: RoutingSubstep[] }>;
  const branchStatus = branches.some((branch) => branch.steps.some((step) => step.status === "failed")) ? "failed"
    : branches.some((branch) => branch.steps.some((step) => step.status === "running")) ? "running"
      : branches.every((branch) => branch.steps.every((step) => step.status === "complete")) ? "complete" : "pending";
  // Ids rather than counts. A count could only ever render a badge; the badge
  // promised artifacts existed and led to a panel that could not open them
  // (#45). Carrying the ids means the node itself is the way in.
  const outputIds = (componentIds: string[]) => (workspace?.component_outputs ?? []).filter((output) => componentIds.includes(output.component_id)).flatMap((output) => output.artifact_ids);
  const measuredIds = [...(workspace?.intake_artifact_ids ?? []), ...(workspace?.schema_artifact_ids ?? [])];
  const documentIds = (workspace?.document_extractions ?? []).map((item) => item.artifact_id).filter((id): id is string => Boolean(id));
  const reportIds = outputIds(["structured-brief", "document-brief", "understanding-synthesis"]);
  return (
    <div className="flex min-w-[1430px] items-center justify-center gap-5 px-6 py-10">
      <PhaseNode title={t("Uploaded files")} subtitle={t("{count} files", { count: routing.files.length })} status="complete" onClick={() => onSelect("source")} footer={t("Inputs")} compact />
      <GraphEdge status="complete" />
      <PhaseNode title={t("Intake")} subtitle={t("Discover, classify, and route")} status={routing.discovery} onClick={() => onSelect("discovery")} artifactIds={measuredIds} activeArtifactId={activeArtifactId} onOpenArtifact={onOpenArtifact} compact />
      <ForkConnector branches={branches.length} status={branchStatus} />
      <div className="flex flex-col gap-4">
        {branches.map((branch) => <BranchNode key={branch.id} title={branch.title} files={branch.files} steps={branch.steps} artifactIds={branch.id === "structured" ? [...measuredIds, ...outputIds(["structured-brief"])] : branch.id === "documents" ? [...documentIds, ...outputIds(["document-brief"])] : []} activeArtifactId={activeArtifactId} onClick={() => onSelect(branch.id)} onOpenArtifact={onOpenArtifact} />)}
      </div>
      <MergeConnector branches={branches.length} status={routing.synthesis} />
      <PhaseNode title={t("Synthesize")} subtitle={t("Bring findings together")} status={routing.synthesis} onClick={() => onSelect("synthesis")} artifactIds={reportIds} activeArtifactId={activeArtifactId} onOpenArtifact={onOpenArtifact} compact />
      <GraphEdge status={proposal === "ready" || proposal === "accepted" ? "complete" : proposal === "blocked" ? "failed" : "pending"} />
      <button type="button" onClick={() => onSelect("proposal")} className={cx("w-[190px] rounded-2xl border-2 p-4 text-left shadow-card transition hover:-translate-y-0.5", proposal === "accepted" ? "border-ok-300 bg-ok-50/70 hover:border-ok-400" : proposal === "ready" ? "border-brand-300 bg-brand-50/80 hover:border-brand-500" : proposal === "blocked" ? "border-stop-300 bg-stop-50" : "border-dashed border-line bg-surface/80")}>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-600">{t(proposal === "accepted" ? "Accepted" : "Proposed")}</p>
        <p className="mt-2 text-sm font-semibold text-ink">{t("Proposed plan")}</p>
        <p className="mt-1 text-[10px] text-ink-mute">{proposal === "accepted" ? t("The pipeline below runs this plan") : proposal === "ready" ? t("Ready for review") : proposal === "blocked" ? t("Blocked — no plan was created") : t("Created after synthesis")}</p>
      </button>
      {trailing ?? (proposal !== "blocked" && <><GraphEdge status="pending" /><ProposedPipelinePreview /></>)}
    </div>
  );
}

export function BranchNode({ title, files, steps, artifactIds, activeArtifactId, onClick, onOpenArtifact }: { title: string; files: RoutedSourceFile[]; steps: RoutingSubstep[]; artifactIds: string[]; activeArtifactId?: string | null; onClick: () => void; onOpenArtifact: (id: string) => void }) {
  const status: ProgressStatus = steps.some((step) => step.status === "failed") ? "failed" : steps.some((step) => step.status === "running") ? "running" : steps.every((step) => step.status === "complete") ? "complete" : "pending";
  const secondary = steps.find((step) => step.detail)?.detail;
  return <ResizableNode className="relative" defaultWidth={290}>{(width) => { const shown = visibleFileChips(files.map((file) => file.name), width); return <><button type="button" onClick={onClick} className={cx("h-full w-full overflow-hidden rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300", status === "running" && "border-brand-400 ring-4 ring-brand-50", status === "failed" && "border-stop-300")}>
    <div className="flex items-start gap-3"><StatusMark status={status} /><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-ink">{title}</p><p className="mt-0.5 text-[10px] text-ink-mute">{t("{count} files", { count: files.length })}</p></div></div>
    <div className="mt-3 flex flex-wrap gap-1">{files.slice(0, shown).map((file) => <span key={file.name} title={file.name} className="max-w-[120px] truncate rounded-md bg-surface-sunken px-2 py-1 text-[9px] font-medium text-ink-mute">{file.name}</span>)}{files.length > shown && <span className="rounded-md bg-surface-sunken px-2 py-1 text-[9px] text-ink-faint">+{files.length - shown}</span>}</div>
    {secondary && <p className="mt-2 text-[9px] font-medium text-ink-mute">{t(secondary)}</p>}
    <ol className="mt-3 grid grid-cols-[auto_auto] justify-between gap-x-3 gap-y-1.5">{steps.map((step) => <li key={step.id} className={cx("min-w-0 text-[10px]", step.status === "running" ? "font-semibold text-brand-700" : step.status === "complete" ? "text-ok-700" : step.status === "failed" ? "text-stop-700" : "text-ink-faint")}><span className="mr-1">{step.status === "complete" ? "✓" : step.status === "running" ? "●" : step.status === "failed" ? "!" : "○"}</span>{t(step.label)}</li>)}</ol>
  </button><ArtifactNodes ids={artifactIds} activeId={activeArtifactId} onOpen={onOpenArtifact} /></>; }}</ResizableNode>;
}

export function RoutingInspector({ selection, profile, workspace, routing, onClose, onOpenArtifact, onAccept, onAdvanced, onOpenPlanner, busy = false, runId }: { selection: Exclude<CanvasSelection, null>; profile: SourceProfile; workspace: StagingWorkspace | null; routing: StagingRoutingState; onClose: () => void; onOpenArtifact: (id: string) => void; onAccept?: () => void; onAdvanced?: () => void; onOpenPlanner?: () => void; busy?: boolean; runId?: string | null }) {
  const titles: Record<Exclude<CanvasSelection, null>, string> = {
    source: t("Uploaded files"), discovery: t("Intake and source routing"), structured: t("Structured data"), documents: t("Understand documents"), synthesis: t("Cross-source synthesis"), proposal: t("Proposed plan"),
  };
  return <Inspector title={titles[selection]} eyebrow={selection === "synthesis" ? t("Evidence and interpretation") : t("Staging")} onClose={onClose}>
    {selection === "source" && <SourceOverview profile={profile} />}
    {selection === "discovery" && <RoutingDetails files={routing.files} />}
    {selection === "structured" && <StructuredDetails profile={profile} workspace={workspace} routing={routing} />}
    {selection === "documents" && <DocumentDetails workspace={workspace} routing={routing} onOpenArtifact={onOpenArtifact} runId={runId} />}
    {selection === "synthesis" && (workspace?.planner_error ? <div className="rounded-xl border border-stop-200 bg-stop-50 p-4"><p className="text-xs font-semibold text-stop-700">{t("Planner synthesis failed")}</p><p className="mt-2 break-words text-[11px] leading-relaxed text-stop-700">{workspace.planner_error}</p></div> : workspace ? <UnderstandingResults profile={profile} workspace={workspace} onOpenArtifact={onOpenArtifact} /> : <ProgressList steps={[...routing.structured, ...routing.documents]} />)}
    {/* #385: gated on the plan existing, not on the caller happening to offer
        an advanced editor. "Plan not ready yet" now means what it says --
        synthesis has not produced a recommendation -- rather than standing in
        for a decision the Planner has already made and explained. */}
    {selection === "proposal" && (workspace?.recommended_plan ? <PlanProposal profile={profile} workspace={workspace} onAccept={onAccept} onAdvanced={onAdvanced} onOpenPlanner={onOpenPlanner} busy={busy} /> : <Empty title={t("Plan not ready yet")} hint={t("The proposal appears after structured and document findings are synthesized.")} />)}
  </Inspector>;
}

/**
 * What ads.file_detection measured, in words a tester can act on.
 *
 * Written as literal t() calls rather than a lookup table so the i18n scanner
 * can see them; a label reaching t() through a variable is invisible to it and
 * ships in English (#38).
 */
function measuredFlowLabel(flow: string): string {
  if (flow === "tablo") return t("Table");
  if (flow === "belge") return t("Document");
  if (flow === "agac") return t("Structured tree");
  if (flow === "kapsayici") return t("Archive");
  if (flow === "islenemez") return t("Unreadable");
  return t("Needs a decision");
}

/**
 * The measurement beside the route, because today they can disagree.
 *
 * The route above is decided by the file's extension. File detection measures the
 * content. A PDF renamed to .csv is routed as structured and read as a table
 * while the measurement says "pdf", and until the two are wired together the
 * only place that shows up is here (#103).
 */
function MeasuredType({ file }: { file: RoutedSourceFile }) {
  if (!file.measuredFlow) return null;
  const disagrees = file.contradictsExtension === true;
  return <div className={cx("mt-2 rounded-lg px-2 py-2", disagrees ? "bg-warn-50" : "bg-surface-sunken")}>
    <div className="flex items-center gap-1.5">
      <span className={cx("text-[9px] font-semibold uppercase tracking-wide", disagrees ? "text-warn-800" : "text-ink-faint")}>{disagrees ? t("Extension and measurement disagree") : t("Measured from content")}</span>
      <span className={cx("rounded-full px-1.5 py-0.5 text-[9px] font-semibold", disagrees ? "bg-warn-100 text-warn-800" : "bg-surface text-ink-mute")}>{measuredFlowLabel(file.measuredFlow)}</span>
    </div>
    {file.measuredEvidence && <p className="mt-1 break-words text-[10px] leading-relaxed text-ink-mute">{file.measuredEvidence}</p>}
    {file.measuredDeterministic === false && file.needsDecisionBecause && <p className="mt-1 break-words text-[10px] leading-relaxed text-warn-700">{t("Needs a decision")}: {file.needsDecisionBecause}</p>}
  </div>;
}

export function RoutingDetails({ files }: { files: RoutedSourceFile[] }) {
  // #98: this list had no bound, so a sharded upload made the Yapısal veri panel
  // one unscrollable column with no way to find a file. Search + page size +
  // paging, mirroring the /datasets catalog (#72). State resets to page 1 when
  // the filter or page size changes; the helper clamps a stale page.
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const { shown, total, page: current, lastPage } = pageOfFiles(files, { search, page, pageSize });
  const pager = (
    <div className="flex items-center gap-1.5">
      <button type="button" className="btn-ghost !py-1 text-[10px]" disabled={current <= 1} onClick={() => setPage(current - 1)}>{t("Previous")}</button>
      <span className="text-[10px] text-ink-mute">{t("Page {page} of {pages}", { page: current, pages: lastPage })}</span>
      <button type="button" className="btn-ghost !py-1 text-[10px]" disabled={current >= lastPage} onClick={() => setPage(current + 1)}>{t("Next")}</button>
    </div>
  );
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <input type="search" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder={t("Search files…")} className="field !h-7 text-[11px]" />
        {/* #293: "per page" is one short word in English and two in Turkish
            ("sayfa başına"). Without this the label broke across two lines
            inside a 28px-tall row and read as clipped text. The parent already
            wraps, so the label moves to its own line instead of splitting. */}
        <label className="flex items-center gap-1 whitespace-nowrap text-[10px] text-ink-mute"><select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }} className="field !h-7 text-[10px]">{[25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}</select>{t("per page")}</label>
      </div>
      {pager}
    </div>
    {shown.map((file) => <RoutedFileCard key={`${file.route}:${file.name}`} file={file} />)}
    {total === 0 && search && <p className="rounded-lg bg-surface-sunken px-3 py-2 text-[10px] text-ink-mute">{t("No files match your search")}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-[10px] text-ink-mute">{t("Showing {shown} of {total}", { shown: shown.length, total })}</p>
      {pager}
    </div>
  </div>;
}

/** One routed file, explained on request rather than at all times (#386).
 *
 * Every card used to stack its insight, its routing reason, its table list and
 * its measured-from-content block unconditionally, so a panel holding more than
 * a couple of files was a wall of prose you scrolled past to find a file name.
 * Collapsed, the card is the row a person is scanning for: format, name, route.
 *
 * The exception is a file carrying a measurement that disagrees with its
 * extension, or one the measurement could not decide. Those are the reason
 * someone opens this panel, so such a card starts expanded -- and if it is
 * collapsed by hand, it still says why it mattered.
 */
function RoutedFileCard({ file }: { file: RoutedSourceFile }) {
  const attention = fileNeedsAttention(file);
  const [open, setOpen] = useState(attention);
  const needsDecision = file.measuredDeterministic === false && file.needsDecisionBecause;
  return <div className="rounded-xl border border-line bg-surface px-3 py-3">
    <button type="button" className="flex w-full items-center gap-2 text-left" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
      <FileBadge format={file.format} />
      <p className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{file.name}</p>
      <span className={cx("rounded-full px-2 py-1 text-[9px] font-semibold", file.route === "structured" ? "bg-ok-50 text-ok-700" : file.route === "documents" ? "bg-brand-50 text-brand-700" : "bg-warn-50 text-warn-700")}>{t(file.route === "structured" ? "Structured data" : file.route === "documents" ? "Documents" : "Needs review")}</span>
      {/* The name of the control, for anyone who cannot see the chevron. The
          row's own content already names the file it belongs to. */}
      <span className="sr-only">{open ? t("Hide file details") : t("Show file details")}</span>
      <Chevron open={open} />
    </button>
    {!open && attention && <div className="mt-2 space-y-1">
      {file.contradictsExtension === true && <p className="text-[10px] font-semibold leading-relaxed text-warn-800">{t("Extension and measurement disagree")}</p>}
      {needsDecision && <p className="text-[10px] leading-relaxed text-warn-700">{t("Needs a decision")}: {file.needsDecisionBecause}</p>}
    </div>}
    {open && <>
      <FileInsight insight={file.insight} />
      {file.reason && <p className="mt-2 text-[10px] leading-relaxed text-ink-mute">{local(file.reason)}</p>}
      {file.tableNames.length > 0 && <p className="mt-1 text-[9px] text-ink-faint">{t("Tables")}: {file.tableNames.join(", ")}</p>}
      <MeasuredType file={file} />
    </>}
  </div>;
}

function StructuredDetails({ profile, workspace, routing }: { profile: SourceProfile; workspace: StagingWorkspace | null; routing: StagingRoutingState }) {
  const files = routing.files.filter((file) => file.route === "structured");
  return <div className="space-y-5"><ProgressList steps={routing.structured} /><EvidenceSection title={t("Measured source facts")} tone="measured"><RoutingDetails files={files} /><div className="mt-3 grid grid-cols-2 gap-2"><Metric label={t("Tables")} value={profile.tables.length} /><Metric label={t("Structured rows")} value={profile.tables.reduce((sum, table) => sum + table.rows, 0).toLocaleString()} /></div></EvidenceSection>{workspace && <EvidenceSection title={t("Measured relationships")} tone="measured"><RelationshipList workspace={workspace} /></EvidenceSection>}</div>;
}

function DocumentDetails({ workspace, routing, onOpenArtifact, runId }: { workspace: StagingWorkspace | null; routing: StagingRoutingState; onOpenArtifact: (id: string) => void; runId?: string | null }) {
  const extraction = workspace?.document_extractions?.at(-1);
  const [reviewing, setReviewing] = useState(false);
  const processedPages = routing.documentFiles.filter((file) => file.status === "ready").reduce((sum, file) => sum + (file.pageCount ?? 0), 0);
  return <div className="space-y-5"><ProgressList steps={routing.documents} /><section className="rounded-xl border border-line p-4"><div className="grid grid-cols-2 gap-2"><Detail label={t("Selected engine")} value={`${routing.engine}${routing.engineVersion ? ` ${routing.engineVersion}` : ""}`} /><Detail label={t("OCR mode")} value={routing.ocrMode} /><Detail label={t("Processed pages")} value={processedPages} /><Detail label={t("Duration")} value={extraction ? formatDuration(extraction.duration_seconds) : t("In progress")} /><Detail label={t("Table candidates")} value={extraction?.table_candidates ?? 0} /><Detail label={t("Figure candidates")} value={extraction?.figure_candidates ?? 0} /></div></section><section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Per-file status")}</p><div className="mt-2 space-y-2">{routing.documentFiles.map((file) => <div key={file.sourceFile} className="rounded-lg border border-line bg-surface px-3 py-3"><div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{file.sourceFile}</span><Badge tone={file.status === "ready" ? "ok" : file.status === "failed" ? "stop" : "brand"}>{t(file.status === "queued" ? "Waiting" : file.status === "running" ? "Processing" : file.status === "ready" ? "Complete" : "Failed")}</Badge></div>{file.status === "ready" && <p className="mt-2 text-[10px] text-ink-mute">{t("{pages} pages · {tables} tables · {figures} figures", { pages: file.pageCount ?? 0, tables: file.tableCandidates ?? 0, figures: file.figureCandidates ?? 0 })}{file.durationSeconds !== undefined ? ` · ${formatDuration(file.durationSeconds)}` : ""}</p>}{file.warnings.map((warning) => <p key={warning.en} className="mt-2 text-[10px] text-warn-700">{local(warning)}</p>)}</div>)}</div></section>{(extraction?.table_candidates ?? 0) > 0 && <div className="rounded-xl border border-warn-200 bg-warn-50 px-3 py-3"><p className="text-xs font-semibold text-warn-800">{t("Candidate — not trusted structured data")}</p><p className="mt-1 text-[10px] text-warn-700">{t("Review and promote each extracted table before it can enter training data.")}</p>{runId && extraction?.artifact_id && <button type="button" className="btn-primary mt-3 w-full justify-center text-xs" onClick={() => setReviewing(true)}>{t("Review {count} extracted tables", { count: extraction.table_candidates })}</button>}</div>}
    {reviewing && runId && extraction?.artifact_id && <DocumentTableReview runId={runId} extractionArtifactId={extraction.artifact_id} onClose={() => setReviewing(false)} onPromoted={() => undefined} />}{localizedList(extraction?.warnings ?? [], extraction?.warnings_tr).map((warning) => <p key={warning} className="rounded-lg bg-warn-50 px-3 py-2 text-[10px] text-warn-700">{warning}</p>)}{extraction?.artifact_id && <button type="button" className="btn-primary w-full text-xs" onClick={() => onOpenArtifact(extraction.artifact_id!)}>{t("Open produced artifacts")}</button>}</div>;
}

function UnderstandingResults({ profile, workspace, onOpenArtifact }: { profile: SourceProfile; workspace: StagingWorkspace; onOpenArtifact: (artifactId: string) => void }) {
  const counts = sourceCounts(profile);
  const extraction = workspace.document_extractions?.at(-1);
  const reportIds = useMemo(() => (workspace.component_outputs ?? []).filter((output) => output.data_type === "reports").flatMap((output) => output.artifact_ids), [workspace.component_outputs]);
  const insights = workspace.reports.flatMap((report) => report.findings).slice(0, 3);
  const warnings = [
    ...localizedList(extraction?.warnings ?? [], extraction?.warnings_tr),
    ...((extraction?.table_candidates ?? 0) > 0 ? [t("Extracted tables are candidates and cannot enter ML until reviewed.")] : []),
  ];
  return <div className="space-y-5">
    <div className="grid grid-cols-2 gap-2"><Metric label={t("Documents")} value={counts.pdfs} /><Metric label={t("Structured files")} value={counts.structured} /><Metric label={t("Candidate document tables")} value={extraction?.table_candidates ?? 0} /><Metric label={t("Measured relationships")} value={workspace.relationship_explanations.length} /></div>
    <section className="space-y-2" aria-label={t("Understanding summary")}>
      {insights.map((insight) => <SignalLine key={insight.en} symbol="✦" tone="insight" text={local(insight)} />)}
      {warnings.map((warning) => <SignalLine key={warning} symbol="!" tone="warning" text={warning} />)}
      {!insights.length && !warnings.length && <SignalLine symbol="✓" tone="ok" text={t("Understanding completed without a reported warning.")} />}
    </section>
    <EvidenceSection title={t("Measured / extracted facts")} tone="measured"><SourceFiles profile={profile} compact />{extraction?.artifact_id && <button type="button" className="mt-3 w-full rounded-lg border border-line px-3 py-3 text-left hover:border-brand-300" onClick={() => onOpenArtifact(extraction.artifact_id!)}><p className="text-xs font-semibold text-ink">{t("Document extraction")}</p><p className="mt-1 text-[11px] text-ink-mute">{t("{tables} candidate tables · {figures} figures/charts", { tables: extraction.table_candidates, figures: extraction.figure_candidates })}</p><p className="mt-2 text-[10px] font-medium text-brand-700">{t("Open extracted content and provenance")}</p></button>}</EvidenceSection>
    <EvidenceSection title={t("Measured relationships")} tone="measured"><RelationshipList workspace={workspace} /></EvidenceSection>
    {workspace.reports.length ? <AgentReports reports={workspace.reports} reportIds={reportIds} onOpenArtifact={onOpenArtifact} /> : <Spinner label={t("The Planner is preparing an explanation…")} />}
  </div>;
}

/** The reports list, opened by the whole row rather than a native `<details>`.
 *
 * It used to be `<details>` with a `display: flex` `<summary>`, and clicking it
 * did nothing (#51) -- a `<summary>` that is not `display: list-item` is not a
 * dependable toggle across engines. Every other collapsible in the workspace is
 * already a button holding its own open state (see `Disclosure` in ui.tsx), so
 * this follows that rather than fighting the element: the row is one wide
 * target, and the chevron is only its indicator.
 */
function AgentReports({ reports, reportIds, onOpenArtifact }: { reports: StagingWorkspace["reports"]; reportIds: string[]; onOpenArtifact: (artifactId: string) => void }) {
  const [open, setOpen] = useState(false);
  return <div className="rounded-xl border border-violet-200 bg-violet-50/40">
    <button type="button" aria-expanded={open} onClick={() => setOpen((current) => !current)} className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-xs font-semibold text-violet-800 transition hover:bg-violet-100/60">
      <span aria-hidden="true">▣</span>
      <span className="flex-1">{t("Agent reports")}</span>
      <Badge tone="neutral">{reports.length}</Badge>
      <Chevron open={open} size="md" />
    </button>
    {open && <div className="space-y-3 border-t border-violet-200 p-4">{reports.map((report, index) => <article key={`${report.title.en}:${index}`} className="rounded-lg border border-violet-200 bg-surface px-3 py-3"><p className="text-xs font-semibold text-ink">{local(report.title)}</p><p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{local(report.summary)}</p>{report.findings.length > 0 && <ul className="mt-3 space-y-1">{report.findings.map((finding) => <li key={finding.en} className="text-[10px] leading-relaxed text-ink-mute">• {local(finding)}</li>)}</ul>}<button type="button" disabled={!reportIds[index]} onClick={() => reportIds[index] && onOpenArtifact(reportIds[index])} className="mt-3 text-[10px] font-semibold text-violet-700 disabled:text-ink-faint">{reportIds[index] ? t("Open report artifact") : t("Summary only")}</button></article>)}</div>}
  </div>;
}

function SignalLine({ symbol, tone, text }: { symbol: string; tone: "insight" | "warning" | "ok"; text: string }) {
  return <div className={cx("flex items-start gap-2 rounded-lg border px-3 py-2", tone === "warning" ? "border-warn-200 bg-warn-50 text-warn-800" : tone === "ok" ? "border-ok-200 bg-ok-50 text-ok-800" : "border-violet-200 bg-violet-50 text-violet-800")}><span className="grid h-4 w-4 shrink-0 place-items-center text-[10px] font-bold">{symbol}</span><p className="line-clamp-2 text-[10px] leading-relaxed">{text}</p></div>;
}

function RelationshipList({ workspace }: { workspace: StagingWorkspace }) {
  return workspace.relationship_explanations.length ? <ul className="space-y-2">{workspace.relationship_explanations.map((relationship) => <li key={`${relationship.from_table}:${relationship.from_columns.join(",")}:${relationship.to_table}:${relationship.to_columns.join(",")}`} className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-xs font-semibold text-ink">{relationship.from_table}.{relationship.from_columns.join(", ")} → {relationship.to_table}.{relationship.to_columns.join(", ")}</p><p className="mt-1 text-[11px] text-ink-mute">{t("{coverage}% measured coverage · {cardinality}", { coverage: (relationship.overlap_rate * 100).toFixed(1), cardinality: relationship.cardinality })}</p><p className="mt-1 text-[10px] text-warn-700">{t("{orphan}% unmatched rows", { orphan: (relationship.orphan_rate * 100).toFixed(1) })}</p></li>)}</ul> : <Empty title={t("No measured cross-table relationship")} hint={t("The system will not invent a join from similar names alone.")} />;
}

export function SourceOverview({ profile, onRemoveFile, onAddFiles, busy = false }: { profile: SourceProfile; onRemoveFile?: (name: string) => void; onAddFiles?: () => void; busy?: boolean }) {
  const counts = sourceCounts(profile);
  return <div><div className="grid grid-cols-2 gap-2"><Metric label={t("PDF documents")} value={counts.pdfs} /><Metric label={t("Structured files")} value={counts.structured} /><Metric label={t("Document pages")} value={counts.pages} /><Metric label={t("Structured rows")} value={counts.rows.toLocaleString()} /></div><div className="mt-5 flex items-center justify-between gap-2"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Files provided")}</p>{onAddFiles && <button type="button" className="btn-ghost !py-1 text-xs" disabled={busy} onClick={onAddFiles}>+ {t("Add files")}</button>}</div><SourceFiles profile={profile} onRemove={onRemoveFile} busy={busy} /></div>;
}

/** `onAccept` is optional: the merged canvas puts Accept in the one run
 *  toolbar, where it becomes Run the moment the plan is accepted, so this panel
 *  reviews the plan without offering a second, competing primary button.
 *
 *  #385: `onAdvanced` is optional for the same reason, and this is the whole
 *  regression. It used to be required, so `RoutingInspector` gated the entire
 *  panel on having one -- and the understanding canvas passes none of these
 *  handlers, so a run the Planner had declined showed "Plan not ready yet"
 *  beside a node reading "Blocked — no plan was created". The explanation is
 *  what the panel is for; only the actions depend on having somewhere to go. */
function PlanProposal({ profile, workspace, onAccept, onAdvanced, onOpenPlanner, busy }: { profile: SourceProfile; workspace: StagingWorkspace; onAccept?: () => void; onAdvanced?: () => void; onOpenPlanner?: () => void; busy: boolean }) {
  const plan = workspace.recommended_plan;
  if (!plan) return <Spinner label={t("The Planner is preparing a proposal…")} />;
  const recommendation = plan.pipeline_recommendation ?? "create_pipeline";
  if (recommendation !== "create_pipeline") {
    const denied = recommendation === "no_pipeline";
    return <div><Badge tone={denied ? "stop" : "warn"}>{t(denied ? "No ML pipeline recommended" : "Pipeline decision deferred")}</Badge><h3 className="mt-3 text-base font-semibold text-ink">{t(denied ? "Understanding complete — stop before ML" : "More evidence is needed before ML")}</h3><p className="mt-2 text-xs leading-relaxed text-ink-mute">{plan.decision_summary ? local(plan.decision_summary) : t("The Planner did not recommend an executable pipeline from the available evidence.")}</p>{plan.rationale.length > 0 && <div className="mt-5 rounded-lg bg-violet-50 px-3 py-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Agent rationale")}</p><ul className="mt-2 space-y-1">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></div>}{(onOpenPlanner || onAdvanced) && <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">{onOpenPlanner && <button type="button" className="btn-primary" onClick={onOpenPlanner}>{t("Ask the Planner to reconsider")}</button>}{onAdvanced && <button type="button" className="btn-ghost" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button>}</div>}<p className="mt-3 text-[10px] text-ink-faint">{t("No pipeline will run unless a human explicitly overrides this recommendation. The override is the Planner: tell it what it is missing and it can propose one.")}</p></div>;
  }
  const steps = visibleWorkflowSteps(workspace, activeLanguage());
  const target = String(plan.configuration.target_column ?? "");
  const goal = String(plan.configuration.problem_title ?? (target ? t("Model {target}", { target }) : t("Analyze and explain the available evidence")));
  const structuredFiles = (profile.source_files ?? []).filter((file) => file.route === "structured").map((file) => file.name);
  const documentFiles = (profile.source_files ?? []).filter((file) => file.route === "documents").map((file) => file.name);
  const baseTable = String(plan.configuration.base_table ?? profile.tables[0]?.name ?? "—");
  const scope = plan.checkpoint_stages.length ? t("Runs with {count} planned review checkpoints.", { count: plan.checkpoint_stages.length }) : t("Runs through the final report; hard safety gates still apply.");
  return <div><Badge tone="brand">{t("Proposed — not executable yet")}</Badge><h3 className="mt-3 text-base font-semibold text-ink">{goal}</h3><p className="mt-1 text-xs text-ink-mute">{t("Review what enters ML and where the base pipeline will stop before accepting.")}</p><section className="mt-5 rounded-xl border border-ok-200 bg-ok-50/50 p-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-ok-700">{t("Enters ML")}</p><p className="mt-2 text-xs font-semibold text-ink">{baseTable}</p><div className="mt-2 flex flex-wrap gap-1">{structuredFiles.map((file) => <Badge key={file} tone="ok" title={file} truncate>{file}</Badge>)}</div>{!structuredFiles.length && <p className="mt-2 text-[10px] text-warn-700">{t("No trusted structured ML input is selected.")}</p>}</section>{documentFiles.length > 0 && <section className="mt-3 rounded-xl border border-violet-200 bg-violet-50/40 p-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Context only")}</p><div className="mt-2 flex flex-wrap gap-1">{documentFiles.map((file) => <Badge key={file} title={file} truncate>{file}</Badge>)}</div><p className="mt-2 text-[10px] text-ink-mute">{t("Extracted PDF tables stay review-only until a human promotes them.")}</p></section>}<section className="mt-5"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Recommended continuation")}</p><ol className="mt-3 space-y-2">{steps.map((step, index) => <li key={`${step}:${index}`} className="flex gap-2 text-xs text-ink-soft"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-50 text-[10px] font-semibold text-brand-700">{index + 1}</span><span className="pt-0.5">{step}</span></li>)}</ol><p className="mt-3 rounded-lg bg-surface-sunken px-3 py-2 text-[10px] text-ink-mute">{scope}</p></section>{plan.rationale.length > 0 && <div className="mt-5 rounded-lg bg-violet-50 px-3 py-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Agent rationale")}</p><ul className="mt-2 space-y-1">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></div>}{(onAccept || onAdvanced) && <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">{onAccept && <button type="button" className="btn-primary" onClick={onAccept} disabled={busy}>{busy ? t("Accepting…") : t("Accept and add base pipeline")}</button>}{onAdvanced && <button type="button" className="btn-ghost" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button>}</div>}<p className="mt-3 text-[10px] text-ink-faint">{t("You can also ask the Planner to revise this proposal.")}</p></div>;
}

function ProgressList({ steps }: { steps: RoutingSubstep[] }) { return <ol className="space-y-2">{steps.map((step) => <li key={`${step.id}:${step.label}`} className={cx("flex items-start gap-2 rounded-lg px-3 py-2 text-xs", step.status === "running" ? "bg-brand-50 font-semibold text-brand-700" : step.status === "complete" ? "text-ok-700" : step.status === "failed" ? "bg-stop-50 text-stop-700" : "text-ink-faint")}><StatusMark status={step.status} /><span><span>{t(step.label)}</span>{step.detail && <span className="mt-0.5 block text-[10px] font-normal text-ink-mute">{t(step.detail)}</span>}</span></li>)}</ol>; }
/** The canvas, plus chrome that is deliberately outside its pan/zoom content.
 *
 * `children` are drawn into the scaled, scrolling content. `overlay` is not:
 * inspectors, dialogs, and the Planner button belong there. A `position: fixed`
 * element inside a transformed ancestor is
 * positioned against that ancestor rather than the viewport, which is why the
 * Planner button slid across the screen as the graph zoomed (#60), and being
 * inside the pan area is why dragging across a panel's text panned the graph
 * instead of selecting it (#56).
 */
export function CanvasSurface({ children, overlay, docked = false, plannerDocked = false }: { children: React.ReactNode; overlay?: React.ReactNode; docked?: boolean; plannerDocked?: boolean }) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [contentBox, setContentBox] = useState({ width: CANVAS_BASE_WIDTH, height: CANVAS_BASE_HEIGHT });
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const [panning, setPanning] = useState(false);
  const [step, setStep] = useState(0);

  // Attached by hand rather than through onWheel, because React's wheel handler
  // is passive and cannot call preventDefault -- without which ctrl+scroll
  // zooms the whole browser page instead of the graph.
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    function onWheel(event: WheelEvent) {
      if (!isZoomGesture(event)) return;
      event.preventDefault();
      setStep((current) => clampStep(current + (event.deltaY < 0 ? 1 : -1)));
    }
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);

  // The scroll extent has to follow the graph, not a guess about it (#203).
  // The content box below sizes itself to its content, so its laid-out width is
  // exactly what the scrolling wrapper must reserve -- and it changes without a
  // re-render whenever a node is resized or a branch appears. ResizeObserver
  // reports the *untransformed* layout box, which is the number wanted here:
  // the zoom factor is applied separately, so measuring the scaled size would
  // multiply it twice.
  useEffect(() => {
    const node = content.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      setContentBox((current) => {
        const width = node.offsetWidth || CANVAS_BASE_WIDTH;
        const height = node.offsetHeight || CANVAS_BASE_HEIGHT;
        return current.width === width && current.height === height
          ? current
          : { width, height };
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  function startPan(event: ReactPointerEvent<HTMLDivElement>) {
    // A press that lands on a control or inside a panel belongs there. Panels
    // are asides docked beside the canvas, and without them in this list a drag
    // across their text panned the graph instead of selecting the text (#56).
    if (event.button !== 0 || isCanvasPanBlocked(event.target)) return;
    const node = viewport.current;
    if (!node) return;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: node.scrollLeft, top: node.scrollTop };
    node.setPointerCapture(event.pointerId);
    setPanning(true);
  }
  function movePan(event: ReactPointerEvent<HTMLDivElement>) {
    const origin = drag.current;
    const node = viewport.current;
    if (!origin || !node || origin.pointerId !== event.pointerId) return;
    node.scrollLeft = origin.left - (event.clientX - origin.x);
    node.scrollTop = origin.top - (event.clientY - origin.y);
  }
  function stopPan(event: ReactPointerEvent<HTMLDivElement>) {
    const node = viewport.current;
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    releaseCanvasPointer(node, event.pointerId);
    setPanning(false);
  }
  function lostPanCapture(event: ReactPointerEvent<HTMLDivElement>) {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setPanning(false);
  }
  useEffect(() => () => {
    const active = drag.current;
    drag.current = null;
    if (active) releaseCanvasPointer(viewport.current, active.pointerId);
  }, []);

  // The inspector used to be fixed over a full-width viewport, so opening it
  // made the right-hand nodes unreachable even after panning (#40). The
  // Planner then covered that inspector from the same right edge (#48). Grid
  // columns make the viewport surrender each panel's width; when both are open,
  // the 47vw caps keep the two docks distinct even on a narrow viewport.
  // The controls sit outside the scrolling element, not inside it. Inside, they
  // rode the scaled content: zooming in moved the bar up the screen and zooming
  // out did not bring it back the same way (#59), and anything else docked over
  // the canvas drifted with it (#60).
  // One implicit row, sized `auto`, is `minmax(auto, max-content)`: it grows to
  // the tallest item's *content* height rather than being clamped to the
  // container. A docked panel with enough in it -- the uploaded-files inspector
  // of a source with many files -- pushed that row past the surface, so its
  // `h-full` resolved to the grown row, its `overflow-y-auto` body was never
  // shorter than its content, and the panel simply had nothing to scroll: no
  // scrollbar, no wheel movement, and the bottom unreachable past `<main>`.
  // Reopening it landed in the same geometry, which is why closing the panel
  // never helped (#161). `minmax(0, 1fr)` clamps the row to the surface; the
  // `min-h-0` on each docked column disables the grid item's automatic minimum
  // size, without which an item still refuses to shrink below its content.
  const gridTemplateRows = "minmax(0, 1fr)";
  const gridTemplateColumns = docked && plannerDocked
    ? "minmax(0, 1fr) minmax(0, min(440px, 47vw)) minmax(0, min(390px, 47vw))"
    : docked ? "minmax(0, 1fr) min(440px, 94vw)"
      : plannerDocked ? "minmax(0, 1fr) min(390px, 94vw)" : "minmax(0, 1fr)";
  return <div className="relative grid h-full min-h-[30rem] overflow-hidden" style={{ gridTemplateColumns, gridTemplateRows }}>
    <div ref={viewport} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={stopPan} onPointerCancel={stopPan} onLostPointerCapture={lostPanCapture} className={cx("h-full min-w-0 overflow-auto bg-surface-sunken bg-[radial-gradient(#d9e0ea_1px,transparent_1px)] [background-size:20px_20px]", panning ? "cursor-grabbing select-none" : "cursor-grab")}>
      <div style={scaledBox(step, contentBox)}>
        {/* `width: max-content` with the base box as a floor, so the box always
            contains the graph. `justify-center` is kept -- it is safe once the
            box can never be narrower than its content, and it is what centres a
            small graph in a large canvas. What it cannot do is centre content
            it does not fit, which is #203. */}
        <div ref={content} className="flex items-center justify-center" style={{ width: "max-content", minWidth: CANVAS_BASE_WIDTH, minHeight: CANVAS_BASE_HEIGHT, transform: `scale(${zoomForStep(step)})`, transformOrigin: "top left" }}>{children}</div>
      </div>
    </div>
    {overlay}
    <div data-no-pan className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-end px-3">
      <div className="pointer-events-auto flex items-center gap-1 rounded-xl border border-line bg-surface/95 p-1 shadow-card backdrop-blur">
        <ZoomControl label={t("Zoom out")} disabled={step <= CANVAS_MIN_STEP} onClick={() => setStep((current) => clampStep(current - 1))}>−</ZoomControl>
        <select aria-label={t("Zoom level")} value={step} onChange={(event) => setStep(clampStep(Number(event.target.value)))} className="min-w-[4.25rem] rounded-lg bg-transparent px-1 py-1 text-center text-[10px] font-semibold tabular-nums text-ink-mute outline-none transition hover:bg-surface-sunken">
          {ZOOM_STEPS.map((value) => <option key={value} value={value}>{zoomPercent(value)}%</option>)}
        </select>
        <ZoomControl label={t("Zoom in")} disabled={step >= CANVAS_MAX_STEP} onClick={() => setStep((current) => clampStep(current + 1))}>+</ZoomControl>
      </div>
    </div>
  </div>;
}

/** Every selectable level, so the control can offer them directly rather than
 *  only as increments (#58). */
const ZOOM_STEPS = Array.from({ length: CANVAS_MAX_STEP - CANVAS_MIN_STEP + 1 }, (_, index) => CANVAS_MIN_STEP + index);

function ZoomControl({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} disabled={disabled} title={label} aria-label={label} className="grid h-7 w-7 place-items-center rounded-lg text-sm font-semibold text-ink-soft transition hover:bg-surface-sunken disabled:opacity-40">{children}</button>;
}
function GraphEdge({ status }: { status: ProgressStatus }) { return <div className={cx("relative h-px w-10 shrink-0", status === "complete" ? "bg-ok-300" : status === "failed" ? "bg-stop-300" : "bg-slate-300")}><span className="absolute -right-1 -top-[3px] h-2 w-2 rotate-45 border-r border-t border-slate-400" />{status === "running" && <span className="absolute inset-y-[-1px] left-0 w-5 animate-pulse rounded-full bg-brand-400 motion-reduce:animate-none" />}</div>; }
function ForkConnector({ branches, status }: { branches: number; status: ProgressStatus }) { const height = Math.max(40, (branches - 1) * 178); return <svg aria-hidden="true" className="w-12 shrink-0" style={{ height }} viewBox={`0 0 48 ${height}`} preserveAspectRatio="none"><path d={`M0 ${height / 2} H20 M20 ${height / 2} V8 M20 ${height / 2} V${height - 8} M20 8 H48 M20 ${height - 8} H48`} fill="none" stroke={status === "complete" ? "#86c99a" : "#cbd5e1"} strokeWidth="1.5" /></svg>; }
function MergeConnector({ branches, status }: { branches: number; status: ProgressStatus }) { const height = Math.max(40, (branches - 1) * 178); return <svg aria-hidden="true" className="w-12 shrink-0" style={{ height }} viewBox={`0 0 48 ${height}`} preserveAspectRatio="none"><path d={`M0 8 H28 M0 ${height - 8} H28 M28 8 V${height - 8} M28 ${height / 2} H48`} fill="none" stroke={status === "complete" ? "#86c99a" : status === "running" ? "#4f7cff" : "#cbd5e1"} strokeWidth="1.5" /><path d={`M43 ${height / 2 - 4} L48 ${height / 2} L43 ${height / 2 + 4}`} fill="none" stroke="#94a3b8" strokeWidth="1.5" /></svg>; }
function PhaseNode({ title, subtitle, footer, status, onClick, artifactIds = [], activeArtifactId, onOpenArtifact, compact = false }: { title: string; subtitle: string; footer?: string; status: ProgressStatus; onClick?: () => void; artifactIds?: string[]; activeArtifactId?: string | null; onOpenArtifact?: (id: string) => void; compact?: boolean }) { const content = <><NodeStatusHeader status={status} /><p className="mt-3 truncate text-sm font-semibold text-ink">{title}</p><p className="mt-1 line-clamp-2 text-[11px] text-ink-mute">{subtitle}</p>{footer && <p className="mt-3 truncate text-[10px] font-medium text-brand-700">{footer}</p>}</>; const className = cx(compact ? "h-full w-full p-4" : "h-full w-full p-5", "overflow-hidden rounded-2xl border bg-surface text-left shadow-card transition", status === "running" ? "border-brand-400 ring-4 ring-brand-50" : status === "failed" ? "border-stop-300" : "border-line", onClick && "hover:-translate-y-0.5 hover:border-brand-300"); const card = onClick ? <button type="button" onClick={onClick} className={className}>{content}</button> : <article className={className}>{content}</article>; return <ResizableNode className="relative shrink-0" defaultWidth={compact ? 190 : 230}>{card}<ArtifactNodes ids={artifactIds} activeId={activeArtifactId} onOpen={onOpenArtifact ?? (() => {})} /></ResizableNode>; }
export function Inspector({ title, eyebrow, onClose, children }: { title: string; eyebrow: string; onClose: () => void; children: React.ReactNode }) { return <aside className="z-20 flex h-full min-h-0 w-full flex-col border-l border-line bg-surface shadow-2xl"><header className="flex items-start gap-3 border-b border-line px-5 pb-4 pt-5"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-600">{eyebrow}</p><h2 className="mt-1 text-base font-semibold text-ink">{title}</h2></div><button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close inspector")} onClick={onClose}>×</button></header>{/* Scroll only the body: overflow used to sit on the aside, so the header
    and its × scrolled out of reach on a long panel (#75). */}<div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5"><div className="mt-5">{children}</div></div></aside>; }
export function DockedPanel({ children }: { children: React.ReactNode }) { return <div data-docked-panel className="z-30 flex h-full min-h-0 min-w-0 w-full overflow-hidden border-l border-line bg-surface shadow-2xl [&>aside]:!w-full">{children}</div>; }
function EvidenceSection({ title, tone, children }: { title: string; tone: "measured" | "interpretation"; children: React.ReactNode }) { return <section className={cx("rounded-xl border p-4", tone === "measured" ? "border-sky-200 bg-sky-50/40" : "border-violet-200 bg-violet-50/40")}><p className={cx("text-[10px] font-semibold uppercase tracking-[0.12em]", tone === "measured" ? "text-sky-700" : "text-violet-700")}>{title}</p><div className="mt-3">{children}</div></section>; }
function SourceFiles({ profile, compact = false, onRemove, busy = false }: { profile: SourceProfile; compact?: boolean; onRemove?: (name: string) => void; busy?: boolean }) {
  const files = buildStagingRoutingState(profile, null, null).files;
  return <div className={compact ? "mt-2 space-y-1.5" : "mt-3 space-y-2"}>{files.map((file) => <div key={`${file.route}:${file.name}`} className="rounded-lg border border-line bg-surface px-3 py-2"><div className="flex items-center gap-3"><FileBadge format={file.format} /><span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">{file.name}</span><span className="text-[10px] text-ink-mute">{t(file.route === "structured" ? "Structured" : file.route === "documents" ? "Document" : "Needs review")}</span>{onRemove && <button type="button" aria-label={t("Remove file")} title={t("Remove file")} disabled={busy} onClick={() => onRemove(file.name)} className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-ink-faint hover:bg-stop-50 hover:text-stop-700 disabled:opacity-40">×</button>}</div><FileInsight insight={file.insight} /></div>)}</div>;
}
function FileBadge({ format }: { format: string }) { return <span className="rounded bg-surface-sunken px-2 py-0.5 text-[9px] font-semibold uppercase text-ink-mute">{format}</span>; }
function Metric({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg border border-line bg-surface px-3 py-2"><p className="text-lg font-semibold text-ink">{value}</p><p className="mt-0.5 text-[10px] text-ink-mute">{label}</p></div>; }
function Detail({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-[9px] uppercase tracking-wide text-ink-faint">{label}</p><p className="mt-1 truncate text-xs font-semibold text-ink">{value}</p></div>; }
function formatDuration(seconds: number): string { return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`; }

export function ArtifactDialog({ preview, onClose }: { preview: ArtifactPreview; onClose: () => void }) {
  const isDocument = preview.artifact_type === "document_extraction";
  // An artifact with no summary, no findings and no document body used to
  // render a literally blank dialog under a generic "ARTIFACT" eyebrow, which
  // read as broken (#74). Its sibling ArtifactModal already shows a fallback;
  // match it so the body is never empty.
  // #304: EDA and the other measured stages already carry chart panels
  // (distributions, missingness, a correlation heatmap, target relationships)
  // built by the backend, but this dialog rendered only text and scalars -- the
  // charts existed and never reached the person. Render them when present, and
  // count them as content so an all-charts artifact is not judged empty.
  const panels = (preview.panels ?? []) as AnalysisPanel[];
  const hasContent = Boolean(preview.summary || isDocument || preview.findings?.length || panels.length || hasArtifactMetadata(preview));
  return <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true"><div className="max-h-[86vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl"><div className="flex items-start gap-4"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-wide text-brand-600">{t(artifactTypeLabel(preview.artifact_type))}</p><h3 className="mt-1 text-lg font-semibold text-ink">{artifactTitle(preview, activeLanguage(), t)}</h3></div><button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close artifact")} onClick={onClose}>×</button></div>{preview.summary && <p className="mt-3 text-sm leading-relaxed text-ink-mute">{local(preview.summary)}</p>}{isDocument && <><div className="mt-4 grid grid-cols-3 gap-2"><Detail label={t("Selected engine")} value={`${preview.engine ?? "—"}${preview.engine_version ? ` ${preview.engine_version}` : ""}`} /><Detail label={t("OCR mode")} value={String(preview.ocr_mode ?? "auto")} /><Detail label={t("Duration")} value={formatDuration(Number(preview.duration_seconds ?? 0))} /></div><div className="mt-4 space-y-3">{preview.documents?.map((document) => <section key={document.source_file} className="rounded-xl border border-line p-4"><div className="flex items-center justify-between gap-3"><p className="truncate text-sm font-semibold text-ink">{document.source_file}</p><span className="text-[10px] text-ink-mute">{document.page_count} {t("pages")}</span></div><p className="mt-2 text-[10px] text-ink-mute">{document.tables.length} {t("table candidates")} · {document.figures.length} {t("figure candidates")}</p>{document.tables.map((table) => <div key={String(table.candidate_id)} className="mt-3 rounded-lg border border-warn-200 bg-warn-50 px-3 py-2"><p className="text-[10px] font-semibold text-warn-800">{t("Candidate — not trusted structured data")}</p><p className="mt-1 text-[10px] text-warn-700">{String(table.title ?? table.candidate_id)} · {t("page {page}", { page: String(table.page_number ?? "—") })}</p></div>)}</section>)}</div></>}{panels.length > 0 && <div className="mt-4"><AnalysisStrip panels={panels} /></div>}{preview.findings && <ul className="mt-4 space-y-2">{preview.findings.map((finding) => <li key={finding.en} className="rounded-lg bg-surface-sunken px-3 py-2 text-xs text-ink-soft">{local(finding)}</li>)}</ul>}<ArtifactMetadata preview={preview} />{!hasContent && <div className="mt-4"><Empty title={t("Artifact recorded")} hint={t("Open its stage inspection for measurements and provenance.")} /></div>}</div></div>;
}
