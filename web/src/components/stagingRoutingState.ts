import type { LocalizedText, SourceProfile, StagingWorkspace } from "../lib/api";

export type RouteKind = "structured" | "documents" | "unsupported";
export type ProgressStatus = "complete" | "running" | "pending" | "failed";

export interface RoutedSourceFile {
  name: string;
  format: string;
  route: RouteKind;
  reason?: LocalizedText;
  tableNames: string[];
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
  attention?: string;
}

export interface StagingProgressSnapshot {
  status?: string;
  current_stage?: string | null;
  error?: string | null;
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
  const humanPrompt = progress?.pending_question?.human_prompt;
  const attention = needsHuman
    ? [humanPrompt?.question, humanPrompt?.context_summary].filter(Boolean).join(" ")
      || "Understanding stopped because a decision is required."
    : undefined;
  const latestExtraction = workspace?.document_extractions?.at(-1);
  const startEvent = [...events].reverse().find((event) => event.event === "document_understanding_started");
  const engine = String(latestExtraction?.engine ?? startEvent?.engine ?? "docling");
  const ocrMode = String(latestExtraction?.ocr_mode ?? startEvent?.ocr_mode ?? "auto");

  const structured = files.some((file) => file.route === "structured") ? [
    { id: "inspect", label: "Inspect", status: statusAfter(discoveryReady, !discoveryReady) },
    { id: "profile", label: "Profile", status: statusAfter(intakeReady, currentStage === "intake") },
    { id: "relationships", label: "Find relationships", status: statusAfter(relationshipsReady, currentStage === "schema_discovery" && !schemaBlocked, schemaBlocked) },
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
    error: typeof progress?.error === "string" && progress.error
      ? progress.error
      : workspace?.planner_error ?? undefined,
    attention,
  };
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
