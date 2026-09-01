import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ArtifactMetadata, hasArtifactMetadata } from "./ArtifactMetadata";

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
