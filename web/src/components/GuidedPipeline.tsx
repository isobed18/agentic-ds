import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  api,
  type ArtifactPreview,
  type PipelineOutputReference,
  type SourceProfile,
  type StageDetail,
  type StagingWorkspace,
  type Workflow,
  type WorkflowNode,
} from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { elapsedLabel, isActive, isAttention, isSucceeded, statusLabel } from "../lib/status";
import { Badge, Empty, cx } from "./ui";
import { ArtifactMetadata, hasArtifactMetadata } from "./ArtifactMetadata";
import { ArtifactNodes } from "./ArtifactNodes";
import { artifactTitle } from "./artifactTitle";
import { isCanvasPanBlocked, releaseCanvasPointer } from "./canvasPan";
import { PlannerPanel } from "./PlannerPanel";
import { ResizableNode } from "./ResizableNode";

interface GuidedPipelineProps {
  runId: string;
  profile: SourceProfile;
  workspace: StagingWorkspace;
  componentOutputs: PipelineOutputReference[];
  runStatus: string | null;
  busy: boolean;
  onRun: (runMode: "fully_auto" | "manual") => void;
  onPause: () => void;
  onRetry: () => void;
  onAdvanced: () => void;
  onOpenExecutions: () => void;
}

/** The fixed ML spine, shared with the proposed-plan preview so the shape shown
 *  during staging is the shape that will actually run. Exported rather than
 *  copied: tests/test_guided_pipeline_groups.py holds every id here to the
 *  workflow spec, and a second hand-maintained list would not inherit that. */
export const GROUPS: Array<{ id: string; title: string; description: string; stages: string[] }> = [
  {
    id: "prepare",
    title: "Prepare ML data",
    description: "Combine approved tables into one verified modeling dataset.",
    stages: ["integration"],
  },
  {
    id: "objective",
    title: "Define the objective",
    description: "Choose the prediction goal and confirm that the available data can support it.",
    stages: ["problem_discovery"],
  },
  // `eda` and `leakage_audit` are the ids the established workflow declares.
  // This group previously named `exploratory_analysis` (an artifact type) and
  // `lineage_audit` (an id nothing produces), so both stages ran but were
  // silently dropped from this group's status, inspection, and artifact count.
  // tests/test_guided_pipeline_groups.py holds every id here to the spec.
  {
    id: "analysis",
    title: "Analyze and validate",
    description: "Measure patterns, choose validation, and check for leakage before training.",
    stages: ["validation_strategy", "eda", "leakage_audit"],
  },
  {
    id: "features",
    title: "Build and split",
    description: "Create model-ready features and divide the data without contaminating evaluation.",
    stages: ["feature_pipeline", "splitting"],
  },
  {
    id: "model",
    title: "Train and evaluate",
    description: "Train candidate models and compare them on held-out data.",
    stages: ["training", "evaluation"],
  },
  {
    id: "report",
    title: "Review results",
    description: "Summarize the selected model, evidence, limitations, and next steps.",
    stages: ["report"],
  },
];

function local(value: { en: string; tr: string }): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

