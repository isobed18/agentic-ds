import { describe, expect, it } from "vitest";

import type { AutomationContents, AutomationDefinition, RunSummary, StagingWorkspace } from "../lib/api";
import { activeRunByAutomation, automationOrigin, automationParams, automationView, availableProjectViews, isAbandonedDraft, preferredExecution, projectReturnParams, projectView, sourceCounts, visibleWorkflowSteps } from "./automationWorkspaceState";

describe("which automation tabs to show (#157, #165)", () => {
  const contents = (over: Partial<AutomationContents> = {}): AutomationContents => ({
    project: {} as AutomationContents["project"],
    automation: {} as AutomationDefinition,
    data: [],
    executions: [],
    models: [],
    reports: [],
    ...over,
  });

  it("keeps every automation-owned page visible even before outputs exist", () => {
    const expected = [
      "editor",
      "data",
      "executions",
      "models",
      "reports",
    ];
    expect(availableProjectViews(null)).toEqual(expected);
    expect(availableProjectViews(contents())).toEqual(expected);
  });
});

describe("discarding an abandoned data project (#83)", () => {
  const automation = (over: Partial<AutomationDefinition> = {}) => ({
    schema_version: "1", automation_id: "automation-000000000001", name: "Untitled automation",
    status: "draft", revision: 1, source_id: null, pipeline_blueprint: null,
    pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
    execution_ids: [], created_at: "", updated_at: "",
    ...over,
  }) as unknown as AutomationDefinition;

  it("discards a draft that never got a source or a run", () => {
    expect(isAbandonedDraft(automation(), "")).toBe(true);
  });

  it("keeps one where a source was chosen (locally or on the record)", () => {
    expect(isAbandonedDraft(automation(), "upload:abc")).toBe(false);
    expect(isAbandonedDraft(automation({ source_id: "upload:abc" }), "")).toBe(false);
  });

  it("keeps one that has already been executed", () => {
    expect(isAbandonedDraft(automation({ execution_ids: ["run-a1b2c3d4"] as unknown as AutomationDefinition["execution_ids"] }), "")).toBe(false);
  });

  it("keeps a saved or errored project with real content behind it", () => {
    expect(isAbandonedDraft(automation({ status: "saved" }), "")).toBe(false);
    expect(isAbandonedDraft(automation({ status: "error" }), "")).toBe(false);
  });

  it("never touches a record that has not loaded yet", () => {
    expect(isAbandonedDraft(null, "")).toBe(false);
  });
});

