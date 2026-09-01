/**
 * Typed client for the control-plane API.
 *
 * The backend is the source of truth: every shape here mirrors an endpoint that
 * already exists. Nothing is invented client-side, so the UI cannot drift into
 * showing state the run does not actually have.
 */

export type { StageStatus } from "./status";
import type { StageStatus } from "./status";
import { activeLanguage, t } from "./i18n";

export interface WorkflowNode {
  id: string;
  component: string;
  consumes: string[];
  produces: string[];
  description: string;
  optional: boolean;
  order: number;
  kind: string;
  owner: string;
  status: StageStatus;
  attempt_count: number;
  retry_count: number;
  /** Wall time summed over this stage's attempts; null before it has run. */
  elapsed_seconds?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
  branch_of?: string | null;
  label?: string;
}

export interface Workflow {
  workflow: string;
  version: string;
  entry: string;
  nodes: WorkflowNode[];
  edges?: { source: string; target: string; condition?: string | null }[];
  run_id?: string | null;
  run_status?: string | null;
}

export interface RunSummary {
  run_id: string;
  status: string;
  artifact_count?: number;
  stages?: string[];
  last_activity?: string;
  label?: string;
  created_at?: string;
  pending_question?: GateDecision | null;
  dataset?: string;
  parent_run_id?: string | null;
  branch_label?: string | null;
  source_id?: string | null;
  automation_id?: string | null;
}

