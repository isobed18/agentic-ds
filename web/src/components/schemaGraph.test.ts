import { describe, expect, it } from "vitest";
import type { MeasuredRelationship } from "../lib/api";
import { displayCardinality, relationshipId, schemaBackbone } from "./schemaGraph";

function edge(
  from: string,
  to: string,
  overlap = 1,
  orphan = 0,
  cardinality = "N:1",
): MeasuredRelationship {
  return {
    from_table: from,
    from_columns: [`${from}_id`],
    to_table: to,
    to_columns: [`${to}_id`],
    overlap_rate: overlap,
    orphan_rate: orphan,
    parent_coverage: 1,
    cardinality,
    name_affinity: 0,
  };
}

describe("schema graph projection", () => {
  it("uses column endpoints in stable relationship identity", () => {
    const first = edge("events", "people");
    const second = { ...first, from_columns: ["owner_id"] };

    expect(relationshipId(first)).not.toBe(relationshipId(second));
  });

  it("reduces redundant transitive paths to a maximum-confidence backbone", () => {
    const relationships = [
      edge("compensation", "master", 1, 0, "1:1"),
      edge("transactions", "compensation"),
      edge("transactions", "master"),
      edge("ledger", "compensation", 0.942, 0.058),
      edge("ledger", "master", 0.942, 0.058),
    ];

    const backbone = schemaBackbone(relationships);

    expect(backbone).toHaveLength(3);
    expect(backbone.map(relationshipId)).toContain(relationshipId(relationships[0]));
    expect(new Set(backbone.flatMap((item) => [item.from_table, item.to_table]))).toEqual(
      new Set(["master", "compensation", "transactions", "ledger"]),
    );
  });

  it("renders reference-to-dependent cardinality for the human view", () => {
    expect(displayCardinality("N:1")).toBe("1:N");
    expect(displayCardinality("1:1")).toBe("1:1");
  });
});
