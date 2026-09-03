/**
 * Interactive measured-schema explorer.
 *
 * Domain edges arrive as dependent/foreign-key -> reference/key. The human
 * canvas deliberately renders the inverse direction: reference entity on the
 * left, dependent/event table on the right. The evidence panel preserves the
 * executor's exact original endpoints.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import type { MeasuredRelationship, ProfiledTable } from "../lib/api";
import { t } from "../lib/i18n";
import { Badge, Spinner, cx } from "./ui";
import { displayCardinality, relationshipId, schemaBackbone } from "./schemaGraph";

interface SchemaTable {
  name: string;
  rows: number;
  columns_count: number;
  columns?: ProfiledTable["columns"];
  candidate_keys?: string[][];
}

interface Props {
  tables: SchemaTable[];
  relationships: MeasuredRelationship[];
}

interface TableNodeData extends Record<string, unknown> {
  table: SchemaTable;
  joinColumns: string[];
}

type TableFlowNode = Node<TableNodeData, "table">;

// #380: px handed to the elk layout, grown 10% with the text inside them.
const NODE_WIDTH = 275;
const NODE_HEIGHT = 130;

function TableNode({ data, selected }: NodeProps<TableFlowNode>) {
  const { table, joinColumns } = data;
  const personal = (table.columns ?? []).filter((column) => column.sensitivity === "pii").length;
  const key = table.candidate_keys?.[0]?.join(" + ");
  return (
    <div
      className={cx(
        "h-[7.375rem] w-[15.625rem] rounded-xl border bg-surface px-3.5 py-3 shadow-card transition-shadow",
        selected ? "border-brand-500 shadow-pop ring-2 ring-brand-100" : "border-line",
      )}
      aria-label={`${table.name}, ${table.rows.toLocaleString()} ${t("rows")}, ${table.columns_count} ${t("columns")}`}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-2 !border-white !bg-brand-500" />
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-2 !border-white !bg-brand-500" />
      <p className="truncate text-2xs font-semibold text-ink" title={table.name}>{table.name}</p>
      <p className="mt-0.5 text-3xs text-ink-mute">
        {table.rows.toLocaleString()} {t("rows")} · {table.columns_count} {t("columns")}
      </p>
      <div className="mt-2 flex min-h-5 flex-wrap gap-1">
        {key && <Badge tone="ok">{t("key")}: {key}</Badge>}
        {personal > 0 && <Badge tone="warn">{personal} {t("personal")}</Badge>}
      </div>
      {joinColumns.length > 0 && (
        <p className="mt-1.5 truncate font-mono text-4xs text-ink-faint" title={joinColumns.join(", ")}>
          {t("joins")}: {joinColumns.join(", ")}
        </p>
      )}
    </div>
  );
}

const nodeTypes = { table: TableNode };

async function layoutGraph(
  tables: SchemaTable[],
  relationships: MeasuredRelationship[],
): Promise<{ nodes: TableFlowNode[]; edges: Edge[] }> {
  // ELK is intentionally lazy: it is a capable layout engine and a large
  // dependency, so pages without a schema should not pay its download cost.
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  const joinColumns = new Map<string, Set<string>>();
  for (const table of tables) joinColumns.set(table.name, new Set());
  for (const edge of relationships) {
    edge.from_columns.forEach((column) => joinColumns.get(edge.from_table)?.add(column));
    edge.to_columns.forEach((column) => joinColumns.get(edge.to_table)?.add(column));
  }

  const graph = await elk.layout({
    id: "schema-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "58",
      "elk.layered.spacing.nodeNodeBetweenLayers": "150",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.padding": "[top=28,left=28,bottom=28,right=28]",
    },
    children: tables.map((table) => ({ id: table.name, width: NODE_WIDTH, height: NODE_HEIGHT })),
    edges: relationships.map((edge) => ({
      id: relationshipId(edge),
      sources: [edge.to_table],
      targets: [edge.from_table],
    })),
  });

  const positions = new Map(
    (graph.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );
  const nodes: TableFlowNode[] = tables.map((table) => ({
    id: table.name,
    type: "table",
    position: positions.get(table.name) ?? { x: 0, y: 0 },
    data: { table, joinColumns: [...(joinColumns.get(table.name) ?? [])].sort() },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  }));

  const edges: Edge[] = relationships.map((relationship) => {
    const suggested = relationship.kind === "suggested";
    const lossy = !suggested && relationship.orphan_rate > 0.02;
    const color = suggested ? "#3b82f6" : lossy ? "#f59e0b" : "#94a3b8";
    return {
      id: relationshipId(relationship),
      source: relationship.to_table,
      target: relationship.from_table,
      type: "smoothstep",
      label: `${displayCardinality(relationship.cardinality)} · ${(relationship.overlap_rate * 100).toFixed(0)}%`,
      // #380: an SVG edge label, in user units rather than rem -- bumped with the rest.
      labelStyle: { fill: lossy ? "#b45309" : "#475569", fontSize: 11, fontWeight: 600 },
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.94 },
      labelBgPadding: [5, 3],
      labelBgBorderRadius: 5,
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      style: {
        stroke: color,
        strokeWidth: lossy ? 2.2 : 1.6,
        strokeDasharray: suggested ? "6 4" : undefined,
      },
      ariaLabel: `${relationship.to_table} to ${relationship.from_table}, ${displayCardinality(relationship.cardinality)}`,
    };
  });
  return { nodes, edges };
}

export function SchemaDiagram({ tables, relationships }: Props) {
  const [showAll, setShowAll] = useState(false);
  const [nodes, setNodes] = useState<TableFlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [layoutError, setLayoutError] = useState<string | null>(null);
  const [selectedRelationshipId, setSelectedRelationshipId] = useState<string | null>(null);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);

  const backbone = useMemo(() => schemaBackbone(relationships), [relationships]);
  const visibleRelationships = showAll ? relationships : backbone;
  const graphKey = visibleRelationships.map(relationshipId).join("|")
    + tables.map((table) => table.name).join("|");

  useEffect(() => {
    let cancelled = false;
    setLayoutError(null);
    void layoutGraph(tables, visibleRelationships)
      .then((layout) => {
        if (cancelled) return;
        setNodes(layout.nodes);
        setEdges(layout.edges);
      })
      .catch((error: unknown) => {
        if (!cancelled) setLayoutError(error instanceof Error ? error.message : String(error));
      });
    return () => { cancelled = true; };
  }, [graphKey]);

  if (!tables.length) return null;

  const selectedRelationship = relationships.find(
    (edge) => relationshipId(edge) === selectedRelationshipId,
  ) ?? null;
  const selectedTableData = tables.find((table) => table.name === selectedTable) ?? null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="mr-auto">
          <p className="text-xs font-medium text-ink">{t("Reference entities flow left to right into dependent tables")}</p>
          <p className="mt-0.5 text-2xs text-ink-faint">{t("Select a table or connection to inspect its measured evidence.")}</p>
        </div>
        {relationships.length > backbone.length && (
          <div className="inline-flex rounded-lg border border-line bg-surface-sunken p-0.5" role="group" aria-label={t("Relationship density")}>
            <button
              onClick={() => setShowAll(false)}
              className={cx("rounded-md px-2.5 py-1 text-2xs font-medium", !showAll ? "bg-surface text-ink shadow-card" : "text-ink-mute")}
            >
              {t("Clear view")} · {backbone.length}
            </button>
            <button
              onClick={() => setShowAll(true)}
              className={cx("rounded-md px-2.5 py-1 text-2xs font-medium", showAll ? "bg-surface text-ink shadow-card" : "text-ink-mute")}
            >
              {t("All measured")} · {relationships.length}
            </button>
          </div>
        )}
      </div>

      <div className="h-[31.25rem] min-h-[26.25rem] w-full overflow-hidden rounded-xl border border-line bg-surface-sunken">
        {layoutError ? (
          <p className="m-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{layoutError}</p>
        ) : nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center"><Spinner label={t("Laying out the schema…")} /></div>
        ) : (
          <ReactFlow
            key={graphKey}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            deleteKeyCode={null}
            multiSelectionKeyCode={null}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1.15 }}
            minZoom={0.25}
            maxZoom={1.6}
            zoomOnScroll={false}
            panOnScroll
            onPaneClick={() => { setSelectedRelationshipId(null); setSelectedTable(null); }}
            onNodeClick={(_, node) => { setSelectedTable(node.id); setSelectedRelationshipId(null); }}
            onEdgeClick={(_, edge) => { setSelectedRelationshipId(edge.id); setSelectedTable(null); }}
            aria-label={t("Measured data schema")}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cbd5e1" />
            <Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        )}
      </div>

      <div className="mt-3 min-h-[4.625rem] rounded-lg border border-line bg-surface px-3.5 py-3">
        {selectedRelationship ? (
          <div className="flex flex-wrap items-start gap-x-5 gap-y-2 text-xs">
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-ink">
                {selectedRelationship.from_table}.{selectedRelationship.from_columns.join(" + ")} → {selectedRelationship.to_table}.{selectedRelationship.to_columns.join(" + ")}
              </p>
              <p className="mt-1 text-ink-mute">{t("Measured from real column values; the arrow above is reversed only for human dependency reading.")}</p>
            </div>
            <Badge>{selectedRelationship.cardinality}</Badge>
            <Badge tone={selectedRelationship.orphan_rate > 0.02 ? "warn" : "ok"}>
              {(selectedRelationship.overlap_rate * 100).toFixed(1)}% {t("match")}
            </Badge>
            {selectedRelationship.orphan_rate > 0 && (
              <Badge tone="warn">{(selectedRelationship.orphan_rate * 100).toFixed(1)}% {t("unmatched")}</Badge>
            )}
          </div>
        ) : selectedTableData ? (
          <div className="text-xs">
            <p className="font-semibold text-ink">{selectedTableData.name}</p>
            <p className="mt-1 text-ink-mute">
              {selectedTableData.rows.toLocaleString()} {t("rows")} · {selectedTableData.columns_count} {t("columns")} · {selectedTableData.candidate_keys?.length ?? 0} {t("candidate keys")}
            </p>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-2xs text-ink-faint">
            <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 bg-ink-faint" />{t("Measured relationship (evidence)")}</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 bg-warn-500" />{t("Lossy relationship (drops rows)")}</span>
            <span>{t("The clear view removes redundant cycles only; all evidence remains available.")}</span>
          </div>
        )}
      </div>
    </div>
  );
}
