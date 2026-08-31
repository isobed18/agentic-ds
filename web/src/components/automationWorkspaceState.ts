import type { AutomationContents, AutomationDefinition, RunSummary, SourceProfile, StagingWorkspace } from "../lib/api";
import type { Language } from "../lib/i18n";
import type { ProjectView } from "../pages/ProjectWorkspace";
import { isRunActive } from "../lib/status";

/** The tabs one automation can show. */
export type WorkspaceView = "editor" | "data" | "executions" | "models" | "reports";

/**
 * Which tabs a project offers, given what it actually contains (#80, #111).
 *
 * Editor and Executions are always present -- the flow and its run history are
 * the project. Data appears only once a source is bound; Models and Reports
 * appear only when the project has produced them. The requirement is explicit
 * that absence is the correct answer, not an empty section to apologise for, so
 * a project that trained no model simply has no Models tab.
 */
export function availableProjectViews(_contents: AutomationContents | null): WorkspaceView[] {
  // #157/#165: selected data, runs, models, and reports remain real pages even
  // when empty. Hiding a page made automation scope impossible to understand.
  return ["editor", "data", "executions", "models", "reports"];
}

/**
 * The live run, if any, each automation currently has in progress (#88).
 *
 * The library card showed only a static Saved/Draft badge and an execution
 * *count*; whether a project had a run actually in progress was invisible until
 * you opened it. /api/runs already reports every run's live status and its
 * `automation_id`, so join the two here. `isRunActive` is the same
 * queued/staging/running/resuming set the rest of the app polls on;
 * `awaiting_human`, `completed` and `failed` are not "in progress" and are left
 * out. When one automation has several live runs the most recent wins, so the
 * card's Stop action targets the run the person would actually mean.
 */
export function activeRunByAutomation(runs: RunSummary[]): Map<string, RunSummary> {
  const active = new Map<string, RunSummary>();
  for (const run of runs) {
    if (!run.automation_id || !isRunActive(run.status)) continue;
    const current = active.get(run.automation_id);
    if (!current || (run.last_activity ?? "") > (current.last_activity ?? "")) {
      active.set(run.automation_id, run);
    }
  }
  return active;
}

/**
 * Whether a data project was created but never actually used, so leaving the
 * editor should discard it rather than leave clutter behind (#83).
 *
 * "+ New data project" persists a backend record the instant it is clicked,
 * before any file is chosen. One still carrying no source and no run when the
 * person navigates away was never started and never will be -- indistinguishable
 * in the library from a real "Untitled automation" except that it is abandoned.
 * A `saved`/`error` project has real content or a run behind it and is kept.
 */
export function isAbandonedDraft(
  automation: AutomationDefinition | null,
  sourceId: string,
): boolean {
  return Boolean(
    automation
    && automation.status === "draft"
    && !sourceId
    && !automation.source_id
    && automation.execution_ids.length === 0,
  );
}

export type AutomationView = "empty" | "source" | "understanding" | "proposal" | "guided_pipeline" | "workflow";

/**
 * Which run the Executions panel should show. #68: a deep-link from
 * /experiments names a specific run (`?run=<id>`), but the panel used to seed
 * its selection from `executions[0]` and ignore the URL, so clicking any run
 * other than the most recent still opened the most recent. Honour the named run
 * when it is in the list; fall back to the most recent only when it is not.
 */
export function preferredExecution(
  executions: RunSummary[],
  preferredRunId: string | null,
): RunSummary | null {
  if (preferredRunId) {
    const match = executions.find((item) => item.run_id === preferredRunId);
    if (match) return match;
  }
  return executions[0] ?? null;
}

