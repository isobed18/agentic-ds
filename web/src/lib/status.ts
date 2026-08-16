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
  s === "queued" || s === "running" || s === "resuming";

/** Human-facing label. The backend vocabulary is precise but not prose. */
export const statusLabel = (s?: string | null) =>
  ({
    pending: "pending",
    running: "running",
    retry: "retrying",
    blocked: "needs approval",
    failed: "failed",
    succeeded: "complete",
    interrupted: "interrupted",
    awaiting_human: "needs you",
  })[s ?? ""] ?? (s ?? "").replace(/_/g, " ");
