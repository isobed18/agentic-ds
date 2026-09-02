import { describe, expect, it } from "vitest";
import type { SourceProfile } from "../lib/api";
import { buildStagingRoutingState, pageOfFiles, routedFiles, type RoutedSourceFile } from "./stagingRoutingState";

const profile: SourceProfile = {
  source_id: "mixed",
  privacy: "safe",
  source_files: [
    { name: "employees.csv", format: "csv", route: "structured", reason: { en: "table", tr: "tablo" }, table_names: ["employees"], insight: { en: "42 rows · keyed table", tr: "42 satır · anahtarlı tablo" } },
    { name: "metrics.parquet", format: "parquet", route: "structured", reason: { en: "table", tr: "tablo" }, table_names: ["metrics"] },
    { name: "survey.pdf", format: "pdf", route: "documents", reason: { en: "document", tr: "belge" }, table_names: [] },
    { name: "report.pdf", format: "pdf", route: "documents", reason: { en: "document", tr: "belge" }, table_names: [] },
  ],
  tables: [],
  documents: [],
};

/**
 * A PDF that someone renamed to .csv. The route comes from the extension and
 * says "structured"; file detection measured the content and says "belge". Until the
 * two are wired together this disagreement is the only warning anyone gets,
 * and it was being dropped on the floor between the API and the screen (#103).
 */
const misnamed: SourceProfile = {
  source_id: "yalan",
  privacy: "safe",
  source_files: [
    {
      name: "musteri_listesi.csv",
      format: "csv",
      route: "structured",
      reason: { en: "A supported tabular format will be profiled deterministically.", tr: "A supported tabular format will be profiled deterministically." },
      table_names: ["musteri_listesi"],
      detected_flow: "belge",
      detection_deterministic: true,
      detection_evidence: "metin katmani: 1 sayfa, sayfa basina 104 karakter",
      detection_conflicts_with_extension: true,
    },
  ],
  tables: [],
  documents: [],
};

describe("staging source routing", () => {
  it("carries the measured type through to the screen", () => {
    const [file] = routedFiles(misnamed);
    expect(file.measuredFlow).toBe("belge");
    expect(file.contradictsExtension).toBe(true);
    expect(file.measuredEvidence).toContain("metin katmani");
    // The route itself is untouched: this release makes the measurement
    // visible, it does not yet let it decide.
    expect(file.route).toBe("structured");
  });

  it("leaves the measurement undefined when file detection is absent", () => {
    const [file] = routedFiles(profile);
    expect(file.measuredFlow).toBeUndefined();
    expect(file.contradictsExtension).toBeUndefined();
  });

  it("keeps every uploaded file attached to exactly one visible route", () => {
    expect(routedFiles(profile).map((file) => [file.name, file.route])).toEqual([
      ["employees.csv", "structured"],
      ["metrics.parquet", "structured"],
      ["survey.pdf", "documents"],
      ["report.pdf", "documents"],
    ]);
  });

  it("carries the measured per-file insight into the Intake card", () => {
    expect(routedFiles(profile)[0].insight).toEqual({
      en: "42 rows · keyed table",
      tr: "42 satır · anahtarlı tablo",
    });
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

  it("names all of the work that keeps schema discovery running", () => {
    // The regression this exists for (#43). A single table has no relationship
    // search to perform, but the schema stage stayed labelled "Find
    // relationships" during its sensitivity and comprehension model calls.
    const singleTable = {
      ...profile,
      source_files: profile.source_files?.filter((file) => file.name === "employees.csv"),
    };
    const state = buildStagingRoutingState(singleTable, {
      status: "staging",
      current_stage: "schema_discovery",
      events: [
        { event: "source_discovery_ready" },
        { event: "gate_decided", stage: "intake" },
      ],
    }, null);

    expect(state.structured[2]).toMatchObject({
      label: "Review schema and describe data",
      detail: "Includes relationship and sensitive-column checks.",
      status: "running",
    });
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

  it("surfaces an escalated schema stage instead of hanging at Explain", () => {
    const state = buildStagingRoutingState(profile, {
      status: "awaiting_human",
      current_stage: "schema_discovery",
      events: [
        { event: "source_discovery_ready" },
        { event: "gate_decided", stage: "intake", verdict: "auto_proceed" },
        { event: "gate_decided", stage: "schema_discovery", verdict: "retry" },
        { event: "gate_decided", stage: "schema_discovery", verdict: "escalate" },
      ],
      pending_question: {
        stage_id: "schema_discovery",
        human_prompt: {
          question: "Schema discovery needs a decision.",
          context_summary: "No stable row grain could be verified.",
        },
      },
    }, null);

    expect(state.structured.map((step) => step.status)).toEqual([
      "complete", "complete", "failed", "failed",
    ]);
    expect(state.synthesis).toBe("failed");
    expect(state.proposal).toBe("failed");
    expect(state.attention).toContain("No stable row grain");
  });
});

describe("paginating the routed-file list (#98)", () => {
  const files: RoutedSourceFile[] = Array.from({ length: 60 }, (_, i) => ({
    name: `${String(i).padStart(5, "0")}.csv`,
    format: "csv",
    route: "structured",
    tableNames: [],
  }));

  it("returns one page-size slice and the true total", () => {
    const result = pageOfFiles(files, { search: "", page: 1, pageSize: 25 });
    expect(result.shown).toHaveLength(25);
    expect(result.shown[0].name).toBe("00000.csv");
    expect(result.total).toBe(60);
    expect(result.lastPage).toBe(3);
  });

  it("slices the requested page", () => {
    const result = pageOfFiles(files, { search: "", page: 3, pageSize: 25 });
    expect(result.shown).toHaveLength(10);
    expect(result.shown[0].name).toBe("00050.csv");
  });

  it("filters by file name before paging", () => {
    const result = pageOfFiles(files, { search: "00045", page: 1, pageSize: 25 });
    expect(result.total).toBe(1);
    expect(result.shown.map((f) => f.name)).toEqual(["00045.csv"]);
  });

  it("matches the search case-insensitively", () => {
    // Uppercase query still matches the lowercase ".csv" every file carries.
    expect(pageOfFiles(files, { search: "CSV", page: 1, pageSize: 100 }).total).toBe(60);
  });

  it("clamps a page left stale by a narrowing search instead of showing an empty page", () => {
    // On page 3 of the full list, then the user types a filter matching two
    // files: page 3 no longer exists, so it falls back to the last real page.
    const result = pageOfFiles(files, { search: "00001", page: 3, pageSize: 25 });
    expect(result.total).toBe(1);
    expect(result.page).toBe(1);
    expect(result.shown.map((f) => f.name)).toEqual(["00001.csv"]);
  });
});
