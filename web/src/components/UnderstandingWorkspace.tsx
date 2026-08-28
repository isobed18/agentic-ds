import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  api,
  type ArtifactPreview,
  type LocalizedText,
  type RunProgressSnapshot,
  type SourceProfile,
  type StagingWorkspace,
} from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { PlannerPanel } from "./PlannerPanel";
import { sourceCounts, visibleWorkflowSteps } from "./automationWorkspaceState";
import {
  buildStagingRoutingState,
  type ProgressStatus,
  type RoutedSourceFile,
  type RoutingSubstep,
  type StagingRoutingState,
} from "./stagingRoutingState";
import { Badge, Empty, Spinner, cx } from "./ui";
import { GROUPS } from "./GuidedPipeline";
import { CANVAS_BASE_HEIGHT, CANVAS_BASE_WIDTH, CANVAS_MAX_ZOOM, CANVAS_MIN_ZOOM, isZoomGesture, scaledBox, steppedZoom } from "./canvasZoom";
import { ArtifactNodes } from "./ArtifactNodes";

type CanvasSelection = "source" | "discovery" | "structured" | "documents" | "synthesis" | "proposal" | null;

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
      if (["queued", "staging", "running"].includes(String(next.status ?? ""))) {
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

export function SourceSummary({ profile, onStart, busy, reuseCache, onReuseCache }: { profile: SourceProfile; onStart: () => void; busy: boolean; reuseCache: boolean; onReuseCache: (value: boolean) => void }) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const counts = sourceCounts(profile);
  return (
    <CanvasSurface>
      <div className="flex min-w-[570px] items-center gap-10 px-10 py-16">
        <PhaseNode title={t("Uploaded files")} subtitle={t("{count} files", { count: counts.files })} status="complete" onClick={() => setSourceOpen(true)} footer={t("Click to inspect files")} />
        <GraphEdge status="pending" />
        <div className="w-[270px] rounded-2xl border-2 border-dashed border-brand-300 bg-brand-50/80 p-5 text-left shadow-card">
          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Recommended next step")}</p>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{t("Classify and route every file, then understand each source on the right path.")}</p>
          {/* A real button, full width of the card. It used to be bare text with
              no padding, so the hit area was the glyphs of "Run Intake" itself:
              the card looked like the control and swallowed every click that
              missed the word. The card cannot become the button -- it holds a
              checkbox, and nesting interactive elements is invalid -- so the
              button grows to fill it instead. */}
          <button type="button" onClick={onStart} disabled={busy} className="btn-primary mt-3 w-full justify-center text-sm">{busy ? t("Starting…") : t("Run Intake")}</button>
          <label className="mt-4 flex items-start gap-2 border-t border-brand-200 pt-3 text-[10px] leading-relaxed text-ink-mute"><input type="checkbox" className="mt-0.5" checked={reuseCache} onChange={(event) => onReuseCache(event.target.checked)} /><span><strong className="block text-ink">{t("Reuse matching understanding")}</strong>{t("Optional. Turn this off to rerun Intake and Schema Discovery for the same files.")}</span></label>
        </div>
      </div>
      {sourceOpen && <Inspector title={t("Uploaded files")} eyebrow={t("Source")} onClose={() => setSourceOpen(false)}><SourceOverview profile={profile} /></Inspector>}
    </CanvasSurface>
  );
}

