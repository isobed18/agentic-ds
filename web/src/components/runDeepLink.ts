/** Where a link to one run has to point for the workspace to open it.
 *
 * `AutomationWorkspace` reads `project` first and renders the project library
 * when it is missing, ignoring `view` and `run` entirely. A URL that names only
 * the run therefore lands on the project list as if nothing had been clicked --
 * which is what every notification did (#292). The link needs the whole chain:
 * project, then automation, then the run inside it.
 */
import type { ProjectDefinition } from "../lib/api";

/**
 * The project that owns an automation, or null when it cannot be resolved.
 *
 * The automation record does not carry its project -- ownership lives on the
 * project, in `automation_ids` -- so the join has to happen from this side. A
 * project the viewer may not see is not in the list, and the answer is null
 * rather than a link that would 404 on arrival.
 */
export function projectOfAutomation(
  projects: ProjectDefinition[],
  automationId: string | null | undefined,
): string | null {
  if (!automationId) return null;
  const owner = projects.find((project) => project.automation_ids.includes(automationId));
  return owner?.project_id ?? null;
}

/**
 * The workspace URL for one run.
 *
 * `view=executions` rather than `view=runs`: the workspace accepts both, but
 * `executions` is the name the tab actually has. Without a resolvable project
 * there is nowhere specific to go, so the link is the project library -- the
 * same place the broken URL ended up, minus the parameters that pretended
 * otherwise.
 */
export function runHref(
  projectId: string | null,
  automationId: string | null | undefined,
  runId: string,
): string {
  if (!projectId || !automationId) return "/projects";
  const params = new URLSearchParams({
    project: projectId,
    automation: automationId,
    view: "executions",
    run: runId,
  });
  return `/projects?${params.toString()}`;
}
