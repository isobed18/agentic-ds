/** Why a stage failed, in a form a panel can render directly.
 *
 * The evidence for a failure is scattered: an attempt carries a free-text
 * `error`, its critique carries the mechanical checks that were not met, and a
 * run that died before any stage reported carries only its own error string.
 * The guided pipeline used to show none of it -- a failed group's inspector
 * rendered the group's *description* as a hint, which explains what the stage
 * does and says nothing about what went wrong (#295).
 *
 * Kept out of the components because picking the right attempt out of a history
 * is the part that is easy to get subtly wrong.
 */
import type { StageDetail } from "../lib/api";
import { activeLanguage } from "../lib/i18n";

export interface StageFailure {
  /** The attempt's own error string, when it recorded one. */
  error: string | null;
  /** The mechanical checks the attempt failed, with whatever the run recorded
   *  about each. `evidence` is the fallback wording for a check id this build
   *  has never seen. */
  checks: { id: string; evidence: string | null }[];
}

/**
 * A check id is a code, so it is translated as one. The recorded evidence is
 * the fallback for a check this build has never seen -- better an English
 * sentence than a bare identifier.
 */
export function checkText(checkId: string, evidence?: string | null): string {
  return CHECK_TEXT[checkId] ?? evidence ?? checkId;
}

export const CHECK_TEXT: Record<string, string> = {
  "schema.base_grain_declared": "The plan declared no base grain.",
  "schema.fan_out_aggregated":
    "The plan did not pass the relationship and fan-out validators.",
  "schema.plan_trial_passed":
    "The plan failed a trial execution against the real tables.",
};

/**
 * A bilingual error the server recorded, read in the reader's language.
 *
 * Older records and errors nobody recognised are plain strings; a stage worker
 * running outside any request records both halves (#263, #265).
 */
export function runErrorText(
  value: unknown,
  turkish: boolean = activeLanguage() === "tr",
): string | null {
  if (typeof value === "string") return value || null;
  if (value && typeof value === "object") {
    const pair = value as { en?: string; tr?: string };
    const chosen = turkish ? (pair.tr ?? pair.en) : pair.en;
    return chosen || null;
  }
  return null;
}

/**
 * What to say about a failure, or null when there is nothing to say.
 *
 * The *last* attempt that recorded anything wins. A stage that was retried
 * three times has three attempts in its history, and the earlier ones are the
 * ones that were superseded -- leading with attempt #1 would explain a failure
 * the run already moved past.
 *
 * The run's own error is the fallback rather than the first choice: it is what
 * survives when the run died before any stage reported an attempt, which is
 * exactly the case that renders an otherwise empty group.
 */
export function stageFailure(detail: StageDetail | null, runError: string | null): StageFailure | null {
  for (const attempt of [...(detail?.attempts ?? [])].reverse()) {
    const unmet = attempt.critique?.unmet_criteria ?? [];
    if (!attempt.error && !unmet.length) continue;
    const findings = attempt.critique?.findings ?? [];
    return {
      error: attempt.error ?? null,
      checks: unmet.map((id) => ({
        id,
        evidence: findings.find((finding) => finding.check_id === id)?.evidence ?? null,
      })),
    };
  }
  return runError ? { error: runError, checks: [] } : null;
}
