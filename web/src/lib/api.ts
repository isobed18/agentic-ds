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
  pipeline_blueprint?: PipelineBlueprint | null;
  pipeline_layout: PipelineLayout;
  workspace_artifact_id?: string | null;
  execution_ids: string[];
  created_at: string;
  updated_at: string;
}

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
  context_summary: string;
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
  [key: string]: unknown;
}

export interface ArtifactPreview {
  artifact_id: string;
  artifact_type: string;
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
    throw new ApiError(res.status, `${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 240)}` : ""}`);
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
  experiments: () => request<ExperimentSummary[]>("/api/catalog/experiments"),
  models: () => request<ModelSummary[]>("/api/catalog/models"),
  reports: () => request<ReportSummary[]>("/api/catalog/reports"),
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