describe("automation progressive disclosure", () => {
  it("never shows a graph before data exists", () => {
    expect(automationView({
      sourceId: "", runId: null, runStatus: null, workspace: null, advancedGraph: false,
    })).toBe("empty");
  });

  it("shows understanding instead of internal staging nodes", () => {
    expect(automationView({
      sourceId: "pdfs", runId: "run-1", runStatus: "staging", workspace: null,
      advancedGraph: false,
    })).toBe("understanding");
  });

  it("keeps a failed staging run on its inspectable understanding canvas", () => {
    expect(automationView({
      sourceId: "pdfs", runId: "run-1", runStatus: "failed", workspace: null,
      advancedGraph: false,
    })).toBe("understanding");
  });

  it("does not claim a staged workspace has a proposal when no plan exists", () => {
    expect(automationView({
      sourceId: "pdfs", runId: "run-1", runStatus: "staged",
      workspace: {
        artifact_id: "workspace", run_id: "run-1", source_id: "pdfs",
        source_fingerprint: "fingerprint", intake_artifact_ids: [], schema_artifact_ids: [],
        cache_reused: false, relationship_explanations: [], reports: [],
        pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
        component_outputs: [], document_extractions: [], chat_history: [],
        recommended_plan: null, planner_error: "Claude output decoding failed",
      },
      advancedGraph: false,
    })).toBe("understanding");
  });

  it("does not treat a deferred plan as a proposal to accept (#316)", () => {
    // Every planner reply persists a plan, so the record's mere existence used
    // to mean "the ML flow is ready" -- asking a question about the raw files
    // unlocked the run controls, including when the planner's own reply said to
    // review the extracted PDF tables first.
    const base = {
      artifact_id: "workspace",
      run_id: "run-1",
      source_id: "source",
      source_fingerprint: "fingerprint",
      intake_artifact_ids: [],
      schema_artifact_ids: [],
      cache_reused: false,
      relationship_explanations: [],
      reports: [],
      pipeline_layout: { version: "1" as const, nodes: [], collapsed_branches: [] },
      component_outputs: [],
      document_extractions: [],
      chat_history: [],
      recommended_plan: {
        proposal_id: "plan-1",
        status: "proposed" as const,
        mode: "fully_auto" as const,
        configuration: {},
        stage_directives: {},
        checkpoint_stages: [],
        auto_proceed_stages: [],
        max_retries_by_stage: {},
        rationale: [],
        accepted: false,
      },
    };
    const view = (recommendation: "create_pipeline" | "defer_pipeline" | "no_pipeline") =>
      automationView({
        sourceId: "source", runId: "run-1", runStatus: "staged",
        workspace: { ...base, recommended_plan: { ...base.recommended_plan, pipeline_recommendation: recommendation } },
        advancedGraph: false,
      });

    expect(view("defer_pipeline")).toBe("understanding");
    expect(view("no_pipeline")).toBe("understanding");
    expect(view("create_pipeline")).toBe("proposal");
  });

  it("still shows a proposal recorded before the recommendation field existed", () => {
    // Absence is an old artifact, not a deferral; those were all proposals.
    expect(automationView({
      sourceId: "source", runId: "run-1", runStatus: "staged",
      workspace: {
        artifact_id: "workspace", run_id: "run-1", source_id: "source",
        source_fingerprint: "fingerprint", intake_artifact_ids: [], schema_artifact_ids: [],
        cache_reused: false, relationship_explanations: [], reports: [],
        pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
        component_outputs: [], document_extractions: [], chat_history: [],
        recommended_plan: {
          proposal_id: "plan-old", status: "proposed", mode: "fully_auto",
          configuration: {}, stage_directives: {}, checkpoint_stages: [],
          auto_proceed_stages: [], max_retries_by_stage: {}, rationale: [], accepted: false,
        },
      },
      advancedGraph: false,
    })).toBe("proposal");
  });

  it("keeps a deferred plan out of the way of an accepted one", () => {
    // Acceptance is a human decision and outranks the recommendation: a plan
    // somebody accepted stays in the guided pipeline whatever it recommends.
    expect(automationView({
      sourceId: "source", runId: "run-1", runStatus: "staged",
      workspace: {
        artifact_id: "workspace", run_id: "run-1", source_id: "source",
        source_fingerprint: "fingerprint", intake_artifact_ids: [], schema_artifact_ids: [],
        cache_reused: false, relationship_explanations: [], reports: [],
        pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
        component_outputs: [], document_extractions: [], chat_history: [],
        recommended_plan: {
          proposal_id: "plan-1", status: "accepted", mode: "fully_auto",
          pipeline_recommendation: "defer_pipeline",
          configuration: {}, stage_directives: {}, checkpoint_stages: [],
          auto_proceed_stages: [], max_retries_by_stage: {}, rationale: [], accepted: true,
        },
      },
      advancedGraph: false,
    })).toBe("guided_pipeline");
  });

  it("keeps the accepted proposal in the guided pipeline unless advanced editing is requested", () => {
    const base = {
      artifact_id: "workspace",
      run_id: "run-1",
      source_id: "source",
      source_fingerprint: "fingerprint",
      intake_artifact_ids: [],
      schema_artifact_ids: [],
      cache_reused: false,
      relationship_explanations: [],
      reports: [],
      pipeline_layout: { version: "1" as const, nodes: [], collapsed_branches: [] },
      component_outputs: [],
      document_extractions: [],
      chat_history: [],
      recommended_plan: {
        proposal_id: "plan-1",
        status: "proposed" as const,
        mode: "fully_auto" as const,
        configuration: {},
        stage_directives: {},
        checkpoint_stages: [],
        auto_proceed_stages: [],
        max_retries_by_stage: {},
        rationale: [],
        accepted: false,
      },
    };
    expect(automationView({
      sourceId: "source", runId: "run-1", runStatus: "staged", workspace: base,
      advancedGraph: false,
    })).toBe("proposal");
    expect(automationView({
      sourceId: "source", runId: "run-1", runStatus: "staged",
      workspace: { ...base, recommended_plan: { ...base.recommended_plan, status: "accepted", accepted: true } },
      advancedGraph: false,
    })).toBe("guided_pipeline");
    expect(automationView({
      sourceId: "source", runId: "run-1", runStatus: "staged",
      workspace: { ...base, recommended_plan: { ...base.recommended_plan, status: "accepted", accepted: true } },
      advancedGraph: true,
    })).toBe("workflow");
  });

  /** A staged workspace whose plan is in the given state. */
  function staged(planOver: Record<string, unknown>) {
    return {
      artifact_id: "workspace", run_id: "run-1", source_id: "source",
      source_fingerprint: "fingerprint", intake_artifact_ids: [], schema_artifact_ids: [],
      cache_reused: false, relationship_explanations: [], reports: [],
      pipeline_layout: { version: "1" as const, nodes: [], collapsed_branches: [] },
      component_outputs: [], document_extractions: [], chat_history: [],
      recommended_plan: {
        proposal_id: "plan-1", status: "proposed", mode: "fully_auto" as const,
        configuration: {}, stage_directives: {}, checkpoint_stages: [],
        auto_proceed_stages: [], max_retries_by_stage: {}, rationale: [], accepted: false,
        ...planOver,
      },
    } as unknown as Parameters<typeof automationView>[0]["workspace"];
  }

  it("keeps an accepted plan on the guided pipeline whatever the run is doing (#191)", () => {
    // The other half of the gate report: answering "send back for rework" put
    // the run into a pre-staging status, and the status list was read BEFORE
    // the plan, so the workspace left the guided ML pipeline for the staging
    // canvas -- with the accepted plan completely unchanged. The reporter saw
    // their ML workflow become "the regular one".
    const accepted = staged({ status: "accepted", accepted: true });
    for (const runStatus of ["queued", "staging", "failed", "interrupted", "running", "awaiting_human"]) {
      expect(automationView({
        sourceId: "source", runId: "run-1", runStatus, workspace: accepted, advancedGraph: false,
      }), runStatus).toBe("guided_pipeline");
    }
  });

  it("still drops an unaccepted plan back to understanding on those statuses", () => {
    // The paired half: the status list is right for everything before
    // acceptance, and moving the plan check ahead of it must not disable it.
    const proposed = staged({});
    for (const runStatus of ["queued", "staging", "failed", "interrupted"]) {
      expect(automationView({
        sourceId: "source", runId: "run-1", runStatus, workspace: proposed, advancedGraph: false,
      }), runStatus).toBe("understanding");
    }
  });

  it("summarizes four PDFs without inventing structured data", () => {
    const documents = Array.from({ length: 4 }, (_, index) => ({
      name: `report-${index}.pdf`, format: "pdf" as const, pages: 10 + index,
      text_pages: 10, text_characters: 100, image_count: 0,
      understanding_status: "text_ready" as const, training_status: "not_extracted" as const,
      issues: [],
    }));
    expect(sourceCounts({ source_id: "pdfs", tables: [], documents, privacy: "safe" })).toEqual({
      files: 4, pdfs: 4, structured: 0, pages: 46, rows: 0,
    });
  });
});

