import { describe, expect, it } from "vitest";

import type { StagingWorkspace } from "../lib/api";
import { automationView, sourceCounts, visibleWorkflowSteps } from "./automationWorkspaceState";

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
