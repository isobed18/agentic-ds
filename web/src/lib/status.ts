import { t } from "./i18n";
/**
 * The stage-status vocabulary, stated once.
 *
 * These six strings are produced by `ControlPlane._stage_status` in
 * src/ads/api/service.py and nowhere else. Keeping the mapping in one module
 * is deliberate: the first version of this UI spelled the same concept
 * "completed" in the progress bar, "retrying" in the rail and "auto_proceed"
 * in the tone map, so a finished run rendered as 0/11 complete.
 */
export type StageStatus =
  | "pending"
  | "running"
  | "retry"
  | "blocked"
  | "failed"
  | "succeeded";

/** A stage that finished and passed its gate. */
export const isSucceeded = (s?: string | null) => s === "succeeded";

/** A stage the run is currently working through — the reason to keep polling. */
export const isActive = (s?: string | null) => s === "running" || s === "retry";

/** A stage that cannot proceed without a person. */
export const isAttention = (s?: string | null) => s === "blocked" || s === "failed";

/**
 * Run-level states that mean more progress is still coming.
 *
 * `interrupted` is deliberately excluded. The backend reports it for a run
 * whose worker process exited, and polling one forever is exactly the bug that
 * state was introduced to end.
 */
export const isRunActive = (s?: string | null) =>
  s === "queued" || s === "running" || s === "resuming" || s === "staging";

/** Human-facing label. The backend vocabulary is precise but not prose. */
export const statusLabel = (s?: string | null) =>
  t(({
    pending: "pending",
    queued: "queued",
    staging: "reading the data",
    staged: "ready to run",
    running: "running",
    resuming: "resuming",
    retry: "retrying",
    blocked: "needs approval",
    failed: "failed",
    succeeded: "complete",
    completed: "complete",
    interrupted: "interrupted",
    awaiting_human: "needs you",
  })[s ?? ""] ?? (s ?? "").replace(/_/g, " "));

/**
 * Gate verdicts and reason codes are internal vocabulary. They stay in the API
 * and the audit record — the run's own history should read as sentences, not as
 * enum values a reader has to decode.
 */
const VERDICT_LABELS: Record<string, string> = {
  auto_proceed: "Continued",
  retry: "Sent back",
  escalate: "Asked you",
  abort: "Stopped",
};

export const verdictLabel = (v: string): string =>
  t(VERDICT_LABELS[v] ?? titleCase(v));

const REASON_LABELS: Record<string, string> = {
  profile_checkpoint: "You asked to review this step",
  leakage_detected: "A feature may leak the answer",
  leakage_unresolved: "Leakage still present after rework",
  leakage_challenge_needs_confirmation: "Agent challenged the leakage finding",
  retry_budget_exhausted: "No attempts left",
  repeated_identical_failure: "The same failure repeated",
  pii_egress_requested: "Personal data would leave the machine",
  destructive_operation: "A step would modify the source data",
  model_below_baseline: "The model did not beat the baseline",
  lift_within_noise: "The improvement is within noise",
  high_cv_variance: "Fold-to-fold scores vary widely",
  candidate_disagreement: "The proposals disagreed",
  statistical_support_low: "Not enough data to support this",
  degenerate_split: "The split left a fold unusable",
  unmet_mandatory_criteria: "A required check did not pass",
  risk_class_gate: "This step is high risk",
  clean: "Nothing to flag",
};

export const reasonLabel = (code: string): string =>
  t(REASON_LABELS[code] ?? titleCase(code));

function titleCase(s: string): string {
  const words = s.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * A stage duration, at the precision a person actually reads. Seconds below a
 * minute, minutes and seconds below an hour: "3m 12s" answers "is this stuck?"
 * and "192.4 seconds" makes you do the arithmetic yourself.
 */
export function elapsedLabel(seconds?: number | null): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
