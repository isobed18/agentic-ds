/**
 * The measured schema, drawn.
 *
 * Every edge here is an overlap between real column values, not a guess from
 * column names. That distinction is the whole point of drawing it: the join
 * from `ledger_2019_2024.provider_ref` to `physicians.physician_id` shares no
 * name at all, and 5.8% of the ledger's rows have no parent -- rows that
 * vanish from the joined table without anyone being told. On the diagram that
 * edge is amber and carries the number; in a list of five relationships it was
 * the fourth row.
 *
 * Laid out in dependency columns rather than by a force simulation: a schema is
 * a small directed graph, its shape carries meaning (parents right, children
 * left), and a layout that settles differently on every render is a layout you
 * cannot point at in a conversation.
 */
import { useMemo, useState } from "react";
import type { MeasuredRelationship, ProfiledTable } from "../lib/api";
import { t } from "../lib/i18n";
import { cx } from "./ui";

interface Props {
  tables: { name: string; rows: number; columns_count: number; columns?: ProfiledTable["columns"] }[];
  relationships: MeasuredRelationship[];
}

const CARD_WIDTH = 190;
const CARD_HEIGHT = 66;
const GAP_X = 96;
const GAP_Y = 26;

/**
 * Depth = longest path of foreign keys out of this table. A child sits one
 * column left of every parent it references, so an arrow always points right
 * and the roots -- the tables nothing depends on -- line up on the right edge.
 */