describe("a run outranks a missing source", () => {
  /**
   * The regression this exists for (#49): accepting a plan sent the screen back
   * to the upload landing page, one click after the plan was approved. Nothing
   * errored, so it read as the project having been lost.
   *
   * `applyWorkspace` fires `refreshAutomation()` without awaiting it, and that
   * ends in `setSourceId(record.source_id ?? "")`. Any moment where the record
   * comes back without a source emptied `sourceId`, and "empty" used to win
   * ahead of every other check.
   */
  const accepted = {
    run_id: "run-1",
    recommended_plan: { status: "accepted", accepted: true },
  } as unknown as StagingWorkspace;

  it("stays on the pipeline when the source id vanishes mid-flow", () => {
    expect(
      automationView({
        sourceId: "",
        runId: "run-1",
        runStatus: "succeeded",
        workspace: accepted,
        advancedGraph: false,
      }),
    ).toBe("guided_pipeline");
  });

  it("still shows the upload screen when nothing has been started", () => {
    // The empty state has to keep working; this is the case it is for.
    expect(
      automationView({
        sourceId: "",
        runId: null,
        runStatus: null,
        workspace: null,
        advancedGraph: false,
      }),
    ).toBe("empty");
  });

  it("shows the upload screen for a run id with no workspace behind it", () => {
    // A run id alone is a pointer that can dangle. Only a produced workspace
    // proves work happened.
    expect(
      automationView({
        sourceId: "",
        runId: "run-1",
        runStatus: "queued",
        workspace: null,
        advancedGraph: false,
      }),
    ).toBe("empty");
  });
});