export function UnderstandingProgress({ profile, runId, workspace, onRetry }: { profile: SourceProfile; runId: string | null; workspace?: StagingWorkspace | null; onRetry?: () => void }) {
  const progress = useRunProgress(runId);
  const routing = useMemo(() => buildStagingRoutingState(profile, progress, workspace ?? null), [profile, progress, workspace]);
  const [selection, setSelection] = useState<CanvasSelection>(null);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  return (
    <CanvasSurface>
      <RoutingGraph routing={routing} workspace={workspace ?? null} onSelect={setSelection} proposal={routing.proposal === "failed" ? "blocked" : "pending"} onOpenArtifact={(id) => { void api.artifactPreview(id).then(setPreview); }} />
      {routing.error && <div role="alert" className="fixed left-1/2 top-[72px] z-20 w-[min(680px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-stop-300 bg-stop-50 px-4 py-3 shadow-pop"><div className="flex items-start gap-3"><StatusMark status="failed" /><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-stop-700">{t("Staging stopped")}</p><p className="mt-1 break-words text-[11px] leading-relaxed text-stop-700">{routing.error}</p></div><button type="button" className="shrink-0 text-[10px] font-semibold text-stop-700 hover:underline" onClick={() => setSelection(routing.documents.some((step) => step.status === "failed" && step.id !== "explain") ? "documents" : "synthesis")}>{t("Inspect failure")}</button></div></div>}
      {!routing.error && routing.attention && <div role="alert" className="fixed left-1/2 top-[72px] z-20 w-[min(720px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-warn-300 bg-warn-50 px-4 py-3 shadow-pop"><div className="flex items-start gap-3"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-warn-100 text-xs font-bold text-warn-800">!</span><div className="min-w-0 flex-1"><p className="text-xs font-semibold text-warn-800">{t("Understanding needs review")}</p><p className="mt-1 break-words text-[11px] leading-relaxed text-warn-700">{routing.attention}</p></div><button type="button" className="shrink-0 text-[10px] font-semibold text-warn-800 hover:underline" onClick={() => onRetry ? onRetry() : setSelection("structured")}>{t(onRetry ? "Start a new understanding run" : "Inspect")}</button></div></div>}
      {selection && <RoutingInspector selection={selection} profile={profile} workspace={workspace ?? null} routing={routing} onClose={() => setSelection(null)} onOpenArtifact={(id) => { void api.artifactPreview(id).then(setPreview); }} />}
      {preview && <ArtifactDialog preview={preview} onClose={() => setPreview(null)} />}
    </CanvasSurface>
  );
}

export function UnderstandingAndProposal({ profile, workspace, sourceId, runId, onWorkspaceUpdated, onAccept, onAdvanced, busy }: { profile: SourceProfile; workspace: StagingWorkspace; sourceId: string; runId: string; onWorkspaceUpdated: (workspace: StagingWorkspace) => void; onAccept: () => void; onAdvanced: () => void; busy: boolean }) {
  const progress = useRunProgress(runId);
  const routing = useMemo(() => buildStagingRoutingState(profile, progress, workspace), [profile, progress, workspace]);
  const [selection, setSelection] = useState<CanvasSelection>("synthesis");
  const [plannerOpen, setPlannerOpen] = useState(false);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  async function openArtifact(artifactId: string) {
    setPreviewError(null);
    try { setPreview(await api.artifactPreview(artifactId)); }
    catch (caught) { setPreviewError(caught instanceof Error ? caught.message : String(caught)); }
  }

  return (
    <CanvasSurface>
      <RoutingGraph routing={routing} workspace={workspace} onSelect={setSelection} proposal="ready" onOpenArtifact={(id) => void openArtifact(id)} />
      <button type="button" onClick={() => setPlannerOpen(true)} className="fixed bottom-5 left-1/2 z-10 -translate-x-1/2 rounded-xl bg-brand-600 px-5 py-2.5 text-xs font-semibold text-white shadow-pop">{t("Chat with Planner")}</button>
      {selection && <RoutingInspector selection={selection} profile={profile} workspace={workspace} routing={routing} onClose={() => setSelection(null)} onOpenArtifact={openArtifact} onAccept={onAccept} onAdvanced={onAdvanced} busy={busy} />}
      {previewError && <p className="absolute bottom-5 left-5 z-40 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{previewError}</p>}
      {plannerOpen && <div className="absolute inset-y-0 right-0 z-30 flex w-[min(390px,92vw)] border-l border-line bg-surface shadow-2xl"><PlannerPanel runId={runId} sourceId={sourceId} open onToggle={() => setPlannerOpen(false)} onWorkspaceUpdated={onWorkspaceUpdated} starterPrompts={[t("What are these files?"), t("Which relationships are measured?"), t("Are the PDFs contextual evidence?"), t("Stop after EDA so I can inspect it.")]} /></div>}
      {preview && <ArtifactDialog preview={preview} onClose={() => setPreview(null)} />}
    </CanvasSurface>
  );
}

/** The fixed ML pipeline, shown faded while it is still being proposed.
 *
 * Staging used to end at a single "Proposed plan" node and then jump to the
 * editing canvas, which made the pipeline look like something being authored --
 * a different screen, an editor, a blank-ish canvas -- when it is fixed and
 * already known. Showing its real shape here, greyed out, says the opposite:
 * this is what will run, staging is deciding what feeds it, and nothing is
 * waiting on you to draw it.
 *
 * The shape comes from GuidedPipeline's GROUPS rather than a list of its own,
 * so the preview cannot drift from what actually executes.
 */
function ProposedPipelinePreview({ proposal }: { proposal: "pending" | "ready" | "blocked" }) {
  if (proposal === "blocked") return null;
  const pending = proposal === "pending";
  return (
    <div
      aria-label={t("Fixed ML pipeline")}
      aria-busy={pending}
      className={cx("flex w-[210px] shrink-0 flex-col gap-2 rounded-2xl border border-dashed p-3 transition-opacity", pending ? "border-line bg-surface/40 opacity-60" : "border-brand-200 bg-brand-50/40 opacity-90")}
    >
      <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
        {pending && <span className="h-2.5 w-2.5 shrink-0 animate-spin rounded-full border border-brand-400 border-t-transparent" aria-hidden="true" />}
        {pending ? t("Preparing the plan…") : t("Fixed ML pipeline")}
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
        {pending
          ? t("This pipeline is fixed. Staging is deciding which data feeds it.")
          : t("Runs as shown once you accept the plan.")}
      </p>
    </div>
  );
}

function RoutingGraph({ routing, workspace, onSelect, proposal, onOpenArtifact }: { routing: StagingRoutingState; workspace: StagingWorkspace | null; onSelect: (selection: CanvasSelection) => void; proposal: "pending" | "ready" | "blocked"; onOpenArtifact: (id: string) => void }) {
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
      <PhaseNode title={t("Intake")} subtitle={t("Discover, classify, and route")} status={routing.discovery} onClick={() => onSelect("discovery")} artifactIds={measuredIds} onOpenArtifact={onOpenArtifact} compact />
      <ForkConnector branches={branches.length} status={branchStatus} />
      <div className="flex w-[290px] flex-col gap-4">
        {branches.map((branch) => <BranchNode key={branch.id} title={branch.title} files={branch.files} steps={branch.steps} artifactIds={branch.id === "structured" ? [...measuredIds, ...outputIds(["structured-brief"])] : branch.id === "documents" ? [...documentIds, ...outputIds(["document-brief"])] : []} onClick={() => onSelect(branch.id)} onOpenArtifact={onOpenArtifact} />)}
      </div>
      <MergeConnector branches={branches.length} status={routing.synthesis} />
      <PhaseNode title={t("Synthesize")} subtitle={t("Bring findings together")} status={routing.synthesis} onClick={() => onSelect("synthesis")} artifactIds={reportIds} onOpenArtifact={onOpenArtifact} compact />
      <GraphEdge status={proposal === "ready" ? "complete" : proposal === "blocked" ? "failed" : "pending"} />
      <button type="button" onClick={() => onSelect("proposal")} className={cx("w-[190px] rounded-2xl border-2 p-4 text-left shadow-card transition hover:-translate-y-0.5", proposal === "ready" ? "border-brand-300 bg-brand-50/80 hover:border-brand-500" : proposal === "blocked" ? "border-stop-300 bg-stop-50" : "border-dashed border-line bg-surface/80")}>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-600">{t("Proposed")}</p>
        <p className="mt-2 text-sm font-semibold text-ink">{t("Proposed plan")}</p>
        <p className="mt-1 text-[10px] text-ink-mute">{proposal === "ready" ? t("Ready for review") : proposal === "blocked" ? t("Blocked — no plan was created") : t("Created after synthesis")}</p>
      </button>
      {proposal !== "blocked" && <GraphEdge status={proposal === "ready" ? "complete" : "pending"} />}
      <ProposedPipelinePreview proposal={proposal} />
    </div>
  );
}

function BranchNode({ title, files, steps, artifactIds, onClick, onOpenArtifact }: { title: string; files: RoutedSourceFile[]; steps: RoutingSubstep[]; artifactIds: string[]; onClick: () => void; onOpenArtifact: (id: string) => void }) {
  const status: ProgressStatus = steps.some((step) => step.status === "failed") ? "failed" : steps.some((step) => step.status === "running") ? "running" : steps.every((step) => step.status === "complete") ? "complete" : "pending";
  const secondary = steps.find((step) => step.detail)?.detail;
  return <div className="relative"><button type="button" onClick={onClick} className={cx("w-full rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand-300", status === "running" && "border-brand-400 ring-4 ring-brand-50", status === "failed" && "border-stop-300")}>
    <div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-ink">{title}</p><p className="mt-0.5 text-[10px] text-ink-mute">{t("{count} files", { count: files.length })}</p></div><StatusMark status={status} /></div>
    <div className="mt-3 flex flex-wrap gap-1">{files.slice(0, 3).map((file) => <span key={file.name} title={file.name} className="max-w-[120px] truncate rounded-md bg-surface-sunken px-2 py-1 text-[9px] font-medium text-ink-mute">{file.name}</span>)}{files.length > 3 && <span className="rounded-md bg-surface-sunken px-2 py-1 text-[9px] text-ink-faint">+{files.length - 3}</span>}</div>
    {secondary && <p className="mt-2 text-[9px] font-medium text-ink-mute">{secondary}</p>}
    <ol className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5">{steps.map((step) => <li key={step.id} className={cx("min-w-0 text-[10px]", step.status === "running" ? "font-semibold text-brand-700" : step.status === "complete" ? "text-ok-700" : step.status === "failed" ? "text-stop-700" : "text-ink-faint")}><span className="mr-1">{step.status === "complete" ? "✓" : step.status === "running" ? "●" : step.status === "failed" ? "!" : "○"}</span>{t(step.label)}</li>)}</ol>
  </button><ArtifactNodes ids={artifactIds} onOpen={onOpenArtifact} /></div>;
}

function RoutingInspector({ selection, profile, workspace, routing, onClose, onOpenArtifact, onAccept, onAdvanced, busy = false }: { selection: Exclude<CanvasSelection, null>; profile: SourceProfile; workspace: StagingWorkspace | null; routing: StagingRoutingState; onClose: () => void; onOpenArtifact: (id: string) => void; onAccept?: () => void; onAdvanced?: () => void; busy?: boolean }) {
  const titles: Record<Exclude<CanvasSelection, null>, string> = {
    source: t("Uploaded files"), discovery: t("Intake and source routing"), structured: t("Structured data"), documents: t("Understand documents"), synthesis: t("Cross-source synthesis"), proposal: t("Proposed plan"),
  };
  return <Inspector title={titles[selection]} eyebrow={selection === "synthesis" ? t("Evidence and interpretation") : t("Staging")} onClose={onClose}>
    {selection === "source" && <SourceOverview profile={profile} />}
    {selection === "discovery" && <RoutingDetails files={routing.files} />}
    {selection === "structured" && <StructuredDetails profile={profile} workspace={workspace} routing={routing} />}
    {selection === "documents" && <DocumentDetails workspace={workspace} routing={routing} onOpenArtifact={onOpenArtifact} />}
    {selection === "synthesis" && (workspace?.planner_error ? <div className="rounded-xl border border-stop-200 bg-stop-50 p-4"><p className="text-xs font-semibold text-stop-700">{t("Planner synthesis failed")}</p><p className="mt-2 break-words text-[11px] leading-relaxed text-stop-700">{workspace.planner_error}</p></div> : workspace ? <UnderstandingResults profile={profile} workspace={workspace} onOpenArtifact={onOpenArtifact} /> : <ProgressList steps={[...routing.structured, ...routing.documents]} />)}
    {selection === "proposal" && (workspace && onAccept && onAdvanced ? <PlanProposal profile={profile} workspace={workspace} onAccept={onAccept} onAdvanced={onAdvanced} busy={busy} /> : <Empty title={t("Plan not ready yet")} hint={t("The proposal appears after structured and document findings are synthesized.")} />)}
  </Inspector>;
}

function RoutingDetails({ files }: { files: RoutedSourceFile[] }) {
  return <div className="space-y-2">{files.map((file) => <div key={`${file.route}:${file.name}`} className="rounded-xl border border-line bg-surface px-3 py-3"><div className="flex items-center gap-2"><FileBadge format={file.format} /><p className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{file.name}</p><span className={cx("rounded-full px-2 py-1 text-[9px] font-semibold", file.route === "structured" ? "bg-ok-50 text-ok-700" : file.route === "documents" ? "bg-brand-50 text-brand-700" : "bg-warn-50 text-warn-700")}>{t(file.route === "structured" ? "Structured data" : file.route === "documents" ? "Documents" : "Needs review")}</span></div>{file.reason && <p className="mt-2 text-[10px] leading-relaxed text-ink-mute">{file.reason}</p>}{file.tableNames.length > 0 && <p className="mt-1 text-[9px] text-ink-faint">{t("Tables")}: {file.tableNames.join(", ")}</p>}</div>)}</div>;
}

function StructuredDetails({ profile, workspace, routing }: { profile: SourceProfile; workspace: StagingWorkspace | null; routing: StagingRoutingState }) {
  const files = routing.files.filter((file) => file.route === "structured");
  return <div className="space-y-5"><ProgressList steps={routing.structured} /><EvidenceSection title={t("Measured source facts")} tone="measured"><RoutingDetails files={files} /><div className="mt-3 grid grid-cols-2 gap-2"><Metric label={t("Tables")} value={profile.tables.length} /><Metric label={t("Structured rows")} value={profile.tables.reduce((sum, table) => sum + table.rows, 0).toLocaleString()} /></div></EvidenceSection>{workspace && <EvidenceSection title={t("Measured relationships")} tone="measured"><RelationshipList workspace={workspace} /></EvidenceSection>}</div>;
}

function DocumentDetails({ workspace, routing, onOpenArtifact }: { workspace: StagingWorkspace | null; routing: StagingRoutingState; onOpenArtifact: (id: string) => void }) {
  const extraction = workspace?.document_extractions?.at(-1);
  const processedPages = routing.documentFiles.filter((file) => file.status === "ready").reduce((sum, file) => sum + (file.pageCount ?? 0), 0);
  return <div className="space-y-5"><ProgressList steps={routing.documents} /><section className="rounded-xl border border-line p-4"><div className="grid grid-cols-2 gap-2"><Detail label={t("Selected engine")} value={`${routing.engine}${routing.engineVersion ? ` ${routing.engineVersion}` : ""}`} /><Detail label={t("OCR mode")} value={routing.ocrMode} /><Detail label={t("Processed pages")} value={processedPages} /><Detail label={t("Duration")} value={extraction ? formatDuration(extraction.duration_seconds) : t("In progress")} /><Detail label={t("Table candidates")} value={extraction?.table_candidates ?? 0} /><Detail label={t("Figure candidates")} value={extraction?.figure_candidates ?? 0} /></div></section><section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Per-file status")}</p><div className="mt-2 space-y-2">{routing.documentFiles.map((file) => <div key={file.sourceFile} className="rounded-lg border border-line bg-surface px-3 py-3"><div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">{file.sourceFile}</span><Badge tone={file.status === "ready" ? "ok" : file.status === "failed" ? "stop" : "brand"}>{t(file.status === "queued" ? "Waiting" : file.status === "running" ? "Processing" : file.status === "ready" ? "Complete" : "Failed")}</Badge></div>{file.status === "ready" && <p className="mt-2 text-[10px] text-ink-mute">{t("{pages} pages · {tables} tables · {figures} figures", { pages: file.pageCount ?? 0, tables: file.tableCandidates ?? 0, figures: file.figureCandidates ?? 0 })}{file.durationSeconds !== undefined ? ` · ${formatDuration(file.durationSeconds)}` : ""}</p>}{file.warnings.map((warning) => <p key={warning} className="mt-2 text-[10px] text-warn-700">{warning}</p>)}</div>)}</div></section>{(extraction?.table_candidates ?? 0) > 0 && <div className="rounded-xl border border-warn-200 bg-warn-50 px-3 py-3"><p className="text-xs font-semibold text-warn-800">{t("Candidate — not trusted structured data")}</p><p className="mt-1 text-[10px] text-warn-700">{t("Review and promote each extracted table before it can enter training data.")}</p></div>}{extraction?.warnings.map((warning) => <p key={warning} className="rounded-lg bg-warn-50 px-3 py-2 text-[10px] text-warn-700">{warning}</p>)}{extraction?.artifact_id && <button type="button" className="btn-primary w-full text-xs" onClick={() => onOpenArtifact(extraction.artifact_id!)}>{t("Open produced artifacts")}</button>}</div>;
}

