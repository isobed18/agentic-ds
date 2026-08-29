import type { RunSummary, SourceProfile, StagingWorkspace } from "../lib/api";
import type { Language } from "../lib/i18n";

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
  if (
    !input.workspace
    || ["queued", "staging", "failed", "interrupted"].includes(input.runStatus ?? "")
  ) {
    return "understanding";
  }
  const plan = input.workspace.recommended_plan;
  if (!plan) return "understanding";
  if (plan && (plan.status === "accepted" || plan.accepted)) return "guided_pipeline";
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