export function GuidedPipeline({ runId, profile, workspace, componentOutputs, runStatus, busy, onRun, onPause, onRetry, onAdvanced, onOpenExecutions }: GuidedPipelineProps) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [progress, setProgress] = useState<Record<string, unknown> | null>(null);
  const [selected, setSelected] = useState<string | null>("summary");
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
  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    const refresh = async () => {
      try {
        const [nextWorkflow, nextProgress] = await Promise.all([api.workflow(runId), api.runProgress(runId)]);
        if (cancelled) return;
        setWorkflow(nextWorkflow);
        setProgress(nextProgress);
        const status = String(nextProgress.status ?? "");
        if (["queued", "staging", "running", "resuming"].includes(status)) timer = window.setTimeout(refresh, 1500);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
      }
    };
    void refresh();
    return () => { cancelled = true; if (timer !== null) window.clearTimeout(timer); };
  }, [runId]);

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
  const nodesById = useMemo(() => new Map((workflow?.nodes ?? []).map((node) => [node.id, node])), [workflow]);
  const groups = GROUPS.map((group) => ({ ...group, nodes: group.stages.map((stage) => nodesById.get(stage)).filter(Boolean) as WorkflowNode[] }));
  const structured = (profile.source_files ?? []).filter((file) => file.route === "structured");
  const documents = (profile.source_files ?? []).filter((file) => file.route === "documents");
  const candidateTables = workspace.document_extractions?.reduce((sum, extraction) => sum + extraction.table_candidates, 0) ?? 0;
  const activeStatus = String(progress?.status ?? runStatus ?? "staged");
  const canStart = activeStatus === "staged";
  const failed = ["failed", "interrupted", "aborted"].includes(activeStatus);
  const complete = activeStatus === "completed";

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

  return <PanCanvas>
    <div className="flex min-w-[1530px] items-center gap-5 px-8 pb-32 pt-20">
      <GuidedNode title={t("Data understood")} subtitle={t("Intake, routing, and synthesis complete")} status="succeeded" artifactIds={componentOutputs.flatMap((output) => output.artifact_ids)} onClick={() => setSelected("summary")} onOpenArtifact={(id) => void openArtifact(id)} />
      <Arrow active={false} complete />
      {groups.map((group, index) => {
        const status = groupStatus(group.nodes, activeStatus, index);
        const groupArtifactIds = group.stages.flatMap((stage) => artifactIdsByStage.get(stage) ?? []);
        return <div key={group.id} className="contents"><GuidedNode title={t(group.title)} subtitle={t(group.description)} status={status} artifactIds={groupArtifactIds} onClick={() => void inspectGroup(group.id)} onOpenArtifact={(id) => void openArtifact(id)} />{index < groups.length - 1 && <Arrow active={status === "running" || status === "retry"} complete={isSucceeded(status)} />}</div>;
      })}
    </div>

    <div className="fixed bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-line bg-surface/95 p-2 shadow-pop backdrop-blur">
      {canStart && !String((progress as Record<string, unknown> | null)?.current_stage ?? "") && (
        <label className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-[11px] font-medium text-ink-soft" title={t("The agent decides each gate on its own signals unless you take that over.")}>
          <input type="checkbox" checked={approveEachStage} onChange={(event) => setApproveEachStage(event.target.checked)} className="h-3.5 w-3.5" />
          {t("Approve at every stage")}
        </label>
      )}
      {canStart && <button type="button" className="btn-primary text-xs" onClick={() => onRun(approveEachStage ? "manual" : "fully_auto")} disabled={busy || !profile.tables.length}>{busy ? t("Working…") : t(String((progress as Record<string, unknown> | null)?.current_stage ?? "") ? "Continue base pipeline" : "Run base ML pipeline")}</button>}
      {["running", "resuming"].includes(activeStatus) && <button type="button" className="btn-ghost text-xs" onClick={onPause} disabled={busy || Boolean(progress?.pause_requested)}>{progress?.pause_requested ? t("Pause requested…") : t("Pause after current stage")}</button>}
      {failed && <button type="button" className="btn-primary text-xs" onClick={onRetry} disabled={busy}>{t("Retry from Intake")}</button>}
      {complete && <button type="button" className="btn-primary text-xs" onClick={onOpenExecutions}>{t("Review results")}</button>}
      {!canStart && !failed && !complete && <Badge tone="brand">{statusLabel(activeStatus)}</Badge>}
      <button type="button" className="btn-ghost text-xs" onClick={() => setSelected("summary")}>{t("Review plan")}</button>
      <button type="button" className="btn-ghost text-xs" onClick={() => setPlannerOpen(true)}>{t("Chat with Planner")}</button>
      <button type="button" className="btn-ghost text-xs" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button>
    </div>

    {/* #97: the planner is consultable while the pipeline runs and while a gate
        waits on a human, not only during staging. Docked over the right edge,
        above the plan/stage details panel, with its own close control. */}
    {plannerOpen && <div className="fixed inset-y-[58px] right-0 z-30 flex"><PlannerPanel runId={runId} open onToggle={() => setPlannerOpen(false)} starterPrompts={[t("What is this gate asking?"), t("What do you recommend here?"), t("Explain the current stage.")]} /></div>}

    {selected && <aside data-no-pan className="fixed inset-y-[58px] right-0 z-20 flex w-[min(440px,94vw)] flex-col border-l border-line bg-surface shadow-2xl">
      <header className="flex items-start gap-3 border-b border-line px-5 pb-4 pt-5"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-600">{t(selected === "summary" ? "Accepted ML plan" : "Base ML pipeline")}</p><h2 className="mt-1 text-base font-semibold text-ink">{t(selected === "summary" ? "What will run" : groups.find((group) => group.id === selected)?.title ?? "Stage details")}</h2></div><button type="button" className="btn-ghost !px-2 !py-1" onClick={() => setSelected(null)}>×</button></header>
      {/* Scroll only the body: overflow used to sit on the aside, so the header
          and its × scrolled out of reach on a long panel (#75). */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {error && <p className="mt-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
        {selected === "summary" ? <PlanSummary profile={profile} workspace={workspace} structured={structured.map((file) => file.name)} documents={documents.map((file) => file.name)} candidateTables={candidateTables} /> : <div className="mt-5 space-y-4">{groups.find((group) => group.id === selected)?.nodes.map((node) => <StageRow key={node.id} node={node} artifactIds={artifactIdsByStage.get(node.id) ?? []} onInspect={() => void inspectStage(node.id)} onOpenArtifact={(id) => void openArtifact(id)} />)}{detail && <StageEvidence detail={detail} onOpenArtifact={(id) => void openArtifact(id)} />}</div>}
      </div>
    </aside>}
    {preview && <ArtifactModal preview={preview} onClose={() => setPreview(null)} />}
  </PanCanvas>;
}

function groupStatus(nodes: WorkflowNode[], runStatus: string, groupIndex: number): WorkflowNode["status"] {
  if (nodes.some((node) => node.status === "failed" || node.status === "blocked")) return nodes.find((node) => isAttention(node.status))!.status;
  if (nodes.some((node) => isActive(node.status))) return nodes.find((node) => isActive(node.status))!.status;
  if (nodes.length && nodes.every((node) => isSucceeded(node.status))) return "succeeded";
  if (["failed", "interrupted", "aborted"].includes(runStatus) && groupIndex === 0 && !nodes.length) return "failed";
  return "pending";
}

function PlanSummary({ profile, workspace, structured, documents, candidateTables }: { profile: SourceProfile; workspace: StagingWorkspace; structured: string[]; documents: string[]; candidateTables: number }) {
  const plan = workspace.recommended_plan!;
  const config = plan.configuration;
  const target = String(config.target_column ?? "");
  const objective = String(config.problem_title ?? (target ? t("Model {target}", { target }) : t("The objective will be finalized during problem discovery")));
  const baseTable = String(config.base_table ?? profile.tables[0]?.name ?? "—");
  const baseGrain = Array.isArray(config.base_grain) ? config.base_grain.map(String).join(", ") : "—";
  return <div className="mt-5 space-y-5">
    <section className="rounded-xl border border-ok-200 bg-ok-50/50 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ok-700">{t("ML inputs")}</p><p className="mt-2 text-sm font-semibold text-ink">{baseTable}</p><p className="mt-1 text-[11px] text-ink-mute">{t("Grain")}: {baseGrain}</p><FileRoles title={t("Enters ML now")} files={structured} tone="ok" empty={t("No trusted structured input is selected.")} /></section>
    {documents.length > 0 && <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Context only")}</p><FileRoles title={t("Document context")} files={documents} tone="neutral" empty="" /><p className="mt-3 text-[10px] leading-relaxed text-ink-mute">{candidateTables ? t("{count} extracted table candidates remain review-only until explicitly promoted.", { count: candidateTables }) : t("Documents inform understanding but do not silently become training rows.")}</p></section>}
    <section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("ML objective")}</p><p className="mt-2 text-sm font-semibold text-ink">{objective}</p>{target && <p className="mt-1 text-[11px] text-ink-mute">{t("Target")}: {target}</p>}</section>
    <section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Execution scope")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{t("Run the established base pipeline through integration, analysis, training, evaluation, and reporting.")}</p>{plan.checkpoint_stages.length > 0 ? <div className="mt-3 flex flex-wrap gap-1.5">{plan.checkpoint_stages.map((stage) => <Badge key={stage} tone="warn">{t("Review after {stage}", { stage: stage.replaceAll("_", " ") })}</Badge>)}</div> : <p className="mt-2 text-[10px] text-ink-faint">{t("No optional human checkpoints; hard safety gates still apply.")}</p>}</section>
    {plan.rationale.length > 0 && <section className="rounded-xl bg-violet-50 p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Planner rationale")}</p><ul className="mt-2 space-y-2">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></section>}
  </div>;
}

