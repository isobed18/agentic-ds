import type { LocalizedText, SourceProfile, StagingWorkspace } from "../lib/api";
import { runErrorText } from "./stageFailure";

export type RouteKind = "structured" | "documents" | "unsupported";
export type ProgressStatus = "complete" | "running" | "pending" | "failed";

export interface RoutedSourceFile {
  name: string;
  format: string;
  route: RouteKind;
  reason?: LocalizedText;
  tableNames: string[];
  // What ads.file_detection measured from the file's content. The route above still
  // comes from the extension, so these two can disagree -- and when they do,
  // that disagreement is the most useful thing on the screen.
  measuredFlow?: string;
  measuredDeterministic?: boolean;
  measuredEvidence?: string;
  needsDecisionBecause?: string;
  contradictsExtension?: boolean;
}

export interface FilePage {
  shown: RoutedSourceFile[];
  total: number;
  /** The requested page, clamped into [1, lastPage]. */
  page: number;
  lastPage: number;
}

/**
 * One page of an in-memory routed-file list (#98).
 *
 * The "Yapısal veri" panel rendered every routed file in one flat, unbounded
 * list; a sharded upload (hundreds of 00000.csv-style files) made it a single
 * unscrollable column with no way to find one file. This filters by file name,
 * then slices to the page -- the same search + page-size + paging design as the
 * /datasets catalog (#72). The requested page is clamped, so when a search
 * shrinks the list below the current page the view falls back to the last real
 * page instead of showing an empty page past the end.
 */
export function pageOfFiles(
  files: RoutedSourceFile[],
  options: { search: string; page: number; pageSize: number },
): FilePage {
  const query = options.search.trim().toLowerCase();
  const filtered = query
    ? files.filter((file) => file.name.toLowerCase().includes(query))
    : files;
  const total = filtered.length;
  const lastPage = Math.max(1, Math.ceil(total / options.pageSize));
  const page = Math.min(Math.max(1, options.page), lastPage);
  const start = (page - 1) * options.pageSize;
  return { shown: filtered.slice(start, start + options.pageSize), total, page, lastPage };
}

export interface RoutingSubstep {
  id: string;
  label: string;
  status: ProgressStatus;
  detail?: string;
}

export interface DocumentFileProgress {
  sourceFile: string;
  status: "queued" | "running" | "ready" | "failed";
  pageCount?: number;
  tableCandidates?: number;
  figureCandidates?: number;
  durationSeconds?: number;
  /**
   * Both languages, because the server composes these around measured values
   * during a background stage and cannot know the reader. The view picks one.
   */
  warnings: LocalizedText[];
}

export interface StagingRoutingState {
  files: RoutedSourceFile[];
  discovery: ProgressStatus;
  structured: RoutingSubstep[];
  documents: RoutingSubstep[];
  synthesis: ProgressStatus;
  proposal: ProgressStatus;
  engine: string;
  engineVersion?: string | null;
  ocrMode: string;
  documentFiles: DocumentFileProgress[];
  error?: string;
  /** #365: why understanding ended with nothing to accept, when it did. */
  outcome?: StagingOutcome;
  // #362: `attention` used to carry the escalated gate's own prose so the
  // canvas could show it in a yellow banner. The red ApprovalCard already
  // renders that text with its reason list and its action buttons, so the
  // banner said the same thing twice and could not answer it. Both are gone;
  // `schemaBlocked` still drives the node statuses below.
}

/**
 * How understanding ended when it ended without a plan to accept (#365).
 *
 * The canvas had exactly two states for the proposal node -- pending and
 * failed -- so every way of finishing without a proposal rendered as "still
 * working". A file was accepted, nothing went red, and the flow simply never
 * advanced; the reader had no way to tell a slow run from one that had already
 * stopped, and reloading started the same wait again.
 *
 * Two of these are ordinary decisions rather than faults. `defer_pipeline` and
 * `no_pipeline` are answers the planner is explicitly asked to consider, and it
 * records its reason in `decision_summary` -- which nothing rendered, so the
 * one sentence explaining the stop was computed and thrown away on every run.
 *
 * `no_plan` is the honest catch-all: understanding settled and produced no
 * proposal at all, and we cannot say why from here. Naming it is still strictly
 * better than a pending spinner, because it is the difference between "wait
 * longer" and "this is finished, and it did not work".
 */
export interface StagingOutcome {
  kind: "deferred" | "declined" | "no_plan";
  summary?: LocalizedText;
  rationale: LocalizedText[];
}

export interface StagingProgressSnapshot {
  status?: string;
  current_stage?: string | null;
  // #365: a run error has been a bilingual `{en, tr}` object since #263/#265,
  // and this read still narrowed on `typeof === "string"`. Every failure after
  // that change fell through the check, so the canvas's red "Staging stopped"
  // banner stopped rendering entirely -- a run died and the screen said
  // nothing. `runErrorText` reads both shapes.
  error?: unknown;
  events?: Array<Record<string, unknown>>;
  attempts?: Array<Record<string, unknown>>;
  pending_question?: {
    stage_id?: string;
    human_prompt?: {
      question?: string;
      context_summary?: string;
    } | null;
  } | null;
}

