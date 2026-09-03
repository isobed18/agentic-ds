import type { ProfiledColumn, SourceProfile, StagingWorkspace } from "../lib/api";

/** One table's columns, as offered for a target choice. */
export interface TargetColumnGroup {
  table: string;
  columns: ProfiledColumn[];
}

/** Every profiled column a person may name as the ML target, base table first.
 *
 * Lifted out of `GuidedPipeline` for #464: the manual target selector is
 * reachable from the failed-run panel *and* from the gate escalation card, and
 * those two live under different parents. Two derivations of "which columns can
 * be a target" would drift, and drifting is how the toolbar picker and the
 * reframe picker came to disagree in the first place.
 *
 * #448 is the rule this keeps: everything the source profiled is offered, not
 * just the base table's columns, because on a multi-table source the fact
 * table's measure is the interesting target and it is rarely the base. Base
 * table first, and a name already claimed is not offered twice -- the value
 * sent is the bare column name, which is what the integrated ABT will call it,
 * and the base table's column is the one a join resolves that name to. A column
 * the accepted joins do not bring into the ABT is refused with measured
 * blocking reasons (#427/#435) rather than silently mis-aimed.
 */
export function targetColumnGroups(
  profile: SourceProfile,
  workspace?: StagingWorkspace | null,
): TargetColumnGroup[] {
  const configuration = (workspace?.recommended_plan?.configuration ?? {}) as Record<string, unknown>;
  const baseTableName = String(configuration.base_table ?? profile.tables[0]?.name ?? "");
  const baseTable = profile.tables.find((table) => table.name === baseTableName) ?? profile.tables[0];
  const ordered = [
    ...(baseTable ? [baseTable] : []),
    ...profile.tables.filter((table) => table.name !== baseTable?.name),
  ];
  const claimed = new Set<string>();
  const groups: TargetColumnGroup[] = [];
  for (const table of ordered) {
    const columns = (table.columns ?? []).filter((column) => !claimed.has(column.name));
    for (const column of columns) claimed.add(column.name);
    if (columns.length) groups.push({ table: table.name, columns });
  }
  return groups;
}

/** The same columns as one flat list, in the same order. */
export function targetColumnList(
  profile: SourceProfile,
  workspace?: StagingWorkspace | null,
): ProfiledColumn[] {
  return targetColumnGroups(profile, workspace).flatMap((group) => group.columns);
}