function FileRoles({ title, files, tone, empty }: { title: string; files: string[]; tone: "ok" | "neutral"; empty: string }) { return <div className="mt-3"><p className="text-[10px] font-medium text-ink-mute">{title}</p>{files.length ? <div className="mt-2 flex flex-wrap gap-1.5">{files.map((file) => <Badge key={file} tone={tone} title={file} truncate>{file}</Badge>)}</div> : <p className="mt-1 text-[10px] text-warn-700">{empty}</p>}</div>; }

function GuidedNode({ title, subtitle, status, artifactIds, onClick, onOpenArtifact }: { title: string; subtitle: string; status: WorkflowNode["status"]; artifactIds: string[]; onClick: () => void; onOpenArtifact: (id: string) => void }) {
  return <ResizableNode className="relative shrink-0" defaultWidth={205}><button type="button" onClick={onClick} className={cx("h-full w-full overflow-hidden rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300", isActive(status) && "border-brand-400 ring-4 ring-brand-50", isAttention(status) && "border-stop-300")}><div className="flex items-center justify-between"><StatusDot status={status} /><Badge tone={isSucceeded(status) ? "ok" : isAttention(status) ? "stop" : isActive(status) ? "brand" : "neutral"}>{statusLabel(status)}</Badge></div><p className="mt-3 truncate text-sm font-semibold text-ink">{title}</p><p className="mt-1 line-clamp-2 min-h-[2rem] text-[10px] leading-relaxed text-ink-mute">{subtitle}</p></button><ArtifactNodes ids={artifactIds} onOpen={onOpenArtifact} /></ResizableNode>;
}

