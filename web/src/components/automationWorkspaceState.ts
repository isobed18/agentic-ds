import type { SourceProfile, StagingWorkspace } from "../lib/api";

export type AutomationView = "empty" | "source" | "understanding" | "proposal" | "guided_pipeline" | "workflow";

export function automationView(input: {
  sourceId: string;
  runId: string | null;
  runStatus: string | null;
  workspace: StagingWorkspace | null;
  advancedGraph: boolean;
}): AutomationView {
  if (!input.sourceId) return "empty";
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

export function visibleWorkflowSteps(workspace: StagingWorkspace): string[] {
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
    .map((component) => component.title.en);
}
