import { describe, expect, it } from "vitest";

import AUTOMATION_SOURCE from "./AutomationWorkspace.tsx?raw";
import PROJECT_SOURCE from "./ProjectWorkspace.tsx?raw";

describe("the project-first journey (#157, #165)", () => {
  it("routes project, then automation, as two distinct levels", () => {
    expect(AUTOMATION_SOURCE).toContain('params.get("project")');
    expect(AUTOMATION_SOURCE).toContain("<ProjectWorkspace");
    expect(AUTOMATION_SOURCE).toContain("<AutomationEditor projectId={projectId}");
  });

  it("opens a project on its overview instead of a graph", () => {
    expect(PROJECT_SOURCE).toContain('type ProjectView = "overview"');
    expect(PROJECT_SOURCE).toContain('view === "overview" && <ProjectOverview');
    expect(PROJECT_SOURCE).toContain('t("Project overview")');
  });

  it("uploads at project scope and shows project files", () => {
    expect(PROJECT_SOURCE).toContain("api.addProjectSource(projectId, group)");
    expect(PROJECT_SOURCE).toContain("source.files.map");
    expect(PROJECT_SOURCE).toContain('t("Choose previously uploaded data")');
    expect(PROJECT_SOURCE).toContain("disabled={uploading}");
    // #172: progress is rendered before the add-file action panel.
    expect(PROJECT_SOURCE.indexOf("{progress &&")).toBeLessThan(PROJECT_SOURCE.indexOf('t("Upload new files")'));
  });

  it("lists many automations and creates them through their parent", () => {
    expect(PROJECT_SOURCE).toContain("api.createProjectAutomation(projectId");
    expect(PROJECT_SOURCE).toContain("automations.map");
    expect(PROJECT_SOURCE).toContain("onOpen(created.automation_id)");
  });

  it("keeps project Models and Reports pages labelled by automation", () => {
    expect(PROJECT_SOURCE).toContain('t("Project models")');
    expect(PROJECT_SOURCE).toContain('t("Project reports")');
    expect(PROJECT_SOURCE).toContain('t("From {automation}", { automation: model.automation_name })');
    expect(PROJECT_SOURCE).toContain('t("From {automation}", { automation: report.automation_name })');
  });

  it("removes the automation toolbar's uploaded-data picker and uploader", () => {
    const header = AUTOMATION_SOURCE.match(/<header className="relative flex h-\[58px\][\s\S]*?<\/header>/)?.[0] ?? "";
    expect(header).toContain("<LanguagePicker");
    expect(header).not.toContain("<select");
    expect(header).not.toContain('title={t("Add files")}');
    expect(AUTOMATION_SOURCE).toContain("<AutomationInputSelector");
  });

  it("returns to the project tab the automation was opened from (#199)", () => {
    // The back button must never rebuild the query from scratch again: doing so
    // dropped the tab and always landed on Overview.
    expect(AUTOMATION_SOURCE).toContain("onClick={() => setParams(projectReturnParams(projectId, origin))}");
    expect(AUTOMATION_SOURCE).toContain('const origin = automationOrigin(params.get("from"));');
    expect(AUTOMATION_SOURCE).toContain('onOpenAutomation={(id) => setParams(automationParams(projectId, id, automationOrigin(params.get("view"))))}');
    // Every rewrite of the automation's own URL keeps the origin, so a tab
    // switch or a started run cannot strip it before the person heads back.
    expect(AUTOMATION_SOURCE.match(/setParams\(\{[^}]*automation: automationId/g)).toBeNull();
  });

  it("uses automation-scoped output data and keeps the human gate reachable", () => {
    expect(AUTOMATION_SOURCE).toContain("api.automationContents(automationId)");
    expect(AUTOMATION_SOURCE).toContain("<ApprovalCard");
    expect(AUTOMATION_SOURCE).toContain("pendingQuestion?.human_prompt");
  });

  it("passes the selected source into the advanced planner (#190)", () => {
    expect(AUTOMATION_SOURCE).toContain("<PipelineBuilder runId={runId} sourceId={sourceId}");
  });
});
