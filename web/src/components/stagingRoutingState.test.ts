import { describe, expect, it } from "vitest";
import type { SourceProfile } from "../lib/api";
import { buildStagingRoutingState, routedFiles } from "./stagingRoutingState";

const profile: SourceProfile = {
  source_id: "mixed",
  privacy: "safe",
  source_files: [
    { name: "employees.csv", format: "csv", route: "structured", reason: "table", table_names: ["employees"] },
    { name: "metrics.parquet", format: "parquet", route: "structured", reason: "table", table_names: ["metrics"] },
    { name: "survey.pdf", format: "pdf", route: "documents", reason: "document", table_names: [] },
    { name: "report.pdf", format: "pdf", route: "documents", reason: "document", table_names: [] },
  ],
  tables: [],
  documents: [],
};

describe("staging source routing", () => {
  it("keeps every uploaded file attached to exactly one visible route", () => {
    expect(routedFiles(profile).map((file) => [file.name, file.route])).toEqual([
      ["employees.csv", "structured"],
      ["metrics.parquet", "structured"],
      ["survey.pdf", "documents"],
      ["report.pdf", "documents"],
    ]);
  });

  it("shows independent measured progress and selected document engine", () => {
    const state = buildStagingRoutingState(profile, {
      status: "staging",
      current_stage: "schema_discovery",
      events: [
        { event: "source_discovery_ready" },
        { event: "gate_decided", stage: "intake" },
        { event: "document_understanding_started", engine: "docling", ocr_mode: "auto" },
        { event: "document_file_ready", source_file: "survey.pdf", page_count: 8, table_candidates: 2, figure_candidates: 1, duration_seconds: 3.2, warnings: [] },
        { event: "document_file_started", source_file: "report.pdf" },
      ],
    }, null);

    expect(state.structured.map((step) => step.status)).toEqual([
      "complete", "complete", "running", "pending",
    ]);
    expect(state.documents.map((step) => step.status)).toEqual([
      "complete", "running", "pending", "pending",
    ]);
    expect(state.documents[1].detail).toBe("docling · OCR auto");
    expect(state.documentFiles.map((file) => [file.sourceFile, file.status])).toEqual([
      ["survey.pdf", "ready"],
      ["report.pdf", "running"],
    ]);
  });

  it("blocks synthesis and the proposal after a document failure", () => {
    const state = buildStagingRoutingState(profile, {
      status: "failed",
      current_stage: "document_understanding",
      error: "DocumentExtractionError: report.pdf failed validation",
      events: [
        { event: "source_discovery_ready" },
        { event: "document_understanding_started", engine: "docling", ocr_mode: "auto" },
        { event: "document_file_failed", source_file: "report.pdf", warnings: ["invalid candidate_id"] },
        { event: "document_understanding_failed", engine: "docling" },
      ],
    }, null);

    expect(state.documents.map((step) => step.status)).toEqual([
      "complete", "failed", "failed", "failed",
    ]);
    expect(state.synthesis).toBe("failed");
    expect(state.proposal).toBe("failed");
    expect(state.error).toContain("report.pdf");
  });

  it("requires a real persisted plan before marking synthesis ready", () => {
    const state = buildStagingRoutingState(profile, {
      status: "staged",
      events: [
        { event: "source_discovery_ready" },
        { event: "document_understanding_ready" },
        { event: "staging_analysis_started" },
        { event: "staging_analysis_ready" },
      ],
    }, {
      artifact_id: "workspace", run_id: "run-1", source_id: "mixed",
      source_fingerprint: "fingerprint", intake_artifact_ids: [], schema_artifact_ids: [],
      cache_reused: false, relationship_explanations: [], reports: [],
      pipeline_layout: { version: "1", nodes: [], collapsed_branches: [] },
      component_outputs: [], document_extractions: [], chat_history: [], recommended_plan: null,
      planner_error: "TypeError: invalid planner output",
    });

    expect(state.synthesis).toBe("failed");
    expect(state.proposal).toBe("failed");
    expect(state.documents.at(-1)?.status).toBe("failed");
    expect(state.error).toContain("invalid planner output");
  });
});