export function automationView(input: {
  sourceId: string;
  runId: string | null;
  runStatus: string | null;
  workspace: StagingWorkspace | null;
  advancedGraph: boolean;
}): AutomationView {
  // "empty" means nothing has ever been started here, and it renders the
  // upload landing screen. A run that has produced a workspace is proof that
  // something was started, so the two cannot both be true.
  //
  // They were: `applyWorkspace` fires `refreshAutomation()` without awaiting
  // it, and that call ends in `setSourceId(record.source_id ?? "")`. Any moment
  // where the refetched record comes back without a source -- a stale response
  // landing after a newer one, a record read mid-write -- emptied `sourceId`
  // and sent the screen back to "upload a file", one click after accepting a
  // plan. Indistinguishable from having lost the project (#49).
  //
  // The run wins because it is the stronger evidence: a source id is a pointer
  // that can go missing, a workspace is a produced artifact that cannot.
  if (!input.sourceId && !(input.runId && input.workspace)) return "empty";
  if (!input.runId) return "source";
  if (input.advancedGraph) return "workflow";
  // An accepted plan is a decision, not a run state, so it is read before the
  // status list below (#191). It used to be read after, which meant any answer
  // that put the run back into `queued`, `staging`, `failed` or `interrupted`
  // -- a gate answered "send back for rework" being the reported one -- dropped
  // the workspace out of the guided ML pipeline and back onto the staging
  // canvas, even though the plan it had accepted was unchanged. The run's
  // trouble belongs on the pipeline that is running, not on the screen for
  // deciding whether to have one.
  const accepted = input.workspace?.recommended_plan;
  if (accepted && (accepted.status === "accepted" || accepted.accepted)) return "guided_pipeline";
  if (
    !input.workspace
    || ["queued", "staging", "failed", "interrupted"].includes(input.runStatus ?? "")
  ) {
    return "understanding";
  }
  const plan = input.workspace.recommended_plan;
  if (!plan) return "understanding";
  return "proposal";
}

export function sourceCounts(profile: SourceProfile | null): {
  files: number;
  pdfs: number;
  structured: number;
  pages: number;
  rows: number;
} {
  const documents = profile?.documents ?? [];
  const tables = profile?.tables ?? [];
  const routed = profile?.source_files ?? [];
  const structuredFiles = routed.filter((file) => file.route === "structured").length;
  const documentFiles = routed.filter((file) => file.route === "documents").length;
  return {
    files: routed.length || documents.length + tables.length,
    pdfs: routed.length ? documentFiles : documents.length,
    structured: routed.length ? structuredFiles : tables.length,
    pages: documents.reduce((sum, document) => sum + document.pages, 0),
    rows: tables.reduce((sum, table) => sum + table.rows, 0),
  };
}

export function visibleWorkflowSteps(workspace: StagingWorkspace, language: Language): string[] {
  const hidden = new Set([
    "data.upload",
    "data.profile_tables",
    "data.find_relationships",
    "document.extract",
    "document.review_tables",
  ]);
  const blueprint = workspace.pipeline_blueprint;
  if (!blueprint) return [];
  return blueprint.components
    .filter((component) => component.enabled && !hidden.has(component.catalog_id ?? ""))
    .filter((component) => component.kind !== "template")
    .map((component) => component.title[language]);
}

/**
 * The project tab a URL names, defaulting to the overview (#157).
 */
export function projectView(view: string | null): ProjectView {
  if (view === "data" || view === "automations" || view === "models" || view === "reports") return view;
  return "overview";
}

/**
 * Where "← Project overview" should actually land, and how it learns that (#199).
 *
 * `view` means two different things at the two levels -- the project's tab and
 * the automation's tab -- so the automation URL cannot just keep carrying the
 * project's `view`. The origin travels under its own key, `from`, and the back
 * button turns it back into `view` on the way out. Automations are opened from
 * the Automations tab, so landing on Overview every time meant a click back into
 * Automations on every single trip.
 *
 * `overview` is the project screen's own fallback, so it is omitted rather than
 * written out as a redundant parameter -- an automation opened from the overview
 * produces the same short URL it always did.
 */
export function automationOrigin(view: string | null): ProjectView | null {
  const origin = projectView(view);
  return origin === "overview" ? null : origin;
}

/** The automation URL, carrying the project tab it was opened from (#199). */
export function automationParams(
  projectId: string,
  automationId: string,
  origin: ProjectView | null,
): Record<string, string> {
  return { project: projectId, automation: automationId, ...(origin && origin !== "overview" ? { from: origin } : {}) };
}

/** The project URL to return to, restoring the tab the automation was opened from (#199). */
export function projectReturnParams(projectId: string, origin: ProjectView | null): Record<string, string> {
  return { project: projectId, ...(origin && origin !== "overview" ? { view: origin } : {}) };
}
