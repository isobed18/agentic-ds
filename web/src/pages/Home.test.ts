/**
 * The home page is the only browsing surface after #111, so a result there has
 * to lead back to the exact project and run that produced it. These pin the
 * link composition: a project with runs opens on its latest run, and a produced
 * output opens its owning project's run -- an output with no project has nowhere
 * to go rather than a link to a project that does not exist.
 */
import { describe, expect, it } from "vitest";
import { outputHref, projectHref } from "./Catalog";
import type { HomeOutput, HomeProject } from "../lib/api";

const project = (over: Partial<HomeProject>): HomeProject => ({
  project_id: "automation-abc123def456",
  name: "Retention",
  status: "saved",
  source_id: "upload:xyz",
  execution_count: 0,
  updated_at: "2026-01-01T00:00:00+00:00",
  state: "idle",
  needs_attention: false,
  latest_run_id: null,
  ...over,
});

describe("home links", () => {
  it("opens a project with no runs on its workspace", () => {
    expect(projectHref(project({}))).toBe("/automation?automation=automation-abc123def456");
  });

  it("opens a project with runs on its latest run", () => {
    expect(projectHref(project({ latest_run_id: "run-9" }))).toBe(
      "/automation?automation=automation-abc123def456&view=runs&run=run-9",
    );
  });

  it("links a produced output to its owning project's run", () => {
    const output: HomeOutput = {
      kind: "report",
      project_id: "automation-abc123def456",
      project_name: "Retention",
      run_id: "run-9",
      artifact_id: "a".repeat(64),
      label: "Holdout summary",
      created_at: "2026-01-01T00:00:00+00:00",
    };
    expect(outputHref(output)).toBe(
      "/automation?automation=automation-abc123def456&view=runs&run=run-9",
    );
  });

  it("gives an ownerless output no destination rather than a broken one", () => {
    const orphan: HomeOutput = {
      kind: "model",
      project_id: null,
      project_name: null,
      run_id: "run-orphan",
      artifact_id: "b".repeat(64),
      label: "Trained model",
      created_at: "2026-01-01T00:00:00+00:00",
    };
    expect(outputHref(orphan)).toBeNull();
  });
});