function UnderstandingResults({ profile, workspace, onOpenArtifact }: { profile: SourceProfile; workspace: StagingWorkspace; onOpenArtifact: (artifactId: string) => void }) {
  const counts = sourceCounts(profile);
  const extraction = workspace.document_extractions?.at(-1);
  const reportIds = useMemo(() => (workspace.component_outputs ?? []).filter((output) => output.data_type === "reports").flatMap((output) => output.artifact_ids), [workspace.component_outputs]);
  const insights = workspace.reports.flatMap((report) => report.findings).slice(0, 3);
  const warnings = [
    ...(extraction?.warnings ?? []),
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
    {workspace.reports.length ? <details className="group rounded-xl border border-violet-200 bg-violet-50/40"><summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 text-xs font-semibold text-violet-800"><span>▣</span><span className="flex-1">{t("Agent reports")}</span><Badge tone="neutral">{workspace.reports.length}</Badge><span className="transition group-open:rotate-180">⌄</span></summary><div className="space-y-3 border-t border-violet-200 p-4">{workspace.reports.map((report, index) => <article key={`${report.title.en}:${index}`} className="rounded-lg border border-violet-200 bg-surface px-3 py-3"><p className="text-xs font-semibold text-ink">{local(report.title)}</p><p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{local(report.summary)}</p>{report.findings.length > 0 && <ul className="mt-3 space-y-1">{report.findings.map((finding) => <li key={finding.en} className="text-[10px] leading-relaxed text-ink-mute">• {local(finding)}</li>)}</ul>}<button type="button" disabled={!reportIds[index]} onClick={() => reportIds[index] && onOpenArtifact(reportIds[index])} className="mt-3 text-[10px] font-semibold text-violet-700 disabled:text-ink-faint">{reportIds[index] ? t("Open report artifact") : t("Summary only")}</button></article>)}</div></details> : <Spinner label={t("The Planner is preparing an explanation…")} />}
  </div>;
}

function SignalLine({ symbol, tone, text }: { symbol: string; tone: "insight" | "warning" | "ok"; text: string }) {
  return <div className={cx("flex items-start gap-2 rounded-lg border px-3 py-2", tone === "warning" ? "border-warn-200 bg-warn-50 text-warn-800" : tone === "ok" ? "border-ok-200 bg-ok-50 text-ok-800" : "border-violet-200 bg-violet-50 text-violet-800")}><span className="grid h-4 w-4 shrink-0 place-items-center text-[10px] font-bold">{symbol}</span><p className="line-clamp-2 text-[10px] leading-relaxed">{text}</p></div>;
}

function RelationshipList({ workspace }: { workspace: StagingWorkspace }) {
  return workspace.relationship_explanations.length ? <ul className="space-y-2">{workspace.relationship_explanations.map((relationship) => <li key={`${relationship.from_table}:${relationship.from_columns.join(",")}:${relationship.to_table}:${relationship.to_columns.join(",")}`} className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-xs font-semibold text-ink">{relationship.from_table}.{relationship.from_columns.join(", ")} → {relationship.to_table}.{relationship.to_columns.join(", ")}</p><p className="mt-1 text-[11px] text-ink-mute">{t("{coverage}% measured coverage · {cardinality}", { coverage: (relationship.overlap_rate * 100).toFixed(1), cardinality: relationship.cardinality })}</p><p className="mt-1 text-[10px] text-warn-700">{t("{orphan}% unmatched rows", { orphan: (relationship.orphan_rate * 100).toFixed(1) })}</p></li>)}</ul> : <Empty title={t("No measured cross-table relationship")} hint={t("The system will not invent a join from similar names alone.")} />;
}

function SourceOverview({ profile }: { profile: SourceProfile }) {
  const counts = sourceCounts(profile);
  return <div><div className="grid grid-cols-2 gap-2"><Metric label={t("PDF documents")} value={counts.pdfs} /><Metric label={t("Structured files")} value={counts.structured} /><Metric label={t("Document pages")} value={counts.pages} /><Metric label={t("Structured rows")} value={counts.rows.toLocaleString()} /></div><p className="mt-5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Files provided")}</p><SourceFiles profile={profile} /></div>;
}

function PlanProposal({ profile, workspace, onAccept, onAdvanced, busy }: { profile: SourceProfile; workspace: StagingWorkspace; onAccept: () => void; onAdvanced: () => void; busy: boolean }) {
  const plan = workspace.recommended_plan;
  if (!plan) return <Spinner label={t("The Planner is preparing a proposal…")} />;
  const recommendation = plan.pipeline_recommendation ?? "create_pipeline";
  if (recommendation !== "create_pipeline") {
    const denied = recommendation === "no_pipeline";
    return <div><Badge tone={denied ? "stop" : "warn"}>{t(denied ? "No ML pipeline recommended" : "Pipeline decision deferred")}</Badge><h3 className="mt-3 text-base font-semibold text-ink">{t(denied ? "Understanding complete — stop before ML" : "More evidence is needed before ML")}</h3><p className="mt-2 text-xs leading-relaxed text-ink-mute">{plan.decision_summary ? local(plan.decision_summary) : t("The Planner did not recommend an executable pipeline from the available evidence.")}</p>{plan.rationale.length > 0 && <div className="mt-5 rounded-lg bg-violet-50 px-3 py-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Agent rationale")}</p><ul className="mt-2 space-y-1">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></div>}<div className="mt-5 border-t border-line pt-4"><button type="button" className="btn-ghost" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button></div><p className="mt-3 text-[10px] text-ink-faint">{t("No pipeline will run unless a human explicitly overrides this recommendation.")}</p></div>;
  }
  const steps = visibleWorkflowSteps(workspace);
  const target = String(plan.configuration.target_column ?? "");
  const goal = String(plan.configuration.problem_title ?? (target ? t("Model {target}", { target }) : t("Analyze and explain the available evidence")));
  const structuredFiles = (profile.source_files ?? []).filter((file) => file.route === "structured").map((file) => file.name);
  const documentFiles = (profile.source_files ?? []).filter((file) => file.route === "documents").map((file) => file.name);
  const baseTable = String(plan.configuration.base_table ?? profile.tables[0]?.name ?? "—");
  const scope = plan.checkpoint_stages.length ? t("Runs with {count} planned review checkpoints.", { count: plan.checkpoint_stages.length }) : t("Runs through the final report; hard safety gates still apply.");
  return <div><Badge tone="brand">{t("Proposed — not executable yet")}</Badge><h3 className="mt-3 text-base font-semibold text-ink">{goal}</h3><p className="mt-1 text-xs text-ink-mute">{t("Review what enters ML and where the base pipeline will stop before accepting.")}</p><section className="mt-5 rounded-xl border border-ok-200 bg-ok-50/50 p-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-ok-700">{t("Enters ML")}</p><p className="mt-2 text-xs font-semibold text-ink">{baseTable}</p><div className="mt-2 flex flex-wrap gap-1">{structuredFiles.map((file) => <Badge key={file} tone="ok">{file}</Badge>)}</div>{!structuredFiles.length && <p className="mt-2 text-[10px] text-warn-700">{t("No trusted structured ML input is selected.")}</p>}</section>{documentFiles.length > 0 && <section className="mt-3 rounded-xl border border-violet-200 bg-violet-50/40 p-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Context only")}</p><div className="mt-2 flex flex-wrap gap-1">{documentFiles.map((file) => <Badge key={file}>{file}</Badge>)}</div><p className="mt-2 text-[10px] text-ink-mute">{t("Extracted PDF tables stay review-only until a human promotes them.")}</p></section>}<section className="mt-5"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Recommended continuation")}</p><ol className="mt-3 space-y-2">{steps.map((step, index) => <li key={`${step}:${index}`} className="flex gap-2 text-xs text-ink-soft"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-50 text-[10px] font-semibold text-brand-700">{index + 1}</span><span className="pt-0.5">{step}</span></li>)}</ol><p className="mt-3 rounded-lg bg-surface-sunken px-3 py-2 text-[10px] text-ink-mute">{scope}</p></section>{plan.rationale.length > 0 && <div className="mt-5 rounded-lg bg-violet-50 px-3 py-3"><p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">{t("Agent rationale")}</p><ul className="mt-2 space-y-1">{plan.rationale.map((reason) => <li key={reason.en} className="text-[11px] leading-relaxed text-ink-mute">{local(reason)}</li>)}</ul></div>}<div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4"><button type="button" className="btn-primary" onClick={onAccept} disabled={busy}>{busy ? t("Accepting…") : t("Accept and add base pipeline")}</button><button type="button" className="btn-ghost" onClick={onAdvanced}>{t("Advanced editor · Experimental")}</button></div><p className="mt-3 text-[10px] text-ink-faint">{t("You can also ask the Planner to revise this proposal.")}</p></div>;
}

