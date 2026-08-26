import type { MeasuredRelationship } from "../lib/api";

/** Stable identity includes columns; table-pair identity loses parallel joins. */
export function relationshipId(edge: MeasuredRelationship): string {
  return [
    edge.from_table,
    edge.from_columns.join("+"),
    edge.to_table,
    edge.to_columns.join("+"),
    edge.kind ?? "measured",
  ].join("::");
}

/**
 * Maximum-confidence spanning forest for the default human view.
 *
 * Every edge remains available in the expanded view. This removes cycles and
 * parallel paths only from the initial canvas so the topology is legible.
 */
export function schemaBackbone(edges: MeasuredRelationship[]): MeasuredRelationship[] {
  const parent = new Map<string, string>();

  function find(value: string): string {
    const current = parent.get(value);
    if (!current) {
      parent.set(value, value);
      return value;
    }
    if (current === value) return value;
    const root = find(current);
    parent.set(value, root);
    return root;
  }

  function union(left: string, right: string): boolean {
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot === rightRoot) return false;
    parent.set(rightRoot, leftRoot);
    return true;
  }

  const ranked = [...edges].sort((left, right) => {
    const evidence = Number((left.kind ?? "measured") === "measured")
      - Number((right.kind ?? "measured") === "measured");
    if (evidence !== 0) return -evidence;
    if (left.overlap_rate !== right.overlap_rate) return right.overlap_rate - left.overlap_rate;
    if (left.orphan_rate !== right.orphan_rate) return left.orphan_rate - right.orphan_rate;
    const oneToOne = Number(left.cardinality === "1:1") - Number(right.cardinality === "1:1");
    if (oneToOne !== 0) return -oneToOne;
    return relationshipId(left).localeCompare(relationshipId(right));
  });

  return ranked.filter((edge) => {
    if (edge.from_table === edge.to_table) return false;
    return union(edge.from_table, edge.to_table);
  });
}

export function displayCardinality(cardinality: string): string {
  if (cardinality === "N:1") return "1:N";
  if (cardinality === "1:N") return "N:1";
  return cardinality;
}