// The head tracks the line: once #196 made `bg-ok-300` a real class, a
// completed arrow drew an emerald line into a grey head. Each half is one stop
// deeper than its line so the point stays legible against it.
function Arrow({ active, complete }: { active: boolean; complete: boolean }) { return <div className={cx("relative h-px w-8 shrink-0", complete ? "bg-ok-300" : "bg-slate-300")}><span className={cx("absolute -right-1 -top-[3px] h-2 w-2 rotate-45 border-r border-t", complete ? "border-ok-400" : "border-slate-400")} />{active && <span className="absolute inset-y-[-1px] left-0 w-5 animate-pulse rounded-full bg-brand-400" />}</div>; }
function StatusDot({ status }: { status: WorkflowNode["status"] }) { return <span className={cx("relative grid h-5 w-5 place-items-center rounded-full border text-[9px]", isSucceeded(status) ? "border-ok-300 bg-ok-50 text-ok-700" : isAttention(status) ? "border-stop-300 bg-stop-50 text-stop-700" : isActive(status) ? "border-brand-400 bg-brand-50 text-brand-700" : "border-line text-ink-faint")}>{isSucceeded(status) ? "✓" : isAttention(status) ? "!" : isActive(status) ? <><span>●</span><span className="absolute inset-0 animate-ping rounded-full bg-brand-300 opacity-40" /></> : "○"}</span>; }

function StageRow({ node, artifactIds, onInspect, onOpenArtifact }: { node: WorkflowNode; artifactIds: string[]; onInspect: () => void; onOpenArtifact: (id: string) => void }) { return <section className="rounded-xl border border-line p-3"><button type="button" className="flex w-full items-start gap-3 text-left" onClick={onInspect}><StatusDot status={node.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-ink">{t(node.label ?? node.id.replaceAll("_", " "))}</span><span className="mt-1 block text-[10px] text-ink-mute">{statusLabel(node.status)}{elapsedLabel(node.elapsed_seconds) ? ` · ${elapsedLabel(node.elapsed_seconds)}` : ""}</span></span></button>{artifactIds.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">{artifactIds.map((id, index) => <button type="button" key={id} onClick={() => onOpenArtifact(id)} className="rounded-md bg-brand-50 px-2 py-1 text-[9px] font-semibold text-brand-700">▣ {t("Artifact {number}", { number: index + 1 })}</button>)}</div>}</section>; }