function ProgressList({ steps }: { steps: RoutingSubstep[] }) { return <ol className="space-y-2">{steps.map((step) => <li key={`${step.id}:${step.label}`} className={cx("flex items-start gap-2 rounded-lg px-3 py-2 text-xs", step.status === "running" ? "bg-brand-50 font-semibold text-brand-700" : step.status === "complete" ? "text-ok-700" : step.status === "failed" ? "bg-stop-50 text-stop-700" : "text-ink-faint")}><StatusMark status={step.status} /><span><span>{t(step.label)}</span>{step.detail && <span className="mt-0.5 block text-[10px] font-normal text-ink-mute">{step.detail}</span>}</span></li>)}</ol>; }
function CanvasSurface({ children }: { children: React.ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const [panning, setPanning] = useState(false);
  const [zoom, setZoom] = useState(1);

  // Attached by hand rather than through onWheel, because React's wheel handler
  // is passive and cannot call preventDefault -- without which ctrl+scroll
  // zooms the whole browser page instead of the graph.
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    function onWheel(event: WheelEvent) {
      // Plain scroll and two-finger trackpad panning are left alone; only the
      // browser's own zoom gesture is taken over.
      if (!isZoomGesture(event)) return;
      event.preventDefault();
      setZoom((current) => steppedZoom(current, event.deltaY < 0 ? 1 : -1));
    }
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);

  function startPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button,input,textarea,select,a,[role='dialog']")) return;
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
    if (node?.hasPointerCapture(event.pointerId)) node.releasePointerCapture(event.pointerId);
    setPanning(false);
  }

  return <div ref={viewport} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={stopPan} onPointerCancel={stopPan} className={cx("relative h-full min-h-[30rem] overflow-auto bg-surface-sunken bg-[radial-gradient(#d9e0ea_1px,transparent_1px)] [background-size:20px_20px]", panning ? "cursor-grabbing select-none" : "cursor-grab")}>
    <div style={scaledBox(zoom)}>
      <div className="flex items-center justify-center" style={{ width: CANVAS_BASE_WIDTH, height: CANVAS_BASE_HEIGHT, transform: `scale(${zoom})`, transformOrigin: "top left" }}>{children}</div>
    </div>
    <div className="sticky bottom-3 left-0 z-10 flex justify-end px-3">
      <div className="flex items-center gap-1 rounded-xl border border-line bg-surface/95 p-1 shadow-card backdrop-blur">
        <ZoomControl label={t("Zoom out")} disabled={zoom <= CANVAS_MIN_ZOOM} onClick={() => setZoom((current) => steppedZoom(current, -1))}>−</ZoomControl>
        <button type="button" onClick={() => setZoom(1)} disabled={zoom === 1} title={t("Reset zoom")} className="min-w-[3rem] rounded-lg px-2 py-1 text-[10px] font-semibold tabular-nums text-ink-mute transition hover:bg-surface-sunken disabled:opacity-40">{Math.round(zoom * 100)}%</button>
        <ZoomControl label={t("Zoom in")} disabled={zoom >= CANVAS_MAX_ZOOM} onClick={() => setZoom((current) => steppedZoom(current, 1))}>+</ZoomControl>
      </div>
    </div>
  </div>;
}

