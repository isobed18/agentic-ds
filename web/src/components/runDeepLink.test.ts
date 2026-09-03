import { describe, expect, it } from "vitest";

import type { ProjectDefinition } from "../lib/api";
import { projectOfAutomation, runHref } from "./runDeepLink";

const project = (over: Partial<ProjectDefinition> = {}): ProjectDefinition => ({
  schema_version: "1",
  project_id: "project-aaaaaaaaaaaa",
  name: "Retention",
  revision: 1,
  owner: "someone",
  visibility: "private",
  mine: true,
  source_ids: [],
  automation_ids: ["automation-111111111111"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...over,
});

describe("linking to a run", () => {
  it("names the project, the automation, the tab and the run", () => {
    // AutomationWorkspace reads `project` first and renders the project library
    // without it, so every one of these is load-bearing.
    expect(runHref("project-aaaaaaaaaaaa", "automation-111111111111", "run-7")).toBe(
      "/projects?project=project-aaaaaaaaaaaa&automation=automation-111111111111&view=executions&run=run-7",
    );
  });

  it("resolves the owning project from the automation", () => {
    const projects = [project({ project_id: "project-b", automation_ids: ["automation-9"] }), project()];
    expect(projectOfAutomation(projects, "automation-111111111111")).toBe("project-aaaaaaaaaaaa");
    expect(projectOfAutomation(projects, "automation-9")).toBe("project-b");
  });

  it("resolves to nothing for a run with no automation behind it", () => {
    expect(projectOfAutomation([project()], null)).toBeNull();
    expect(projectOfAutomation([project()], undefined)).toBeNull();
  });

  it("resolves to nothing when the project is not one this viewer may see", () => {
    // /api/projects is viewer-filtered, so an unreadable project is simply
    // absent. Guessing a link would 404 on arrival.
    expect(projectOfAutomation([], "automation-111111111111")).toBeNull();
  });

  it("falls back to the project library rather than a URL that goes nowhere", () => {
    expect(runHref(null, "automation-111111111111", "run-7")).toBe("/projects");
    expect(runHref("project-aaaaaaaaaaaa", null, "run-7")).toBe("/projects");
  });

  it("escapes ids rather than pasting them into the query string", () => {
    expect(runHref("project-a", "automation-1", "run 7&x=1")).toContain("run=run+7%26x%3D1");
  });
});
