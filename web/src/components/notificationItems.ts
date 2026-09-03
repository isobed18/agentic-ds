/** What the notification bell has to say, derived from what the server reports.
 *
 * Kept out of the component because these are rules about *which* events are
 * worth a line and where each one points -- decisions that are easy to state
 * and easy to break, and invisible in a rendered snapshot.
 */
import type { HomeOutput, RunSummary } from "../lib/api";
import { t } from "../lib/i18n";
import { reasonLabel } from "../lib/status";
import { runHref } from "./runDeepLink";

export interface NotificationItem {
  id: string;
  tone: "stop" | "warn" | "ok";
  title: string;
  detail: string;
  /** Where clicking the row goes before the owning project is resolved.
   *
   * Correct as-is for an output row -- `outputHref` already has the project
   * from the home read model. For a run row it is only the degrade path
   * (#292): the real destination needs `/api/projects`, which `runId` and
   * `automationId` below exist to make possible on click, not on every poll.
   */
  href: string;
  runId?: string;
  automationId?: string | null;
  at?: string;
}

/** The workspace URL for an artifact a run deposited.
 *
 * Models and reports are project-owned outputs, so the link has to name the
 * project — and the automation when one is known, because that is the level
 * where the Models and Reports tabs actually live. A stray output whose owner
 * could not be resolved still gets a link rather than a dead row.
 */
export function outputHref(output: HomeOutput): string {
  const view = output.kind === "model" ? "models" : "reports";
  if (!output.project_id) return "/projects";
  const params = new URLSearchParams({ project: output.project_id });
  if (output.automation_id) params.set("automation", output.automation_id);
  params.set("view", view);
  return `/projects?${params.toString()}`;
}

/**
 * The panel's items, most recent first.
 *
 * `outputs` is the home read model's recent list, which already carries the
 * owning project and automation for every model and report — so no second
 * lookup is needed to build a link that lands somewhere.
 *
 * A completed run that produced artifacts is announced by those artifacts
 * rather than by a bare "Run finished": "the report is ready" is the thing the
 * person was waiting for, and two rows for one event is how a panel becomes
 * noise. A completed run that produced nothing still gets its own line.
 */
export function itemsFrom(runs: RunSummary[], outputs: HomeOutput[] = []): NotificationItem[] {
  const items: NotificationItem[] = [];
  const producedOutput = new Set(outputs.map((output) => output.run_id));

  for (const run of runs) {
    const label = run.label ?? run.dataset ?? run.run_id;
    // Unresolved until click time (#292); `runHref(null, …)` degrades to the
    // project library rather than the broken run-only URL this used to be.
    const href = runHref(null, run.automation_id, run.run_id);
    if (run.pending_question) {
      items.push({
        // Keyed by stage and attempt so a second question on the same run is a
        // new notification rather than a silent overwrite.
        id: `${run.run_id}:ask:${run.pending_question.stage_id}:${run.pending_question.attempt}`,
        tone: "stop",
        title: t("Waiting for you"),
        detail: `${label} — ${reasonLabel(run.pending_question.reason_code)}`,
        href,
        runId: run.run_id,
        automationId: run.automation_id,
        at: run.last_activity,
      });
    } else if (run.status === "completed") {
      if (producedOutput.has(run.run_id)) continue;
      items.push({
        id: `${run.run_id}:done`,
        tone: "ok",
        title: t("Run finished"),
        detail: label,
        href,
        runId: run.run_id,
        automationId: run.automation_id,
        at: run.last_activity,
      });
    } else if (run.status === "failed") {
      items.push({
        id: `${run.run_id}:failed`,
        tone: "warn",
        title: t("Run stopped with an error"),
        detail: label,
        href,
        runId: run.run_id,
        automationId: run.automation_id,
        at: run.last_activity,
      });
    }
  }

  for (const output of outputs) {
    items.push({
      // Same convention as the run ids above, so the seen set keys uniformly.
      id: `${output.artifact_id}:${output.kind}`,
      tone: "ok",
      title: output.kind === "model" ? t("Model generated") : t("Report generated"),
      // The automation is what a person recognises; the artifact name alone
      // does not say which of their pipelines produced it.
      detail: output.automation_name ? `${output.label} — ${output.automation_name}` : output.label,
      href: outputHref(output),
      at: output.created_at,
    });
  }

  return items.sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
}
