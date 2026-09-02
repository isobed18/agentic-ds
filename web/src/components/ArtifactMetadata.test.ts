import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactMetadata, hasArtifactMetadata, localizedFields } from "./ArtifactMetadata";

Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: () => null },
  configurable: true,
});

describe("generic artifact metadata", () => {
  const preview = {
    artifact_id: "card-1",
    artifact_type: "data_card",
    fields: { table_name: "customers", n_rows: 24 },
    collection_sizes: { columns: 6 },
  };

  it("treats fields and collection counts as meaningful preview content", () => {
    expect(hasArtifactMetadata(preview)).toBe(true);
    expect(hasArtifactMetadata({ artifact_id: "empty", artifact_type: "artifact" })).toBe(false);
  });

  it("renders the safe values instead of an empty-state card", () => {
    const markup = renderToStaticMarkup(createElement(ArtifactMetadata, { preview }));

    expect(markup).toContain("Table name");
    expect(markup).toContain("customers");
    expect(markup).toContain("N rows");
    expect(markup).toContain("24");
    expect(markup).toContain("Columns");
    // #297: a column count says "columns", not the generic "items"/"öğe".
    expect(markup).toMatch(/6 (columns|sütun)/);
  });

  it("keeps the generic unit for a collection of opaque items (#297)", () => {
    const markup = renderToStaticMarkup(createElement(ArtifactMetadata, {
      preview: { artifact_id: "gate-1", artifact_type: "gate", collection_sizes: { triggered_rules: 3 } },
    }));

    expect(markup).toMatch(/3 (items|öğe)/);
  });
});

describe("bilingual field pairs", () => {
  // What an evaluation report's payload looks like through the generic
  // fallback: both languages arrive as sibling scalars (#290).
  const report = {
    problem_title: "Predict churn",
    problem_title_tr: "Kayıp tahmini",
    problem_description: "Which customers leave.",
    problem_description_tr: "Hangi müşteriler ayrılıyor.",
    target_column: "churned",
  };

  it("shows only the Turkish half to a Turkish reader", () => {
    expect(localizedFields(report, "tr")).toEqual([
      ["problem_title", "Kayıp tahmini"],
      ["problem_description", "Hangi müşteriler ayrılıyor."],
      ["target_column", "churned"],
    ]);
  });

  it("shows only the English half to an English reader", () => {
    expect(localizedFields(report, "en")).toEqual([
      ["problem_title", "Predict churn"],
      ["problem_description", "Which customers leave."],
      ["target_column", "churned"],
    ]);
  });

  it("never renders a _tr key as a label of its own", () => {
    for (const language of ["tr", "en"] as const) {
      expect(localizedFields(report, language).map(([key]) => key)).not.toContain("problem_title_tr");
    }
  });

  it("falls back to the English prose when the Turkish half was never written", () => {
    // The contract defaults these to "" when the upstream author produced no
    // Turkish variant. An empty card is worse than English text.
    const fields = { problem_title: "Predict churn", problem_title_tr: "" };
    expect(localizedFields(fields, "tr")).toEqual([["problem_title", "Predict churn"]]);
  });

  it("shows an orphan Turkish value under the base name", () => {
    expect(localizedFields({ rationale_tr: "Gerekçe" }, "tr")).toEqual([["rationale", "Gerekçe"]]);
    expect(localizedFields({ rationale_tr: "Gerekçe" }, "en")).toEqual([["rationale", "Gerekçe"]]);
  });

  it("drops an orphan Turkish value that is blank rather than showing an empty card", () => {
    expect(localizedFields({ rationale_tr: "  " }, "tr")).toEqual([]);
  });

  it("leaves fields with no Turkish sibling alone", () => {
    expect(localizedFields({ table_name: "customers", n_rows: 24 }, "tr")).toEqual([
      ["table_name", "customers"],
      ["n_rows", 24],
    ]);
  });
});