function ZoomControl({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} disabled={disabled} title={label} aria-label={label} className="grid h-7 w-7 place-items-center rounded-lg text-sm font-semibold text-ink-soft transition hover:bg-surface-sunken disabled:opacity-40">{children}</button>;
}
function GraphEdge({ status }: { status: ProgressStatus }) { return <div className={cx("relative h-px w-10 shrink-0", status === "complete" ? "bg-ok-300" : status === "failed" ? "bg-stop-300" : "bg-slate-300")}><span className="absolute -right-1 -top-[3px] h-2 w-2 rotate-45 border-r border-t border-slate-400" />{status === "running" && <span className="absolute inset-y-[-1px] left-0 w-5 animate-pulse rounded-full bg-brand-400 motion-reduce:animate-none" />}</div>; }
function ForkConnector({ branches, status }: { branches: number; status: ProgressStatus }) { const height = Math.max(40, (branches - 1) * 178); return <svg aria-hidden="true" className="w-12 shrink-0" style={{ height }} viewBox={`0 0 48 ${height}`} preserveAspectRatio="none"><path d={`M0 ${height / 2} H20 M20 ${height / 2} V8 M20 ${height / 2} V${height - 8} M20 8 H48 M20 ${height - 8} H48`} fill="none" stroke={status === "complete" ? "#86c99a" : "#cbd5e1"} strokeWidth="1.5" /></svg>; }
function MergeConnector({ branches, status }: { branches: number; status: ProgressStatus }) { const height = Math.max(40, (branches - 1) * 178); return <svg aria-hidden="true" className="w-12 shrink-0" style={{ height }} viewBox={`0 0 48 ${height}`} preserveAspectRatio="none"><path d={`M0 8 H28 M0 ${height - 8} H28 M28 8 V${height - 8} M28 ${height / 2} H48`} fill="none" stroke={status === "complete" ? "#86c99a" : status === "running" ? "#4f7cff" : "#cbd5e1"} strokeWidth="1.5" /><path d={`M43 ${height / 2 - 4} L48 ${height / 2} L43 ${height / 2 + 4}`} fill="none" stroke="#94a3b8" strokeWidth="1.5" /></svg>; }
function StatusMark({ status }: { status: ProgressStatus }) { return <span className={cx("relative grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[9px]", status === "complete" ? "border-ok-300 bg-ok-50 text-ok-700" : status === "running" ? "border-brand-400 bg-brand-50 text-brand-700" : status === "failed" ? "border-stop-300 bg-stop-50 text-stop-700" : "border-line bg-surface text-ink-faint")}>{status === "complete" ? "✓" : status === "running" ? <><span>●</span><span className="absolute inset-0 animate-ping rounded-full bg-brand-300 opacity-40 motion-reduce:animate-none" /></> : status === "failed" ? "!" : "○"}</span>; }
function PhaseNode({ title, subtitle, footer, status, onClick, artifactIds = [], onOpenArtifact, compact = false }: { title: string; subtitle: string; footer?: string; status: ProgressStatus; onClick?: () => void; artifactIds?: string[]; onOpenArtifact?: (id: string) => void; compact?: boolean }) { const content = <><div className="flex items-start justify-between gap-3"><StatusMark status={status} /><Badge tone={status === "complete" ? "ok" : status === "failed" ? "stop" : status === "running" ? "brand" : "neutral"}>{t(status === "complete" ? "Complete" : status === "running" ? "Processing" : status === "failed" ? "Failed" : "Waiting")}</Badge></div><p className="mt-3 text-sm font-semibold text-ink">{title}</p><p className="mt-1 text-[11px] text-ink-mute">{subtitle}</p>{footer && <p className="mt-3 text-[10px] font-medium text-brand-700">{footer}</p>}</>; const className = cx(compact ? "w-[190px] p-4" : "w-[230px] p-5", "rounded-2xl border bg-surface text-left shadow-card transition", status === "running" ? "border-brand-400 ring-4 ring-brand-50" : status === "failed" ? "border-stop-300" : "border-line", onClick && "hover:-translate-y-0.5 hover:border-brand-300"); const card = onClick ? <button type="button" onClick={onClick} className={className}>{content}</button> : <article className={className}>{content}</article>; return <div className="relative shrink-0">{card}<ArtifactNodes ids={artifactIds} onOpen={onOpenArtifact ?? (() => {})} /></div>; }
function Inspector({ title, eyebrow, onClose, children }: { title: string; eyebrow: string; onClose: () => void; children: React.ReactNode }) { return <aside className="fixed inset-y-[58px] right-0 z-20 w-[min(440px,94vw)] overflow-y-auto border-l border-line bg-surface p-5 shadow-2xl"><header className="flex items-start gap-3 border-b border-line pb-4"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand-600">{eyebrow}</p><h2 className="mt-1 text-base font-semibold text-ink">{title}</h2></div><button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close inspector")} onClick={onClose}>×</button></header><div className="mt-5">{children}</div></aside>; }
function EvidenceSection({ title, tone, children }: { title: string; tone: "measured" | "interpretation"; children: React.ReactNode }) { return <section className={cx("rounded-xl border p-4", tone === "measured" ? "border-sky-200 bg-sky-50/40" : "border-violet-200 bg-violet-50/40")}><p className={cx("text-[10px] font-semibold uppercase tracking-[0.12em]", tone === "measured" ? "text-sky-700" : "text-violet-700")}>{title}</p><div className="mt-3">{children}</div></section>; }
function SourceFiles({ profile, compact = false }: { profile: SourceProfile; compact?: boolean }) { const files = buildStagingRoutingState(profile, null, null).files; return <div className={compact ? "mt-2 space-y-1.5" : "mt-3 space-y-2"}>{files.map((file) => <div key={`${file.route}:${file.name}`} className="flex items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2"><FileBadge format={file.format} /><span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">{file.name}</span><span className="text-[10px] text-ink-mute">{t(file.route === "structured" ? "Structured" : file.route === "documents" ? "Document" : "Needs review")}</span></div>)}</div>; }
function FileBadge({ format }: { format: string }) { return <span className="rounded bg-surface-sunken px-2 py-0.5 text-[9px] font-semibold uppercase text-ink-mute">{format}</span>; }
function Metric({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg border border-line bg-surface px-3 py-2"><p className="text-lg font-semibold text-ink">{value}</p><p className="mt-0.5 text-[10px] text-ink-mute">{label}</p></div>; }
function Detail({ label, value }: { label: string; value: string | number }) { return <div className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-[9px] uppercase tracking-wide text-ink-faint">{label}</p><p className="mt-1 truncate text-xs font-semibold text-ink">{value}</p></div>; }
function formatDuration(seconds: number): string { return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(1)} s`; }

function ArtifactDialog({ preview, onClose }: { preview: ArtifactPreview; onClose: () => void }) {
  const isDocument = preview.artifact_type === "document_extraction";
  return <div className="fixed inset-0 z-50 grid place-items-center bg-ink/30 p-4" role="dialog" aria-modal="true"><div className="max-h-[86vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-surface p-5 shadow-2xl"><div className="flex items-start gap-4"><div className="min-w-0 flex-1"><p className="text-[10px] font-semibold uppercase tracking-wide text-brand-600">{String(preview.artifact_type).replaceAll("_", " ")}</p><h3 className="mt-1 text-lg font-semibold text-ink">{preview.title ? local(preview.title) : isDocument ? t("Extracted document artifacts") : t("Artifact details")}</h3></div><button type="button" className="btn-ghost !px-2 !py-1" aria-label={t("Close artifact")} onClick={onClose}>×</button></div>{preview.summary && <p className="mt-3 text-sm leading-relaxed text-ink-mute">{local(preview.summary)}</p>}{isDocument && <><div className="mt-4 grid grid-cols-3 gap-2"><Detail label={t("Selected engine")} value={`${preview.engine ?? "—"}${preview.engine_version ? ` ${preview.engine_version}` : ""}`} /><Detail label={t("OCR mode")} value={String(preview.ocr_mode ?? "auto")} /><Detail label={t("Duration")} value={formatDuration(Number(preview.duration_seconds ?? 0))} /></div><div className="mt-4 space-y-3">{preview.documents?.map((document) => <section key={document.source_file} className="rounded-xl border border-line p-4"><div className="flex items-center justify-between gap-3"><p className="truncate text-sm font-semibold text-ink">{document.source_file}</p><span className="text-[10px] text-ink-mute">{document.page_count} {t("pages")}</span></div><p className="mt-2 text-[10px] text-ink-mute">{document.tables.length} {t("table candidates")} · {document.figures.length} {t("figure candidates")}</p>{document.tables.map((table) => <div key={String(table.candidate_id)} className="mt-3 rounded-lg border border-warn-200 bg-warn-50 px-3 py-2"><p className="text-[10px] font-semibold text-warn-800">{t("Candidate — not trusted structured data")}</p><p className="mt-1 text-[10px] text-warn-700">{String(table.title ?? table.candidate_id)} · {t("page {page}", { page: String(table.page_number ?? "—") })}</p></div>)}</section>)}</div></>}{preview.findings && <ul className="mt-4 space-y-2">{preview.findings.map((finding) => <li key={finding.en} className="rounded-lg bg-surface-sunken px-3 py-2 text-xs text-ink-soft">{local(finding)}</li>)}</ul>}</div></div>;
}
