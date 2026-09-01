import { describe, expect, it } from "vitest";

import { COUNT_UNITS, countUnit } from "./collectionUnit";

describe("units for artifact collection counts", () => {
  it("counts columns as columns, not as items", () => {
    // The reported case: a "Feature columns" card read "42 öğe" -- true, and
    // useless, because "öğe" says nothing about what 42 counts.
    expect(countUnit("feature_columns")).toBe("{count} columns");
    expect(countUnit("columns")).toBe("{count} columns");
  });

  it("covers the whole column family from one rule", () => {
    // Roughly twenty payload fields end in `columns`. Matching the trailing
    // noun rather than the whole key means a new contract field is covered
    // without a change here.
    for (const key of [
      "declared_feature_columns",
      "dropped_columns",
      "excluded_columns",
      "leakage_suspect_columns",
      "model_eligible_columns",
      "structural_leakage_suspect_columns",
      "left_columns",
    ]) {
      expect(countUnit(key), key).toBe("{count} columns");
    }
  });

  it("names the other dimensionful collections", () => {
    expect(countUnit("rows")).toBe("{count} rows");
    expect(countUnit("tables")).toBe("{count} tables");
    expect(countUnit("selected_files")).toBe("{count} files");
    expect(countUnit("documents")).toBe("{count} documents");
    expect(countUnit("pages")).toBe("{count} pages");
    expect(countUnit("small_sample_warnings")).toBe("{count} warnings");
    expect(countUnit("findings")).toBe("{count} findings");
    expect(countUnit("candidates")).toBe("{count} candidates");
    expect(countUnit("holdout_metrics")).toBe("{count} metrics");
  });

  it("keeps 'items' for collections that really do hold opaque items", () => {
    // Inventing a unit for a rule list or a check list would be worse than the
    // generic word, which is the honest answer there.
    expect(countUnit("triggered_rules")).toBe("{count} items");
    expect(countUnit("unmet_criteria")).toBe("{count} items");
    expect(countUnit("settings")).toBe("{count} items");
    expect(countUnit("items")).toBe("{count} items");
  });

  it("matches the trailing noun, not a substring of it", () => {
    // `candidate_comparisons` counts comparisons, `candidate_primary_keys`
    // counts keys. Neither is a candidate count.
    expect(countUnit("candidate_comparisons")).toBe("{count} items");
    expect(countUnit("candidate_primary_keys")).toBe("{count} items");
    expect(countUnit("columns_metadata")).toBe("{count} items");
  });

  it("lists every phrase it can return, so the catalogue test can see them", () => {
    // These reach t() through a variable and are invisible to the literal
    // scanner that guards the Turkish catalogue.
    expect(COUNT_UNITS).toContain("{count} items");
    expect(new Set(COUNT_UNITS).size).toBe(COUNT_UNITS.length);
    for (const phrase of COUNT_UNITS) expect(phrase).toContain("{count}");
  });
});