describe("which execution a deep-link selects", () => {
  // #68: /experiments links to /automation?...&run=<id>. The Executions panel
  // used to seed from executions[0] and ignore the URL, so any run but the most
  // recent opened the most recent instead.
  const runs = [
    { run_id: "run-newest" },
    { run_id: "run-middle" },
    { run_id: "run-oldest" },
  ] as unknown as RunSummary[];

  it("selects the run named in the URL, not the most recent", () => {
    expect(preferredExecution(runs, "run-oldest")?.run_id).toBe("run-oldest");
  });

  it("falls back to the most recent when no run is named", () => {
    expect(preferredExecution(runs, null)?.run_id).toBe("run-newest");
  });

  it("falls back to the most recent when the named run is not in the list", () => {
    expect(preferredExecution(runs, "run-deleted")?.run_id).toBe("run-newest");
  });

  it("returns null when there are no executions", () => {
    expect(preferredExecution([], "run-oldest")).toBeNull();
  });
});

describe("which automations have a live run (#88)", () => {
  const run = (over: Partial<RunSummary>): RunSummary =>
    ({ run_id: "run", status: "completed", ...over }) as RunSummary;

  it("indexes an in-progress run under its automation", () => {
    const active = activeRunByAutomation([
      run({ run_id: "r1", status: "running", automation_id: "a1", last_activity: "2026-01-01T00:00:00Z" }),
    ]);
    expect(active.get("a1")?.run_id).toBe("r1");
  });

  it("ignores runs that are not in progress", () => {
    // completed, failed and awaiting_human are not "running" -- a card should
    // not offer to stop a run that has already finished or is parked on a gate.
    const active = activeRunByAutomation([
      run({ status: "completed", automation_id: "a1" }),
      run({ status: "failed", automation_id: "a2" }),
      run({ status: "awaiting_human", automation_id: "a3" }),
    ]);
    expect(active.size).toBe(0);
  });

  it("ignores runs with no automation behind them", () => {
    expect(activeRunByAutomation([run({ status: "running", automation_id: null })]).size).toBe(0);
  });

  it("keeps the most recent live run when an automation has several", () => {
    const active = activeRunByAutomation([
      run({ run_id: "old", status: "running", automation_id: "a1", last_activity: "2026-01-01T00:00:00Z" }),
      run({ run_id: "new", status: "staging", automation_id: "a1", last_activity: "2026-06-01T00:00:00Z" }),
    ]);
    expect(active.get("a1")?.run_id).toBe("new");
  });
});

describe("proposed workflow step labels", () => {
  const workspace = {
    pipeline_blueprint: {
      components: [
        {
          id: "training",
          catalog_id: "ml.train",
          enabled: true,
          kind: "agent",
          title: { en: "Train models", tr: "Modelleri eğit" },
        },
      ],
    },
  } as unknown as StagingWorkspace;

  it("uses the selected language instead of always reading English", () => {
    // The proposal panel previously hardcoded title.en, leaving every numbered
    // step in English while the rest of the panel was Turkish (#61).
    expect(visibleWorkflowSteps(workspace, "tr")).toEqual(["Modelleri eğit"]);
    expect(visibleWorkflowSteps(workspace, "en")).toEqual(["Train models"]);
  });
});

describe("returning to the tab an automation was opened from (#199)", () => {
  it("carries the originating project tab into the automation URL", () => {
    expect(automationParams("project-1", "automation-1", "automations")).toEqual({
      project: "project-1",
      automation: "automation-1",
      from: "automations",
    });
  });

  it("restores that tab on the way back instead of the overview", () => {
    expect(projectReturnParams("project-1", "automations")).toEqual({ project: "project-1", view: "automations" });
    expect(projectReturnParams("project-1", "reports")).toEqual({ project: "project-1", view: "reports" });
  });

  it("omits the origin for the overview, which is already the fallback", () => {
    expect(automationOrigin(null)).toBeNull();
    expect(automationOrigin("overview")).toBeNull();
    expect(automationOrigin("nonsense")).toBeNull();
    expect(automationParams("project-1", "automation-1", null)).toEqual({ project: "project-1", automation: "automation-1" });
    expect(projectReturnParams("project-1", null)).toEqual({ project: "project-1" });
  });

  it("reads the origin out of a project tab name and rejects anything else", () => {
    expect(automationOrigin("models")).toBe("models");
    expect(projectView("data")).toBe("data");
    expect(projectView("editor")).toBe("overview");
  });
});
