import { describe, expect, it } from "vitest";

import { ARTIFACT_TYPE_LABELS, artifactTypeLabel } from "./artifactTypeLabel";

describe("naming an artifact kind", () => {
  it("keeps initialisms uppercase, which the derived label could not", () => {
    // #366: the old `.replaceAll("_", " ")` + capitalise produced "Eda report",
    // which English readers saw too.
    expect(artifactTypeLabel("eda_report")).toBe("EDA report");
  });

  it("names every kind in the vocabulary", () => {
    expect(artifactTypeLabel("measurement_bundle")).toBe("Measurement bundle");
    expect(artifactTypeLabel("gate_decision")).toBe("Gate decision");
  });

  it("still reads as prose for a kind the server added before this build", () => {
    // The label table is not a gate on rendering: an unknown code gets the old
    // derived name rather than a raw snake_case string on the card.
    expect(artifactTypeLabel("some_future_kind")).toBe("Some future kind");
  });

  it("exports each label exactly once for the catalogue test", () => {
    expect(new Set(ARTIFACT_TYPE_LABELS).size).toBe(ARTIFACT_TYPE_LABELS.length);
  });
});