function depths(tables: string[], edges: MeasuredRelationship[]): Map<string, number> {
  const parents = new Map<string, string[]>();
  for (const table of tables) parents.set(table, []);
  for (const edge of edges) {
    if (edge.from_table === edge.to_table) continue;
    parents.get(edge.from_table)?.push(edge.to_table);
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();

  function walk(table: string): number {
    const known = depth.get(table);
    if (known !== undefined) return known;
    // A cycle is possible in measured overlaps -- two tables can each cover the
    // other -- and a recursive layout must not hang on one.
    if (visiting.has(table)) return 0;
    visiting.add(table);
    const own = parents.get(table) ?? [];
    const value = own.length ? Math.max(...own.map((parent) => walk(parent) + 1)) : 0;
    visiting.delete(table);
    depth.set(table, value);
    return value;
  }

  for (const table of tables) walk(table);
  return depth;
}

export function SchemaDiagram({ tables, relationships }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);

  const layout = useMemo(() => {
    const names = tables.map((table) => table.name);
    const depth = depths(names, relationships);
    const columns = new Map<number, string[]>();
    for (const name of names) {
      const level = depth.get(name) ?? 0;
      columns.set(level, [...(columns.get(level) ?? []), name]);
    }
    const tallest = Math.max(...[...columns.values()].map((column) => column.length), 1);
    const positions = new Map<string, { x: number; y: number }>();
    for (const [level, members] of columns) {
      const offset = ((tallest - members.length) * (CARD_HEIGHT + GAP_Y)) / 2;
      members.forEach((name, index) => {
        positions.set(name, {
          x: level * (CARD_WIDTH + GAP_X),
          y: offset + index * (CARD_HEIGHT + GAP_Y),
        });
      });
    }
    return {
      positions,
      width: (Math.max(...columns.keys(), 0) + 1) * (CARD_WIDTH + GAP_X) - GAP_X,
      height: tallest * (CARD_HEIGHT + GAP_Y) - GAP_Y,
    };
  }, [tables, relationships]);

  if (!tables.length) return null;

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`-16 -16 ${layout.width + 32} ${layout.height + 32}`}
        style={{ minWidth: Math.min(layout.width + 32, 1100), height: layout.height + 32 }}
        className="max-w-full"
      >
        <defs>
          <marker id="schema-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.5 L7.5 4 L0 7.5 z" className="fill-ink-faint" />
          </marker>
          <marker id="schema-arrow-warn" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.5 L7.5 4 L0 7.5 z" className="fill-warn-500" />
          </marker>
          <marker id="schema-arrow-suggested" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.5 L7.5 4 L0 7.5 z" className="fill-brand-500" />
          </marker>
        </defs>

        {relationships.map((edge, index) => {
          const from = layout.positions.get(edge.from_table);
          const to = layout.positions.get(edge.to_table);
          if (!from || !to) return null;
          const x1 = from.x + CARD_WIDTH;
          const y1 = from.y + CARD_HEIGHT / 2;
          const x2 = to.x;
          const y2 = to.y + CARD_HEIGHT / 2;
          const mid = (x1 + x2) / 2;
          const isSuggested = edge.kind === "suggested";
          const lossy = !isSuggested && edge.orphan_rate > 0.02;
          const active = hovered === null || hovered === edge.from_table || hovered === edge.to_table;
          return (
            <g key={index} opacity={active ? 1 : 0.18}>
              <path
                d={`M${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                fill="none"
                strokeWidth={isSuggested ? 1.6 : lossy ? 2 : 1.4}
                strokeDasharray={isSuggested ? "5 4" : undefined}
                className={isSuggested ? "stroke-brand-500" : lossy ? "stroke-warn-500" : "stroke-line"}
                markerEnd={`url(#${isSuggested ? "schema-arrow-suggested" : lossy ? "schema-arrow-warn" : "schema-arrow"})`}
              />
              <text
                x={mid}
                y={(y1 + y2) / 2 - 6}
                textAnchor="middle"
                className={cx(
                  "text-[9px]",
                  isSuggested ? "fill-brand-600 font-medium" : lossy ? "fill-warn-700 font-medium" : "fill-ink-faint"
                )}
              >
                {edge.cardinality}
                {isSuggested && ` · ${t("suggested")}`}
                {!isSuggested && lossy && ` · ${(edge.orphan_rate * 100).toFixed(1)}% ${t("unmatched")}`}
              </text>
              <title>
                {isSuggested
                  ? `[${t("Claim / Suggestion")}] ${edge.from_table}.${edge.from_columns.join(" + ")} → ` +
                    `${edge.to_table}.${edge.to_columns.join(" + ")}` +
                    (edge.rationale ? ` · ${edge.rationale}` : "")
                  : `${edge.from_table}.${edge.from_columns.join(" + ")} → ` +
                    `${edge.to_table}.${edge.to_columns.join(" + ")} · ` +
                    `${(edge.overlap_rate * 100).toFixed(1)}% ${t("of rows match")}`}
              </title>
            </g>
          );
        })}

        {tables.map((table) => {
          const at = layout.positions.get(table.name);
          if (!at) return null;
          const personal = (table.columns ?? []).filter((c) => c.sensitivity === "pii").length;
          const dim = hovered !== null && hovered !== table.name;
          return (
            <g
              key={table.name}
              transform={`translate(${at.x} ${at.y})`}
              onMouseEnter={() => setHovered(table.name)}
              onMouseLeave={() => setHovered(null)}
              opacity={dim ? 0.35 : 1}
              className="cursor-default"
            >
              <rect
                width={CARD_WIDTH}
                height={CARD_HEIGHT}
                rx={10}
                className="fill-surface stroke-line"
                strokeWidth={1.2}
              />
              <text x={12} y={24} className="fill-ink text-[12.5px] font-semibold">
                {table.name.length > 24 ? `${table.name.slice(0, 23)}…` : table.name}
              </text>
              <text x={12} y={41} className="fill-ink-mute text-[10px]">
                {table.rows.toLocaleString()} {t("rows")} · {table.columns_count} {t("columns")}
              </text>
              {personal > 0 && (
                <>
                  <rect x={12} y={48} width={personal > 9 ? 74 : 68} height={13} rx={3} className="fill-warn-50" />
                  <text x={18} y={57.5} className="fill-warn-700 text-[9px] font-semibold">
                    {personal} {t("personal")}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] leading-relaxed text-ink-faint">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-ink-faint" />
          {t("Measured relationship (evidence)")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-warn-500" />
          {t("Lossy relationship (drops rows)")}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 border-b-2 border-dashed border-brand-500" />
          {t("Agent-suggested relationship (claim)")}
        </span>
      </div>
    </div>
  );
}