export function routedFiles(profile: SourceProfile): RoutedSourceFile[] {
  if (profile.source_files?.length) {
    return profile.source_files.map((file) => ({
      name: file.name,
      format: file.format,
      route: file.route,
      reason: file.reason,
      tableNames: file.table_names ?? [],
      measuredFlow: file.detected_flow,
      measuredDeterministic: file.detection_deterministic,
      measuredEvidence: file.detection_evidence,
      needsDecisionBecause: file.detection_reason,
      contradictsExtension: file.detection_conflicts_with_extension,
    }));
  }
  const structured = profile.tables.map((table) => ({
    name: table.source_file ?? table.name,
    format: table.format,
    route: "structured" as const,
    tableNames: [table.name],
  }));
  const documents = (profile.documents ?? []).map((document) => ({
    name: document.name,
    format: document.format,
    route: "documents" as const,
    tableNames: [],
  }));
  return uniqueFiles([...structured, ...documents]);
}

function uniqueFiles(files: RoutedSourceFile[]): RoutedSourceFile[] {
  const byRouteAndName = new Map<string, RoutedSourceFile>();
  for (const file of files) {
    const key = `${file.route}:${file.name}`;
    const previous = byRouteAndName.get(key);
    byRouteAndName.set(key, previous ? {
      ...previous,
      tableNames: [...new Set([...previous.tableNames, ...file.tableNames])],
    } : file);
  }
  return [...byRouteAndName.values()];
}

function statusAfter(done: boolean, running: boolean, failed = false): ProgressStatus {
  if (failed) return "failed";
  if (done) return "complete";
  return running ? "running" : "pending";
}

export function buildStagingRoutingState(
  profile: SourceProfile,
  progress: StagingProgressSnapshot | null,
  workspace: StagingWorkspace | null,
): StagingRoutingState {
  const files = routedFiles(profile);
  const events = progress?.events ?? [];
  const eventNames = new Set(events.map((event) => String(event.event ?? "")));
  const successfulGateStages = new Set(
    events
      .filter((event) => event.event === "gate_decided"
        && (!event.verdict || event.verdict === "auto_proceed"))
      .map((event) => String(event.stage ?? "")),
  );
  const discoveryReady = eventNames.has("source_discovery_ready");
  const intakeReady = successfulGateStages.has("intake");
  const relationshipsReady = successfulGateStages.has("schema_discovery");
  const documentReady = eventNames.has("document_understanding_ready");
  const documentFailed = eventNames.has("document_understanding_failed");
  const plannerFailed = Boolean(workspace?.planner_error && !workspace.recommended_plan);
  const runStatus = String(progress?.status ?? "");
  const runFailed = plannerFailed || ["failed", "interrupted", "aborted"].includes(runStatus);
  const analysisStarted = eventNames.has("staging_analysis_started");
  const analysisReady = Boolean(workspace?.recommended_plan) && (
    eventNames.has("staging_analysis_ready") || progress?.status === "staged"
  );
  const currentStage = String(progress?.current_stage ?? "");
  const needsHuman = runStatus === "awaiting_human";
  const schemaBlocked = currentStage === "schema_discovery" && (needsHuman || runFailed);
  // #365: "staged" is set by the worker only after the analysis call returns,
  // so a settled run with no plan has genuinely finished without one -- it is
  // not a plan that has yet to arrive.
  const settled = ["staged", "completed"].includes(runStatus);
  const outcome = stagingOutcome(
    settled && !runFailed,
    workspace?.recommended_plan,
    [...events].reverse().find((event) => event.event === "staging_analysis_skipped")?.reason,
  );
  const latestExtraction = workspace?.document_extractions?.at(-1);
  const startEvent = [...events].reverse().find((event) => event.event === "document_understanding_started");
  const engine = String(latestExtraction?.engine ?? startEvent?.engine ?? "docling");
  const ocrMode = String(latestExtraction?.ocr_mode ?? startEvent?.ocr_mode ?? "auto");

  const structured = files.some((file) => file.route === "structured") ? [
    { id: "inspect", label: "Inspect", status: statusAfter(discoveryReady, !discoveryReady) },
    { id: "profile", label: "Profile", status: statusAfter(intakeReady, currentStage === "intake") },
    {
      id: "relationships",
      label: "Review schema and describe data",
      status: statusAfter(relationshipsReady, currentStage === "schema_discovery" && !schemaBlocked, schemaBlocked),
      detail: "Includes relationship and sensitive-column checks.",
    },
    { id: "explain", label: "Explain", status: statusAfter(analysisReady, analysisStarted && relationshipsReady && !analysisReady, schemaBlocked) },
  ] satisfies RoutingSubstep[] : [];

  const documents = files.some((file) => file.route === "documents") ? [
    { id: "inspect", label: "Inspect", status: statusAfter(discoveryReady, !discoveryReady) },
    {
      id: "extract",
      label: "Extract",
      status: statusAfter(documentReady, eventNames.has("document_understanding_started") && !documentReady, documentFailed),
      detail: eventNames.has("document_understanding_started") || documentReady || documentFailed
        ? `${engine} · OCR ${ocrMode}`
        : undefined,
    },
    { id: "verify", label: "Verify", status: statusAfter(documentReady, false, documentFailed) },
    { id: "explain", label: "Explain", status: statusAfter(analysisReady && documentReady, analysisStarted && documentReady && !analysisReady && !runFailed, documentFailed || plannerFailed) },
  ] satisfies RoutingSubstep[] : [];

  const documentFiles = documentProgress(files, events, latestExtraction?.files ?? []);
  return {
    files,
    discovery: statusAfter(discoveryReady, !discoveryReady),
    structured,
    documents,
    synthesis: statusAfter(
      analysisReady,
      analysisStarted && !analysisReady && !runFailed,
      documentFailed || runFailed || schemaBlocked,
    ),
    proposal: workspace?.recommended_plan ? "complete" : runFailed || schemaBlocked ? "failed" : "pending",
    engine,
    engineVersion: latestExtraction?.engine_version,
    ocrMode,
    documentFiles,
    error: runErrorText(progress?.error) ?? workspace?.planner_error ?? undefined,
    outcome,
  };
}