export interface AutomationDefinition {
  schema_version: "1";
  automation_id: string;
  name: string;
  status: "draft" | "saved" | "error";
  revision: number;
  source_id?: string | null;
  selected_files?: AutomationInputFile[];
  pipeline_blueprint?: PipelineBlueprint | null;
  pipeline_layout: PipelineLayout;
  workspace_artifact_id?: string | null;
  execution_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface AutomationInputFile {
  source_id: string;
  path: string;
}

/** A durable project is the container; automations are its children. */
export interface ProjectDefinition {
  schema_version: "1";
  project_id: string;
  name: string;
  revision: number;
  /** The account that created it (#206). `null` means it predates ownership,
   *  and those stay visible to everyone rather than being orphaned. */
  owner: string | null;
  /** Private is the default: only the owner may see the project at all. */
  visibility: ProjectVisibility;
  /** Whether the account reading this owns the project, computed per request.
   *  It answers "may I change who sees this?", which is a fact about the
   *  reader, so the toggle renders interactive for one account and as a
   *  read-only mark for every other (#207). */
  mine: boolean;
  source_ids: string[];
  automation_ids: string[];
  created_at: string;
  updated_at: string;
}

export type ProjectVisibility = "private" | "public";

export interface GateDecision {
  artifact_id?: string;
  stage_id: string;
  attempt: number;
  verdict: string;
  reason_code: string;
  triggered_rules: string[];
  human_prompt?: HumanPrompt | null;
  correction_instructions?: string[];
}

export interface HumanPrompt {
  stage_id: string;
  question: string;
  /** Which kind of question this is, apart from its wording. The text itself is
   *  built in English on the server; the card needs the KIND to render Turkish. */
  question_kind?: "problem" | "checkpoint" | "no_output" | "missing_output";
  missing_artifact_types?: string[];
  context_summary: string;
  context_note?: string;
  reason_codes?: string[];
  /** #262: suspect columns behind a leakage escalation, empty otherwise. */
  leakage_suspect_columns?: string[];
  /** Subset of leakage_suspect_columns that are target-correlation suspects
   *  rather than structural ones (labelling only; both still get offered). */
  leakage_target_columns?: string[];
  options: { option_id: string; label: string; consequence: string; downstream_effect?: string | null; recommended?: boolean }[];
  allows_free_text?: boolean;
}

/**
 * A folder under `data/` that the profiler cannot read comes back carrying only
 * `source_id`, `label` and `profile_error` — every measured field is absent.
 * They are optional here because the API genuinely omits them, not as a
 * convenience: typing them as required made the UI crash on the first
 * unreadable folder it met.
 */
export interface DatasetSummary {
  source_id: string;
  label: string;
  profile_error?: string;
  tables?: number;
  rows?: number;
  columns?: number;
  candidate_keys?: number;
  quality_issues?: number;
  sensitive_columns?: number;
  table_summaries?: { name: string; format: string; rows: number; columns: number; candidate_keys: number; issues: string[] }[];
  documents?: number;
  document_pages?: number;
  document_summaries?: { name: string; format: string; pages: number }[];
  privacy?: string;
}

/** One page of {@link DatasetSummary}, with the unfiltered total behind it. */
export interface DatasetPage {
  items: DatasetSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface ModelSummary {
  artifact_id: string;
  run_id: string;
  created_at: string;
  winner_id: string;
  display_name: string;
  estimator: string;
  metric: string;
  holdout_score: number;
  cv_mean: number;
  cv_std: number;
  saved: boolean;
  candidate_count: number;
  training_rows: number;
}

export interface ReportSummary {
  artifact_id: string;
  run_id: string;
  created_at: string;
  title?: string;
  [k: string]: unknown;
}

export interface ExperimentSummary {
  run_id: string;
  [k: string]: unknown;
}

/**
 * One project's own inputs and outputs, assembled from its execution history
 * rather than a global catalogue filtered by source (#111). `source_references`
 * names every project bound to the same reusable input, so shared data is
 * legible without any project owning another's outputs.
 */
export interface ProjectDataSource extends DatasetSummary {
  files: string[];
}

export interface ProjectOwnedOutput {
  automation_id: string;
  automation_name: string;
}

export interface ProjectContents {
  project: ProjectDefinition;
  data: ProjectDataSource[];
  automations: AutomationDefinition[];
  executions: (ExperimentSummary & ProjectOwnedOutput)[];
  models: (ModelSummary & ProjectOwnedOutput)[];
  reports: (ReportSummary & ProjectOwnedOutput)[];
}

/** One automation's private input snapshot and only the outputs it produced. */
export interface AutomationContents {
  automation: AutomationDefinition;
  project: ProjectDefinition;
  data: AutomationInputFile[];
  executions: ExperimentSummary[];
  models: ModelSummary[];
  reports: ReportSummary[];
}

/** One project as the home page shows it: state, size, and where to resume. */
export type ProjectState = "running" | "awaiting_human" | "failed" | "completed" | "idle";

export interface HomeProject {
  project_id: string;
  name: string;
  visibility: ProjectVisibility;
  mine: boolean;
  status: "draft" | "saved" | "error";
  source_id: string | null;
  execution_count: number;
  updated_at: string;
  state: ProjectState;
  needs_attention: boolean;
  latest_run_id: string | null;
}

/** A model or report a project produced, project-labelled for rediscovery. */
export interface HomeOutput {
  kind: "model" | "report";
  project_id: string | null;
  project_name: string | null;
  automation_id?: string | null;
  automation_name?: string | null;
  run_id: string;
  artifact_id: string;
  label: string;
  created_at: string;
}

export interface HomeOverview {
  projects: HomeProject[];
  totals: {
    projects: number;
    executions: number;
    running: number;
    awaiting_human: number;
    failed: number;
    completed: number;
  };
  recent: HomeOutput[];
}

export type FactValue = string | number | boolean;

/**
 * The presentation layer the backend attaches to every artifact.
 *
 * `headline`, `explanation` and `facts` are always present; the remaining
 * fields are stage-specific and appear only where that stage measured
 * something of that kind. The UI renders whichever are present rather than
 * switching on stage id, so a new stage that emits `model_comparison` gets a
 * comparison table without a frontend change.
 */
export interface AnalysisPanelSpec {
  id: string;
  title: string;
  severity: string;
  caption?: string;
  description?: string;
  chart: Record<string, unknown> & { kind: string };
  insights?: string[];
  table?: { columns: string[]; rows: (string | number)[][] } | null;
  origin?: string;
  proposed_interpretations?: {
    interpretation?: string;
    why_it_matters?: string;
    verification_question?: string;
    confidence?: string;
    epistemic_state?: string;
    measurement_id?: string;
  }[];
}

export interface Story {
  /** The analysis strip: one panel per measured analysis, most severe first. */
  panels?: AnalysisPanelSpec[];
  /** Join plan as nodes and edges, with measured overlap on each edge. */
  schema_graph?: {
    base_table?: string | null;
    nodes: { id: string; role: string; source_table?: string | null }[];
    edges: {
      source: string; target: string; left_columns: string[]; right_columns: string[];
      how: string; overlap_rate?: number | null; confidence: string; via: string;
    }[];
  };
  headline?: string;
  explanation?: string;
  facts?: { label: string; value: FactValue }[];
  suggestion?: string | null;
  warnings?: string[];
  rationale?: string | null;
  criteria?: Record<string, boolean>;
  findings?: unknown[];
  excluded_columns?: string[];
  choices?: {
    title: string; target?: string; task?: string; metric?: string;
    rationale?: string; viable?: boolean;
  }[];
  model_comparison?: {
    candidate: string; selected?: boolean; baseline?: boolean;
    cv_mean?: number; cv_std?: number; holdout?: number;
  }[];
  holdout_metrics?: { metric: string; score: number }[];
  history_alerts?: { detail: string; resolved?: boolean }[];
  visuals?: {
    target?: {
      column: string; kind: string; total_count?: number; non_null_count?: number;
      null_rate?: number;
      values?: { value: string; count: number }[];
      numeric?: Record<string, number>;
    };
    missingness?: { column: string; null_count: number; null_rate: number }[];
  };
  /** The agent's own explanations, each bound to what it was measured from. */
  insights?: {
    kind?: string;
    interpretation?: string;
    why_it_matters?: string;
    verification_question?: string;
    confidence?: string;
    epistemic_state?: string;
    subjects?: string[];
  }[];
  report_markdown?: string;
  quality_checks?: { label: string; passed: boolean; detail?: string }[];
  panel?: {
    member: number;
    model: string;
    attempts: number;
    accepted: boolean;
    validation_failures?: string[];
    repairs?: string[];
    latency_s?: number;
  }[];
}

export interface StageOutput {
  artifact_id: string;
  type: string;
  name: string;
  stage: string;
  created_at: string;
  summary?: Record<string, unknown>;
  story?: Story;
}

export interface StageAttempt {
  stage_id: string;
  attempt: number;
  started_at?: string;
  ended_at?: string | null;
  artifact_ids: string[];
  verdict?: string | null;
  error?: string | null;
  /**
   * The orchestrator's rubric result. Typed as a string here for as long as
   * anyone can remember, while the API has always sent the object -- so the
   * one field that explains why an attempt was rejected could not be read.
   */
  critique?: StageCritique | null;
}

export interface StageCritique {
  rubric_version?: string;
  unmet_criteria?: string[];
  findings?: { check_id: string; severity: string; evidence?: string | null }[];
}

/** Whether this stage is waiting on a person, in the backend's own words. */
export interface HumanView {
  needs_human: boolean;
  state_label: string;
  suggestion?: string | null;
  question?: HumanPrompt | null;
  can_resume: boolean;
}

export interface DataSource {
  source_id: string;
  label: string;
  files?: string[];
}

export interface ProfiledColumn {
  name: string;
  dtype: string;
  semantic_type: string;
  sensitivity: string;
  null_rate: number;
  unique_rate: number;
  is_unique: boolean;
  candidate_target: boolean;
}

export interface ProfiledTable {
  name: string;
  source_file?: string;
  sheet_name?: string | null;
  format: string;
  rows: number;
  columns_count: number;
  candidate_keys: string[][];
  issues: string[];
  columns: ProfiledColumn[];
}

/** A foreign key the profiler measured, whether or not the data declares one. */
export interface MeasuredRelationship {
  from_table: string;
  from_columns: string[];
  to_table: string;
  to_columns: string[];
  overlap_rate: number;
  orphan_rate: number;
  parent_coverage: number;
  cardinality: string;
  name_affinity: number;
  kind?: "measured" | "suggested";
  rationale?: string;
}

export interface SourceProfile {
  source_id: string;
  source_files?: Array<{
    name: string;
    format: string;
    route: "structured" | "documents" | "unsupported";
    reason: LocalizedText;
    table_names: string[];
    // Measured from content by ads.file_detection, not from the extension. Optional
    // because the extra may not be installed, in which case the API omits it.
    detected_flow?: string;
    detection_deterministic?: boolean;
    detection_evidence?: string;
    detection_reason?: string;
    detection_conflicts_with_extension?: boolean;
  }>;
  tables: ProfiledTable[];
  documents?: ProfiledDocument[];
  relationships?: MeasuredRelationship[];
  privacy: string;
  file_detection?: {
    used: boolean;
    reason?: string;
    file_count?: number;
    deterministic_count?: number;
    adjudication_required_count?: number;
    extension_conflict_count?: number;
  };
}

export interface ProfiledDocument {
  name: string;
  format: "pdf";
  pages: number;
  text_pages: number;
  text_characters: number;
  image_count: number;
  title?: string | null;
  author?: string | null;
  understanding_status: "text_ready" | "needs_ocr_or_vision";
  training_status: "not_extracted";
  issues: string[];
}

export interface RunOptions {
  task_types: string[];
  metrics_by_task: Record<string, string[]>;
  split_strategies: string[];
  defaults: { n_folds: number; test_size: number; candidate_limit: number };
  agent_panel_sizes: number[];
  execution_modes: { value: string; label: string; description: string }[];
}

export interface LocalizedText {
  en: string;
  tr: string;
}

/** Host-registered, versioned edge contract (for example `ads.table_asset@1`). */
export type PipelineDataType = string;

export interface PipelinePort {
  id: string;
  label: LocalizedText;
  data_type: PipelineDataType;
  required: boolean;
  multiple: boolean;
}

export interface PipelineNodeControl {
  execution: "auto" | "pause_after";
  gate_handler: "human" | "planner";
  max_retries?: number | null;
}

export interface PipelineComponent {
  id: string;
  kind: "data_source" | "intake" | "schema_discovery" | "document_understanding"
    | "integration" | "report" | "planner" | "ml_pipeline" | "human_review"
    | "problem_discovery" | "validation" | "analysis" | "feature_engineering"
    | "splitting" | "training" | "evaluation" | "template";
  title: LocalizedText;
  description: LocalizedText;
  inputs: PipelinePort[];
  outputs: PipelinePort[];
  settings: Record<string, unknown>;
  control: PipelineNodeControl;
  enabled: boolean;
  optional: boolean;
  evidence_layer: "measured" | "agent_proposal" | "human_decision" | "executor";
  configured_by: "system" | "planner" | "human";
  catalog_id?: string | null;
  branch_id?: string | null;
  group_id?: string | null;
}

export interface PipelineConnection {
  id: string;
  source_component: string;
  source_port: string;
  target_component: string;
  target_port: string;
}

export interface PipelineBlueprint {
  version: "1";
  revision?: number;
  name: LocalizedText;
  components: PipelineComponent[];
  connections: PipelineConnection[];
}

export interface PipelineNodeLayout {
  component_id: string;
  x: number;
  y: number;
  collapsed: boolean;
}

export interface PipelineLayout {
  version: "1";
  nodes: PipelineNodeLayout[];
  collapsed_branches: string[];
}

export interface PipelineOutputReference {
  component_id: string;
  port_id: string;
  data_type: PipelineDataType;
  status: "ready" | "pending" | "needs_review" | "unavailable" | "not_started";
  artifact_ids: string[];
  summary: LocalizedText;
}

export interface AutomationExecutionPlan {
  blueprint_fingerprint: string;
  node_order: string[];
  nodes: Array<{
    component_id: string;
    catalog_id: string;
    executor: string;
    stage_id?: string | null;
    control: PipelineNodeControl;
  }>;
  pause_after_component?: string | null;
  pause_after_stage?: string | null;
}

export interface DocumentEngine {
  id: string;
  label: string;
  description: LocalizedText;
  available: boolean;
  install_extra?: string | null;
  local: boolean;
  selectable: boolean;
  license: string;
}

export interface AutomationComponentDefinition {
  catalog_id: string;
  category: "source" | "understand" | "extract" | "review" | "transform"
    | "agent" | "analyze" | "train" | "publish" | "template";
  title: LocalizedText;
  description: LocalizedText;
  kind: PipelineComponent["kind"];
  inputs: PipelinePort[];
  outputs: PipelinePort[];
  default_settings: Record<string, unknown>;
  evidence_layer: PipelineComponent["evidence_layer"];
  repeatable: boolean;
}

export interface DocumentExtractionSummary {
  artifact_id?: string | null;
  engine: string;
  engine_version?: string | null;
  ocr_mode?: "auto" | "always" | "never";
  status: "ready" | "failed";
  document_count: number;
  page_count: number;
  table_candidates: number;
  figure_candidates: number;
  warnings: string[];
  /** Turkish counterpart of `warnings`, matched by position. */
  warnings_tr?: string[];
  duration_seconds: number;
  files?: Array<{
    source_file: string;
    status: "ready" | "failed";
    page_count: number;
    table_candidates: number;
    figure_candidates: number;
    duration_seconds: number;
    warnings: string[];
    warnings_tr?: string[];
  }>;
}

export interface RunProgressSnapshot {
  status?: string;
  current_stage?: string | null;
  events?: Array<Record<string, unknown>>;
  attempts?: Array<Record<string, unknown>>;
  pause_requested?: boolean;
  /** #305: ids of this run's diagnostic artifacts, hidden from the default view. */
  diagnostic_artifact_ids?: string[];
  [key: string]: unknown;
}

export interface ArtifactPreview {
  artifact_id: string;
  artifact_type: string;
  fields?: Record<string, string | number | boolean>;
  collection_sizes?: Record<string, number>;
  engine?: string;
  engine_version?: string | null;
  duration_seconds?: number;
  producer_component_id?: string;
  title?: LocalizedText;
  summary?: LocalizedText;
  findings?: LocalizedText[];
  verification_questions?: LocalizedText[];
  documents?: Array<{
    source_file: string;
    title?: string | null;
    page_count: number;
    text_characters: number;
    tables: Array<Record<string, unknown>>;
    figures: Array<Record<string, unknown>>;
    warnings: string[];
    warnings_tr?: string[];
  }>;
  [key: string]: unknown;
}

export interface StagingWorkspace {
  artifact_id: string;
  run_id: string;
  source_id: string;
  source_fingerprint: string;
  intake_artifact_ids: string[];
  schema_artifact_ids: string[];
  cache_reused: boolean;
  relationship_explanations: {
    from_table: string;
    from_columns: string[];
    to_table: string;
    to_columns: string[];
    cardinality: string;
    overlap_rate: number;
    orphan_rate: number;
    explanation: LocalizedText;
    why_it_matters: LocalizedText;
    verification_question: LocalizedText;
    evidence_status: "measured_with_agent_interpretation";
  }[];
  reports: {
    title: LocalizedText;
    summary: LocalizedText;
    findings: LocalizedText[];
    verification_questions: LocalizedText[];
  }[];
  pipeline_blueprint?: PipelineBlueprint | null;
  pipeline_layout: PipelineLayout;
  component_outputs?: PipelineOutputReference[];
  document_extractions?: DocumentExtractionSummary[];
  recommended_plan?: {
    proposal_id: string;
    status: "proposed" | "accepted" | "rejected" | "superseded";
    mode: "fully_auto";
    pipeline_recommendation?: "create_pipeline" | "defer_pipeline" | "no_pipeline";
    decision_summary?: LocalizedText | null;
    configuration: Record<string, unknown>;
    stage_directives: Record<string, string[]>;
    checkpoint_stages: string[];
    auto_proceed_stages: string[];
    max_retries_by_stage: Record<string, number>;
    rationale: LocalizedText[];
    accepted: boolean;
    accepted_at?: string | null;
    accepted_by?: "human" | null;
  } | null;
  chat_history: {
    role: "user" | "planner";
    content: LocalizedText;
    model?: string | null;
  }[];
  planner_model?: string | null;
  planner_error?: string | null;
}

/**
 * The body POST /api/runs expects. The contracts are built up front in both
 * modes; in agent mode the planner agents supersede them, which is why a
 * target still has to be chosen even when the agents will re-decide it.
 */
export interface RunRequest {
  source_id: string;
  mode: string;
  agent_panel_size: number;
  base_table: string;
  base_grain: string[];
  /**
   * Optional, and deliberately so. Omitting them means "I do not know what
   * should be predicted yet" — the server records the problem as `auto` rather
   * than as the human's intent, and forces a checkpoint at problem discovery.
   * Sending a guessed value is not a neutral default; it becomes the stated
   * intent that steers every later stage.
   */
  task_type?: string;
  primary_metric?: string;
  target_column?: string | null;
  problem_title?: string;
  excluded_columns?: string[];
  validation?: {
    strategy: string;
    n_folds: number;
    test_size: number;
    group_column?: string | null;
    time_column?: string | null;
  };
  instructions?: string[];
  /** Whether the EDA investigator runs on top of the fixed profiler. */
  eda_agent?: boolean;
  /**
   * `auto` lets the gate decide on its own signals; `manual` declares every
   * stage a checkpoint, so the run stops after each one for approval.
   */
  run_mode?: "auto" | "manual" | "fully_auto";
  /** Set when this run explores an alternative problem alongside another run. */
  parent_run_id?: string;
  branch_label?: string;
}

export interface StageDetail {
  run_id: string;
  stage: { id: string; description: string; consumes: string[]; produces: string[] };
  status: StageStatus;
  attempts: StageAttempt[];
  events: { event: string; at: string; stage: string; attempt: number }[];
  inputs: StageOutput[];
  outputs: StageOutput[];
  measurements: { artifact_type: string; name: string; values: Record<string, unknown> }[];
  /** Panels built from every artifact the stage produced, not just one.
   *  Intake emits a DataCard per table, and comparing them is the whole
   *  question at that stage. */
  panels?: AnalysisPanelSpec[];
  gate_decisions: GateDecision[];
  corrections: unknown[];
  human_questions: HumanPrompt[];
  human_view: HumanView;
}

export interface Hardening {
  [group: string]: Record<string, unknown>;
}

/**
 * Guard against a stampede: the app polls several endpoints at once, so an
 * expired session produces a burst of 401s. Only the first one should navigate.
 */
let redirectingToLogin = false;

// Carries the HTTP status so callers can branch on it (e.g. retry a 409
// automation-revision conflict) without pattern-matching the message string.
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  // The server composes some panel prose around measured values, so it needs to
  // know the language at fetch time; it cannot be translated afterwards.
  const separator = path.includes("?") ? "&" : "?";
  const controller = timeoutMs ? new AbortController() : undefined;
  const timeout = timeoutMs ? window.setTimeout(() => controller?.abort(), timeoutMs) : undefined;
  let res: Response;
  try {
    res = await fetch(`${path}${separator}lang=${activeLanguage()}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch (error) {
    if (controller?.signal.aborted) {
      throw new Error(t("The planner did not respond. Please try again."));
    }
    throw error;
  } finally {
    if (timeout !== undefined) window.clearTimeout(timeout);
  }
  if (res.status === 401) {
    // The session expired or was never established. The server answers API
    // calls with 401 rather than redirecting, precisely so this decision is
    // made here instead of an HTML login page arriving where JSON was expected.
    if (!redirectingToLogin) {
      redirectingToLogin = true;
      const next = window.location.pathname + window.location.search;
      window.location.href = `/login?next=${encodeURIComponent(next)}`;
    }
    throw new Error("401 oturum gerekli");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    // #242: an error body's own {"detail": "..."} is the sentence written for a
    // person; surfacing the raw JSON blob (or a JSON parser offset buried inside
    // it) as the message is what made the planner failure unreadable. Prefer the
    // detail string; fall back to the trimmed body, then to the status line.
    let message = "";
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed.detail === "string") message = parsed.detail;
    } catch { /* not JSON — fall through to the raw text */ }
    if (!message) message = body.slice(0, 240);
    throw new ApiError(res.status, message || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}


export type DocumentTableDecisionInput = {
  candidate_id: string;
  decision: "accepted" | "rejected";
};

export type DocumentTableReviewResult = {
  artifact_id: string;
  extraction_artifact_id: string;
  accepted: number;
  rejected: number;
};

export type DocumentTablePromotionResult = {
  review_artifact_id: string;
  table_assets: Array<{
    artifact_id: string;
    rows: number;
    columns: number;
    fingerprint: string;
  }>;
};

export const api = {
  authSession: () => request<{ username: string | null; authenticated: boolean }>("/api/auth/session"),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  /**
   * Without a run this is the blank pipeline template; with one, the same
   * graph annotated with per-stage status, attempts and retries. This — not
   * /progress, which returns only the raw event log — is where node state
   * comes from.
   */
  workflow: (runId?: string | null) =>
    request<Workflow>(runId ? `/api/workflow?run_id=${encodeURIComponent(runId)}` : "/api/workflow"),
  runs: () => request<RunSummary[]>("/api/runs"),
  /**
   * The project-first home read model: every project's state plus the recent
   * work projects produced, both filtered by one search so a forgotten output
   * still leads back to its project (#111). Global catalogues no longer exist
   * as destinations, so this endpoint carries the browsing job they had.
   */
  home: (search?: string) =>
    request<HomeOverview>(`/api/home${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  projects: () => request<ProjectDefinition[]>("/api/projects"),
  project: (id: string) => request<ProjectDefinition>(`/api/projects/${encodeURIComponent(id)}`),
  createProject: (name: string) =>
    request<ProjectDefinition>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  updateProject: (id: string, expectedRevision: number, changes: Record<string, unknown>) =>
    request<ProjectDefinition>(`/api/projects/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: expectedRevision, changes }),
    }),
  /**
   * #207: while a project had one writer, losing a `expected_revision` race was
   * nearly unreachable. A public project has several, and the workspace header
   * holds the revision it loaded for as long as the tab stays open — so a
   * rename after somebody else's edit hit a 409 and surfaced it as a raw error
   * string. Same reasoning and same shape as `updateAutomationSafely`: the
   * fields these callers touch are disjoint, so re-applying the change onto the
   * latest revision cannot clobber the other writer.
   */
  async updateProjectSafely(
    id: string,
    expectedRevision: number,
    changes: Record<string, unknown>,
  ): Promise<ProjectDefinition> {
    let revision = expectedRevision;
    for (let attempt = 0; ; attempt++) {
      try {
        return await api.updateProject(id, revision, changes);
      } catch (caught) {
        if (attempt >= 3 || !(caught instanceof ApiError) || caught.status !== 409) throw caught;
        revision = (await api.project(id)).revision;
      }
    }
  },
  /**
   * Publish a project to every signed-in account, or take it back. Owner-only:
   * the server answers 403 to anyone else, which is the one asymmetry in an
   * otherwise shared project.
   */
  setProjectVisibility: (id: string, visibility: ProjectVisibility) =>
    request<ProjectDefinition>(`/api/projects/${encodeURIComponent(id)}/visibility`, {
      method: "POST",
      body: JSON.stringify({ visibility }),
    }),
  /** #181: delete a project and its automations; data and run history are kept. */
  deleteProject: (id: string) =>
    request<{ project_id: string; automations: number }>(
      `/api/projects/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  projectData: (id: string) =>
    request<ProjectDataSource[]>(`/api/projects/${encodeURIComponent(id)}/data`),
  addProjectSource: (id: string, sourceId: string) =>
    request<ProjectDefinition>(`/api/projects/${encodeURIComponent(id)}/sources`, {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId }),
    }),
  projectAutomations: (id: string) =>
    request<AutomationDefinition[]>(`/api/projects/${encodeURIComponent(id)}/automations`),
  createProjectAutomation: (id: string, name: string) =>
    request<AutomationDefinition>(`/api/projects/${encodeURIComponent(id)}/automations`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  automations: () => request<AutomationDefinition[]>("/api/automations"),
  automation: (id: string) => request<AutomationDefinition>(`/api/automations/${encodeURIComponent(id)}`),
  createAutomation: (name: string) =>
    request<AutomationDefinition>("/api/automations", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  /**
   * #101: start a fresh data project on the same files as an existing one, so a
   * person can re-run the same source as a clean project -- its own name and
   * execution history -- without re-uploading. Only the source binding is
   * carried over; the plan, layout and run history begin empty, which is what
   * "start the flow fresh" means. A project with no files has nothing to
   * duplicate, so the new record is left blank for the caller to fill.
   */
  async duplicateAutomation(source: AutomationDefinition, name: string): Promise<AutomationDefinition> {
    const created = await api.createAutomation(name);
    if (!source.source_id) return created;
    return api.updateAutomation(created.automation_id, created.revision, { source_id: source.source_id });
  },
  updateAutomation: (id: string, expectedRevision: number, changes: Record<string, unknown>) =>
    request<AutomationDefinition>(`/api/automations/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: expectedRevision, changes }),
    }),
  /**
   * #87: a rename (one fast PUT) and a large batch upload (whose final PUT binds
   * the new source) race on the same automation revision. Whichever lands first
   * bumps the revision, so the slower flow's PUT carries a now-stale
   * `expected_revision` and the server rejects it with 409 — dropping the change
   * with no retry and no sign to the user. For the upload this orphaned the just
   * -uploaded files, unattached to the project that was renamed. Each update
   * touches only its own disjoint fields (name vs source_id), so re-applying the
   * same change onto the latest revision cannot clobber the other flow: on a
   * conflict, refetch the current revision and retry.
   */
  async updateAutomationSafely(
    id: string,
    expectedRevision: number,
    changes: Record<string, unknown>,
  ): Promise<AutomationDefinition> {
    let revision = expectedRevision;
    for (let attempt = 0; ; attempt++) {
      try {
        return await api.updateAutomation(id, revision, changes);
      } catch (caught) {
        if (attempt >= 3 || !(caught instanceof ApiError) || caught.status !== 409) throw caught;
        revision = (await api.automation(id)).revision;
      }
    }
  },
  deleteAutomation: (id: string) =>
    request<{ automation_id: string; definitions: number; revisions: number }>(
      `/api/automations/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  automationExecutions: (id: string) =>
    request<RunSummary[]>(`/api/automations/${encodeURIComponent(id)}/executions`),
  selectAutomationInputs: (id: string, expectedRevision: number, selections: AutomationInputFile[]) =>
    request<AutomationDefinition>(`/api/automations/${encodeURIComponent(id)}/inputs`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: expectedRevision, selections }),
    }),
  /**
   * A project's own data, runs, models and reports, owned through its execution
   * history (#111). The project workspace shows these as tabs instead of the
   * deleted global catalogues; Models/Reports tabs appear only when the arrays
   * are non-empty, so a project that produced nothing has no such section.
   */
  projectContents: (id: string) =>
    request<ProjectContents>(`/api/projects/${encodeURIComponent(id)}/contents`),
  automationContents: (id: string) =>
    request<AutomationContents>(`/api/automations/${encodeURIComponent(id)}/contents`),
  run: (id: string) => request<Record<string, unknown>>(`/api/runs/${id}`),
  runProgress: (id: string) => request<RunProgressSnapshot>(`/api/runs/${id}/progress`),
  stage: (runId: string, stageId: string) => request<StageDetail>(`/api/runs/${runId}/stages/${stageId}`),
  createRun: (body: RunRequest) => request<RunSummary>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  /**
   * Permanent: drops the run's artifacts and its state snapshot. The backend
   * requires the run id back as confirmation and answers 409 otherwise, so the
   * UI must ask before calling this rather than deleting on a single click.
   */
  deleteRun: (id: string) =>
    request<unknown>(`/api/runs/${id}/delete`, {
      method: "POST",
      body: JSON.stringify({ confirmation: id }),
    }),
  answer: (id: string, body: unknown) => request<unknown>(`/api/runs/${id}/answer`, { method: "POST", body: JSON.stringify(body) }),
  deleteModel: (artifactId: string) =>
    request<{ artifact_id: string; index_entries: number }>(
      `/api/models/${encodeURIComponent(artifactId)}`,
      { method: "DELETE" },
    ),
  deleteReport: (artifactId: string) =>
    request<{ artifact_id: string; index_entries: number }>(
      `/api/reports/${encodeURIComponent(artifactId)}`,
      { method: "DELETE" },
    ),

  /**
   * Choosing a dataset starts a run immediately and stops it after intake and
   * schema discovery. What comes back is a real run id and the profile, so the
   * pipeline can be drawn before anything has been configured.
   */
  stageRun: (sourceId: string, reuseCache = false, pipelineBlueprint?: PipelineBlueprint | null, automationId?: string | null) =>
    request<{ run_id: string; status: string; profile: SourceProfile }>("/api/runs/staged", {
      method: "POST",
      body: JSON.stringify({
        source_id: sourceId,
        automation_id: automationId,
        reuse_cache: reuseCache,
        configuration: pipelineBlueprint ? { pipeline_blueprint: pipelineBlueprint } : undefined,
      }),
    }),
  /**
   * Re-runs an automation from the seed its most recent run recorded, rather
   * than a fresh random one (backend `rerun_with_same_seed`). Returns a new
   * staged run, same shape as `stageRun` -- the saved graph and the run being
   * re-run are both left untouched (#247).
   */
  rerun: (runId: string) =>
    request<{ run_id: string; status: string; profile: SourceProfile }>(`/api/runs/${runId}/rerun`, {
      method: "POST",
    }),
  stagingWorkspace: (runId: string) =>
    request<StagingWorkspace>(`/api/runs/${runId}/staging`),
  stagingComponents: () =>
    request<{ document_engines: DocumentEngine[]; automation_components: AutomationComponentDefinition[] }>("/api/staging/components"),
  automationComponents: () =>
    request<{ document_engines: DocumentEngine[]; components: AutomationComponentDefinition[] }>("/api/automation/components"),
  updateStagingPipeline: (runId: string, baseArtifactId: string, blueprint: PipelineBlueprint) =>
    request<StagingWorkspace>(`/api/runs/${runId}/staging/pipeline`, {
      method: "PUT",
      body: JSON.stringify({ base_artifact_id: baseArtifactId, blueprint }),
    }),
  updateStagingLayout: (runId: string, baseArtifactId: string, layout: PipelineLayout) =>
    request<StagingWorkspace>(`/api/runs/${runId}/staging/layout`, {
      method: "PUT",
      body: JSON.stringify({ base_artifact_id: baseArtifactId, layout }),
    }),
  acceptStagingPlan: (runId: string, baseArtifactId: string) =>
    request<StagingWorkspace & { execution_plan_artifact_id: string }>(
      `/api/runs/${runId}/staging/plan/accept`,
      { method: "POST", body: JSON.stringify({ base_artifact_id: baseArtifactId }) },
    ),
  /**
   * Accept the plan, against the workspace as it is now (#167).
   *
   * The base artifact id is a moving value: the workspace is polled every
   * 2200ms and rewritten on each tick, so any snapshot the runner writes
   * between the last tick and this POST invalidates the id that was just sent.
   * Accepting then failed outright, and two seconds later the client was
   * holding the newer snapshot anyway with no path back to the accept the
   * person had already asked for.
   *
   * Re-reads and retries on 409, the way `updateAutomationSafely` does for a
   * revision conflict. Only on 409: a malformed request is the client's own
   * mistake and would fail identically however many times it is repeated,
   * which is why the route had to stop reporting both as 400 first.
   */
  async acceptStagingPlanSafely(
    runId: string,
    baseArtifactId: string,
  ): Promise<StagingWorkspace & { execution_plan_artifact_id: string }> {
    let base = baseArtifactId;
    for (let attempt = 0; ; attempt++) {
      try {
        return await api.acceptStagingPlan(runId, base);
      } catch (caught) {
        if (attempt >= 3 || !(caught instanceof ApiError) || caught.status !== 409) throw caught;
        base = (await api.stagingWorkspace(runId)).artifact_id;
      }
    }
  },
  /** Record accept/reject decisions for extracted PDF table candidates.
   *
   * Two calls rather than one, because the backend keeps the decision and the
   * promotion as separate artifacts: the review is the record of what a person
   * decided, and it stands whether or not anything was promoted from it. */
  reviewDocumentTables: (runId: string, decisions: DocumentTableDecisionInput[]) =>
    request<DocumentTableReviewResult>(
      `/api/runs/${runId}/staging/documents/review`,
      { method: "POST", body: JSON.stringify({ decisions }) },
    ),
  /** Turn the accepted candidates of a review into real tables. */
  promoteDocumentTables: (runId: string, reviewArtifactId: string) =>
    request<DocumentTablePromotionResult>(
      `/api/runs/${runId}/staging/documents/promote`,
      { method: "POST", body: JSON.stringify({ review_artifact_id: reviewArtifactId }) },
    ),
  compileAutomation: (runId: string) =>
    request<{ artifact_id: string; workspace_artifact_id: string; plan: AutomationExecutionPlan }>(
      `/api/runs/${runId}/automation/compile`,
      { method: "POST" },
    ),
  startAutomationBranches: (runId: string) =>
    request<{ parent_run_id: string; branches: Array<{ branch_id: string; run_id: string }> }>(
      `/api/runs/${runId}/automation/branches/start`,
      { method: "POST" },
    ),
  runDocumentUnderstanding: (runId: string) =>
    request<StagingWorkspace>(`/api/runs/${runId}/staging/documents/run`, { method: "POST" }),
  artifactPreview: (artifactId: string) =>
    request<ArtifactPreview>(`/api/artifacts/${encodeURIComponent(artifactId)}/preview`),
  /** Amend a staged run's configuration. Accepted while it is still staging. */
  updateStaged: (runId: string, configuration: Record<string, unknown>) =>
    request<{ configuration: Record<string, unknown> }>(`/api/runs/${runId}/staged`, {
      method: "PATCH",
      body: JSON.stringify(configuration),
    }),
  /** Continue a staged run through the rest of the pipeline. Same run id. */
  startStaged: (runId: string, configuration: Record<string, unknown>) =>
    request<{ run_id: string; status: string }>(`/api/runs/${runId}/start`, {
      method: "POST",
      body: JSON.stringify(configuration),
    }),
  pauseRun: (runId: string) =>
    request<{ run_id: string; status: string }>(`/api/runs/${runId}/pause`, { method: "POST" }),
  discardStaged: (runId: string) =>
    request<{ run_id: string; status: string }>(`/api/runs/${runId}/discard`, { method: "POST" }),
  runOptions: () => request<RunOptions>("/api/run-options"),
  dataSources: () => request<DataSource[]>("/api/data-sources"),
  installPdfDemo: () =>
    request<{ source_id: string; label: string; files: string[]; reused: boolean }>(
      "/api/demo-data/pdf",
      { method: "POST" },
    ),
  sourceProfile: (id: string) =>
    request<SourceProfile>(`/api/data-sources/${encodeURIComponent(id)}/profile`),
  defaultStagingPipeline: (id: string) =>
    request<PipelineBlueprint>(`/api/data-sources/${encodeURIComponent(id)}/pipeline-blueprint`),
  datasets: (opts: { search?: string; page?: number; pageSize?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.search) params.set("search", opts.search);
    if (opts.page) params.set("page", String(opts.page));
    if (opts.pageSize) params.set("page_size", String(opts.pageSize));
    const query = params.toString();
    return request<DatasetPage>(`/api/catalog/datasets${query ? `?${query}` : ""}`);
  },

  /**
   * Upload one file. Omit `sourceId` for the first file of a new group and pass
   * the returned id for every file after it, so a multi-table dataset arrives
   * as one source rather than several.
   *
   * Uses XHR rather than fetch so an upload can report progress and be aborted:
   * fetch exposes neither, so a large file left a static "Uploading…" with no
   * percentage and no way to cancel except refreshing the tab (#84). `signal`
   * cancels the in-flight request; `onProgress` receives 0..1 as bytes are sent.
   */
  upload: (
    file: File,
    sourceId?: string,
    opts: { signal?: AbortSignal; onProgress?: (fraction: number) => void } = {},
  ) =>
    new Promise<{ source_id: string; label: string; files: string[]; reused?: boolean }>((resolve, reject) => {
      const query = sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : "";
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/uploads/${encodeURIComponent(file.name)}${query}`);
      xhr.upload.onprogress = (event) => {
        if (opts.onProgress && event.lengthComputable) opts.onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { reject(new Error("Malformed upload response")); }
          return;
        }
        let detail = xhr.responseText;
        try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch { /* not JSON */ }
        reject(new Error(detail || String(xhr.status)));
      };
      xhr.onerror = () => reject(new Error("Upload failed"));
      xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
      if (opts.signal) {
        if (opts.signal.aborted) { xhr.abort(); return; }
        opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
      xhr.send(file);
    }),
  /** Remove one file from an upload group; `deleted` is true if that was its last. */
  removeSourceFile: (sourceId: string, filename: string) =>
    request<{ source_id: string; files: string[]; deleted: boolean; label?: string }>(
      `/api/data-sources/${encodeURIComponent(sourceId)}/files/${encodeURIComponent(filename)}`,
      { method: "DELETE" },
    ),
  // #111: the global experiment/model/report catalogues are no longer browsed
  // as destinations -- these outputs are read per-project through
  // projectContents. The dataset catalogue endpoint stays (the source picker in
  // LaunchDialog lists reusable sources from it).
  hardening: () => request<Hardening>("/api/hardening"),
  /** Move a column between pii and internal before the classification is used. */
  overrideSensitivity: (runId: string, columns: Record<string, "pii" | "internal">) =>
    request<{ applied: Record<string, string> }>(`/api/runs/${runId}/sensitivity`, {
      method: "POST",
      body: JSON.stringify({ columns }),
    }),

  /** Address an instruction to the agent working one stage of this run. */
  directStage: (runId: string, stageId: string, instruction: string) =>
    request<{ directives: string[] }>(`/api/runs/${runId}/stages/${stageId}/direct`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  directives: (runId: string) =>
    request<{ directives: Record<string, string[]> }>(`/api/runs/${runId}/directives`),

  plannerChat: (body: unknown) => request<{ reply?: string; message?: string; [k: string]: unknown }>("/api/planner/chat", { method: "POST", body: JSON.stringify(body) }, 120_000),
  artifact: (id: string) => request<Record<string, unknown>>(`/api/artifacts/${id}`),
};
