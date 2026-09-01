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
  s === "queued" ||
  s === "running" ||
  s === "resuming" ||
  s === "staging" ||
  s === "branches_running";

/** Human-facing label. The backend vocabulary is precise but not prose. */
export const statusLabel = (s?: string | null) =>
  t(({
    pending: "pending",
    queued: "queued",
    staging: "reading the data",
    staged: "ready to run",
    running: "running",
    resuming: "resuming",
    branches_running: "running branches",
    retry: "retrying",
    blocked: "needs approval",
    failed: "failed",
    succeeded: "complete",
    completed: "complete",
    interrupted: "interrupted",
    awaiting_human: "needs you",
  })[s ?? ""] ?? (s ?? "").replace(/_/g, " "));

/**
 * How a status *looks* — the one mapping both canvases read (#195).
 *
 * The two canvases speak two different status vocabularies: the understanding
 * page's nodes carry `ProgressStatus` (`complete`/`running`/`failed`/`pending`)
 * and the ML pipeline's carry `WorkflowNode["status"]` (`succeeded`/`blocked`/
 * `retry`/…), with run-level states (`queued`, `staging`, `awaiting_human`)
 * reaching the same badges from the run header. Each page used to fold its own
 * vocabulary into colours itself, so the two drifted apart: different palettes,
 * a pulse guarded by `motion-reduce` on one page and unguarded on the other.
 *
 * Folding *both* vocabularies into one presentation here is the actual work:
 * with a single mapping the two canvases are identical by construction rather
 * than by two lists being kept in sync.
 */
export type StatusTone = "complete" | "running" | "attention" | "pending";

const STATUS_TONES: Record<string, StatusTone> = {
  complete: "complete",
  completed: "complete",
  succeeded: "complete",
  queued: "running",
  staging: "running",
  running: "running",
  resuming: "running",
  branches_running: "running",
  retry: "running",
  blocked: "attention",
  failed: "attention",
  interrupted: "attention",
  awaiting_human: "attention",
  staged: "pending",
  pending: "pending",
};

export const statusTone = (s?: string | null): StatusTone => STATUS_TONES[s ?? ""] ?? "pending";

/**
 * The badge vocabulary: capitalised, one label per state (#195).
 *
 * `statusLabel` is deliberately lowercase — its own comment calls the backend
 * vocabulary "precise but not prose" — which is right for the sentence
 * fragments it feeds ("running · 3m 12s") and wrong for a badge standing on its
 * own. The ML pipeline's nodes rendered `statusLabel` in a badge, so they read
 * "tamamlandı"/"bekliyor" beside the understanding page's
 * "Tamamlandı"/"Bekliyor". Badges take these; `statusLabel` stays for the
 * mid-sentence uses it was written for.
 */
const BADGE_LABELS: Record<string, string> = {
  pending: "Waiting",
  queued: "Queued",
  staging: "Reading the data",
  staged: "Ready to run",
  running: "Processing",
  resuming: "Resuming",
  branches_running: "Running branches",
  retry: "Retrying",
  blocked: "Needs approval",
  failed: "Failed",
  succeeded: "Complete",
  complete: "Complete",
  completed: "Complete",
  interrupted: "Interrupted",
  awaiting_human: "Needs you",
};

export const statusBadgeLabel = (s?: string | null): string =>
  t(BADGE_LABELS[s ?? ""] ?? titleCase((s ?? "").replace(/_/g, " ")));

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
  missing_required_artifact: "A required stage output is missing",
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
  critique_errors: "The agent's own review raised errors",
  separator_needs_confirmation: "A column may perfectly determine the target",
  repeated_validation_failures: "Output kept failing validation",
  profile_requires_confirmation: "Your profile requires confirmation at every stage",
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