function stagingOutcome(
  settled: boolean,
  plan: StagingWorkspace["recommended_plan"],
  skippedReason: unknown,
): StagingOutcome | undefined {
  if (!settled) return undefined;
  // #365: the one case the server can explain -- no planner model is
  // configured, so no plan was ever attempted -- says so rather than leaving
  // the notice to report an unexplained stop.
  if (!plan) return { kind: "no_plan", summary: localizedPair(skippedReason), rationale: [] };
  // An older record predates `pipeline_recommendation` and was always a
  // proposal, so absence of the field is not a decision to report.
  const kind = plan.pipeline_recommendation === "defer_pipeline" ? "deferred"
    : plan.pipeline_recommendation === "no_pipeline" ? "declined"
    : null;
  if (!kind) return undefined;
  return {
    kind,
    summary: plan.decision_summary ?? undefined,
    rationale: plan.rationale ?? [],
  };
}

/** A bilingual `{en, tr}` pair a run event recorded, or nothing. */
function localizedPair(value: unknown): LocalizedText | undefined {
  if (!value || typeof value !== "object") return undefined;
  const pair = value as { en?: unknown; tr?: unknown };
  if (typeof pair.en !== "string" || !pair.en) return undefined;
  return { en: pair.en, tr: typeof pair.tr === "string" && pair.tr ? pair.tr : pair.en };
}

function documentProgress(
  files: RoutedSourceFile[],
  events: Array<Record<string, unknown>>,
  completed: NonNullable<StagingWorkspace["document_extractions"]>[number]["files"],
): DocumentFileProgress[] {
  const byName = new Map<string, DocumentFileProgress>();
  for (const file of files.filter((item) => item.route === "documents")) {
    byName.set(file.name, { sourceFile: file.name, status: "queued", warnings: [] });
  }
  for (const event of events) {
    const eventName = String(event.event ?? "");
    if (!eventName.startsWith("document_file_")) continue;
    const sourceFile = String(event.source_file ?? "");
    if (!sourceFile) continue;
    const status = eventName.endsWith("started") ? "running"
      : eventName.endsWith("ready") ? "ready" : "failed";
    byName.set(sourceFile, {
      sourceFile,
      status,
      pageCount: numberOrUndefined(event.page_count),
      tableCandidates: numberOrUndefined(event.table_candidates),
      figureCandidates: numberOrUndefined(event.figure_candidates),
      durationSeconds: numberOrUndefined(event.duration_seconds),
      warnings: bilingual(stringList(event.warnings), stringList(event.warnings_tr)),
    });
  }
  for (const file of completed ?? []) {
    byName.set(file.source_file, {
      sourceFile: file.source_file,
      status: file.status,
      pageCount: file.page_count,
      tableCandidates: file.table_candidates,
      figureCandidates: file.figure_candidates,
      durationSeconds: file.duration_seconds,
      warnings: bilingual(file.warnings, file.warnings_tr),
    });
  }
  return [...byName.values()];
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/**
 * Pair two positionally aligned server lists into one bilingual list.
 *
 * A run recorded before warnings had a Turkish half carries only the English
 * one, so each entry falls back to its English text instead of a blank line.
 */
function bilingual(en: string[], tr?: string[]): LocalizedText[] {
  return en.map((text, index) => ({ en: text, tr: tr?.[index] ?? text }));
}