function StageEvidence({ detail, onOpenArtifact }: { detail: StageDetail; onOpenArtifact: (id: string) => void }) { const outputs = detail.outputs ?? []; return <section className="rounded-xl bg-surface-sunken p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Inspection")}</p><p className="mt-2 text-xs leading-relaxed text-ink-mute">{detail.stage.description}</p>{outputs.length ? <div className="mt-3 space-y-2">{outputs.map((output) => <button type="button" key={output.artifact_id} onClick={() => onOpenArtifact(output.artifact_id)} className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-left text-[10px] font-semibold text-brand-700">{output.name || output.type} · {t("Open artifact")}</button>)}</div> : <p className="mt-3 text-[10px] text-ink-faint">{t("No artifacts produced yet.")}</p>}</section>; }

function ArtifactModal({ preview, onClose }: { preview: ArtifactPreview; onClose: () => void }) { const hasContent = Boolean(preview.summary || preview.findings?.length || hasArtifactMetadata(preview)); return <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true"><div className="max-h-[82vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl"><div className="flex items-start gap-3"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-wide text-brand-600">{String(preview.artifact_type).replaceAll("_", " ")}</p><h3 className="mt-1 text-lg font-semibold text-ink">{artifactTitle(preview, activeLanguage(), t)}</h3></div><button type="button" className="btn-ghost !px-2 !py-1" onClick={onClose}>×</button></div>{preview.summary && <p className="mt-4 text-sm leading-relaxed text-ink-mute">{local(preview.summary)}</p>}{preview.findings?.length ? <ul className="mt-4 space-y-2">{preview.findings.map((finding) => <li key={finding.en} className="rounded-lg bg-surface-sunken px-3 py-2 text-xs text-ink-soft">{local(finding)}</li>)}</ul> : null}<ArtifactMetadata preview={preview} />{!hasContent && <Empty title={t("Artifact recorded")} hint={t("Open its stage inspection for measurements and provenance.")} />}</div></div>; }

function PanCanvas({ children }: { children: React.ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const [panning, setPanning] = useState(false);
  function start(event: ReactPointerEvent<HTMLDivElement>) { if (event.button !== 0 || isCanvasPanBlocked(event.target)) return; const node = viewport.current; if (!node) return; drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: node.scrollLeft, top: node.scrollTop }; node.setPointerCapture(event.pointerId); setPanning(true); }
  function move(event: ReactPointerEvent<HTMLDivElement>) { const origin = drag.current; const node = viewport.current; if (!origin || !node || origin.pointerId !== event.pointerId) return; node.scrollLeft = origin.left - (event.clientX - origin.x); node.scrollTop = origin.top - (event.clientY - origin.y); }
  function stop(event: ReactPointerEvent<HTMLDivElement>) { if (drag.current?.pointerId !== event.pointerId) return; drag.current = null; releaseCanvasPointer(viewport.current, event.pointerId); setPanning(false); }
  function lostCapture(event: ReactPointerEvent<HTMLDivElement>) { if (drag.current?.pointerId !== event.pointerId) return; drag.current = null; setPanning(false); }
  useEffect(() => () => { const active = drag.current; drag.current = null; if (active) releaseCanvasPointer(viewport.current, active.pointerId); }, []);
  return <div ref={viewport} onPointerDown={start} onPointerMove={move} onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={lostCapture} className={cx("relative h-full overflow-auto bg-surface-sunken bg-[radial-gradient(#d9e0ea_1px,transparent_1px)] [background-size:20px_20px]", panning ? "cursor-grabbing select-none" : "cursor-grab")}><div className="flex min-h-[860px] min-w-[1700px] items-center justify-center">{children}</div></div>;
}
