/**
 * Typed client for the control-plane API.
 *
 * The backend is the source of truth: every shape here mirrors an endpoint that
 * already exists. Nothing is invented client-side, so the UI cannot drift into
 * showing state the run does not actually have.
 */

export type { StageStatus } from "./status";
import type { StageStatus } from "./status";

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

export interface DatasetSummary {
  source_id: string;
  label: string;
  tables: number;
  rows: number;
  columns: number;
  candidate_keys: number;
  quality_issues: number;
  sensitive_columns: number;
  table_summaries: { name: string; format: string; rows: number; columns: number; candidate_keys: number; issues: string[] }[];
  privacy: string;
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
  report_markdown?: string;
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
  critique?: string | null;
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
  format: string;
  rows: number;
  columns_count: number;
  candidate_keys: string[][];
  issues: string[];
  columns: ProfiledColumn[];
}

export interface SourceProfile {
  source_id: string;
  tables: ProfiledTable[];
  privacy: string;
}

export interface RunOptions {
  task_types: string[];
  metrics_by_task: Record<string, string[]>;
  split_strategies: string[];
  defaults: { n_folds: number; test_size: number; candidate_limit: number };
  agent_panel_sizes: number[];
  execution_modes: { value: string; label: string; description: string }[];
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
  task_type: string;
  primary_metric: string;
  target_column: string | null;
  problem_title?: string;
  excluded_columns?: string[];
  validation: {
    strategy: string;
    n_folds: number;
    test_size: number;
    group_column?: string | null;
    time_column?: string | null;
  };
  instructions?: string[];
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 240)}` : ""}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  /**
   * Without a run this is the blank pipeline template; with one, the same
   * graph annotated with per-stage status, attempts and retries. This — not
   * /progress, which returns only the raw event log — is where node state
   * comes from.
   */
  workflow: (runId?: string | null) =>
    request<Workflow>(runId ? `/api/workflow?run_id=${encodeURIComponent(runId)}` : "/api/workflow"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: string) => request<Record<string, unknown>>(`/api/runs/${id}`),
  runProgress: (id: string) => request<{ nodes?: WorkflowNode[]; status?: string; [k: string]: unknown }>(`/api/runs/${id}/progress`),
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
  runOptions: () => request<RunOptions>("/api/run-options"),
  dataSources: () => request<DataSource[]>("/api/data-sources"),
  sourceProfile: (id: string) =>
    request<SourceProfile>(`/api/data-sources/${encodeURIComponent(id)}/profile`),
  datasets: () => request<DatasetSummary[]>("/api/catalog/datasets"),
  experiments: () => request<ExperimentSummary[]>("/api/catalog/experiments"),
  models: () => request<ModelSummary[]>("/api/catalog/models"),
  reports: () => request<ReportSummary[]>("/api/catalog/reports"),
  hardening: () => request<Hardening>("/api/hardening"),
  plannerChat: (body: unknown) => request<{ reply?: string; message?: string; [k: string]: unknown }>("/api/planner/chat", { method: "POST", body: JSON.stringify(body) }),
  artifact: (id: string) => request<Record<string, unknown>>(`/api/artifacts/${id}`),
};
