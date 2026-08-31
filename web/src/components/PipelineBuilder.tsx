import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type OnEdgesChange,
} from "@xyflow/react";
import {
  api,
  type ArtifactPreview,
  type AutomationComponentDefinition,
  type DocumentEngine,
  type PipelineBlueprint,
  type PipelineComponent,
  type PipelineConnection,
  type PipelineLayout,
  type PipelineOutputReference,
} from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { ArtifactMetadata } from "./ArtifactMetadata";
import { artifactTitle } from "./artifactTitle";
import { pipelinePortsMatch } from "./pipelineGraph";
import { PlannerPanel } from "./PlannerPanel";
import { Badge, Spinner, cx } from "./ui";

interface ComponentNodeData extends Record<string, unknown> {
  component: PipelineComponent;
  outputs: PipelineOutputReference[];
}

interface BranchNodeData extends Record<string, unknown> {
  branchId: string;
  componentCount: number;
  onExpand: (branchId: string) => void;
}

type ComponentFlowNode = Node<ComponentNodeData, "pipelineComponent">;
type BranchFlowNode = Node<BranchNodeData, "branchGroup">;
type FlowNode = ComponentFlowNode | BranchFlowNode;

const NODE_WIDTH = 224;
const NODE_HEIGHT = 82;
const BRANCH_WIDTH = 250;
const BRANCH_HEIGHT = 92;

export const WORKFLOW_INTERACTION = {
  panOnScroll: true,
  panOnScrollSpeed: 0.85,
  zoomOnScroll: false,
  zoomOnPinch: true,
  panOnDrag: [1, 2] as number[],
  minZoom: 0.25,
  maxZoom: 1.8,
};

function local(value: { en: string; tr: string }): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

function ComponentNode({ data, selected }: NodeProps<ComponentFlowNode>) {
  const { component, outputs } = data;
  const artifactCount = new Set(outputs.flatMap((output) => output.artifact_ids)).size;
  const status = outputs.some((output) => output.status === "needs_review")
    ? "Needs review"
    : outputs.length > 0 && outputs.every((output) => output.status === "ready")
      ? "Completed"
      : outputs.some((output) => output.status === "pending")
        ? "Running"
        : component.enabled ? "Ready" : "Disabled";
  return (
    <article className={cx("h-[82px] w-56 rounded-xl border bg-surface shadow-card", selected ? "border-brand-500 ring-2 ring-brand-100" : "border-line", !component.enabled && "opacity-55")}>
      {component.inputs.map((port, index) => <Handle key={`in:${port.id}`} id={`in:${port.id}`} type="target" position={Position.Left} style={{ top: `${32 + ((index + 1) * 40) / (component.inputs.length + 1)}%` }} className="!h-2 !w-2 !border-2 !border-white !bg-slate-400" />)}
      {component.outputs.map((port, index) => <Handle key={`out:${port.id}`} id={`out:${port.id}`} type="source" position={Position.Right} style={{ top: `${32 + ((index + 1) * 40) / (component.outputs.length + 1)}%` }} className="!h-2 !w-2 !border-2 !border-white !bg-slate-400" />)}
      <div className="flex h-full items-center gap-3 overflow-hidden rounded-xl px-3.5 py-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-50 text-sm font-bold text-brand-700">{local(component.title).slice(0, 1)}</span>
        <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-semibold text-ink">{local(component.title)}</span><span className="mt-1 block text-[10px] font-medium text-ink-mute">{t(status)}</span></span>
        <span className="flex flex-col items-end gap-1">{component.control.execution === "pause_after" && <Badge tone="warn">{t("Pause")}</Badge>}{component.branch_id && <Badge tone="brand">{component.branch_id}</Badge>}{artifactCount > 0 && <Badge tone="neutral">{artifactCount}</Badge>}</span>
      </div>
    </article>
  );
}

function BranchNode({ data, selected }: NodeProps<BranchFlowNode>) {
  return (
    <article className={cx("h-[92px] w-[250px] rounded-2xl border-2 border-dashed bg-brand-50 px-4 py-3 shadow-card", selected ? "border-brand-500" : "border-brand-200")}>
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-brand-400" />
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-brand-400" />
      <div className="flex items-center gap-3 overflow-hidden"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-100 text-brand-700">⑂</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-ink">{data.branchId}</p><p className="truncate text-[10px] text-ink-mute">{data.componentCount} {t("workflow steps collapsed")}</p></div></div>
      <button type="button" className="mt-2 text-[10px] font-semibold text-brand-700" onClick={(event) => { event.stopPropagation(); data.onExpand(data.branchId); }}>{t("Expand branch")}</button>
    </article>
  );
}

const nodeTypes = { pipelineComponent: ComponentNode, branchGroup: BranchNode };

function semanticEdge(connection: PipelineConnection): Edge {
  return {
    id: connection.id,
    source: connection.source_component,
    sourceHandle: `out:${connection.source_port}`,
    target: connection.target_component,
    targetHandle: `in:${connection.target_port}`,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8", width: 14, height: 14 },
    style: { stroke: "#94a3b8", strokeWidth: 1.5 },
  };
}

export function displayEdges(blueprint: PipelineBlueprint, collapsedBranches: Set<string>): Edge[] {
  const branchByComponent = new Map(blueprint.components.map((component) => [component.id, component.branch_id]));
  const mapped: Edge[] = [];
  for (const connection of blueprint.connections) {
    const sourceBranch = branchByComponent.get(connection.source_component);
    const targetBranch = branchByComponent.get(connection.target_component);
    const source = sourceBranch && collapsedBranches.has(sourceBranch) ? `branch:${sourceBranch}` : connection.source_component;
    const target = targetBranch && collapsedBranches.has(targetBranch) ? `branch:${targetBranch}` : connection.target_component;
    if (source === target) continue;
    const edge = semanticEdge(connection);
    mapped.push({ ...edge, id: `${source}->${target}`, source, target, sourceHandle: source.startsWith("branch:") ? null : edge.sourceHandle, targetHandle: target.startsWith("branch:") ? null : edge.targetHandle });
  }
  return [...new Map(mapped.map((edge) => [edge.id, edge])).values()];
}

function displayNodes(
  blueprint: PipelineBlueprint,
  outputs: PipelineOutputReference[],
  collapsedBranches: Set<string>,
  positions: Map<string, { x: number; y: number }>,
  onExpand: (branchId: string) => void,
): FlowNode[] {
  const visible = blueprint.components.filter((component) => !component.branch_id || !collapsedBranches.has(component.branch_id));
  const nodes: FlowNode[] = visible.map((component) => ({
    id: component.id,
    type: "pipelineComponent",
    position: positions.get(component.id) ?? { x: 0, y: 0 },
    data: { component, outputs: outputs.filter((output) => output.component_id === component.id) },
  }));
  for (const branchId of collapsedBranches) {
    const children = blueprint.components.filter((component) => component.branch_id === branchId);
    if (!children.length) continue;
    const firstPosition = children.map((component) => positions.get(component.id)).find(Boolean) ?? { x: 0, y: 0 };
    nodes.push({ id: `branch:${branchId}`, type: "branchGroup", position: firstPosition, data: { branchId, componentCount: children.length, onExpand } });
  }
  return nodes;
}

async function autoLayout(nodes: FlowNode[], edges: Edge[]): Promise<FlowNode[]> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const graph = await new ELK().layout({
    id: "pipeline-root",
    layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT", "elk.edgeRouting": "ORTHOGONAL", "elk.spacing.nodeNode": "54", "elk.layered.spacing.nodeNodeBetweenLayers": "100", "elk.padding": "[top=40,left=40,bottom=40,right=40]" },
    children: nodes.map((node) => ({ id: node.id, width: node.type === "branchGroup" ? BRANCH_WIDTH : NODE_WIDTH, height: node.type === "branchGroup" ? BRANCH_HEIGHT : NODE_HEIGHT })),
    edges: edges.map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
  });
  const positions = new Map((graph.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]));
  return nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position }));
}

export function PipelineBuilder({ runId = null, baseArtifactId = null, blueprint, layout, componentOutputs = [], onSaved, onChange, onExitAdvanced }: {
  runId?: string | null;
  baseArtifactId?: string | null;
  blueprint: PipelineBlueprint;
  layout?: PipelineLayout;
  componentOutputs?: PipelineOutputReference[];
  onSaved?: (workspace: Awaited<ReturnType<typeof api.updateStagingPipeline>>) => void;
  onChange?: (blueprint: PipelineBlueprint) => void;
  onExitAdvanced?: () => void;
}) {
  const [draft, setDraft] = useState(blueprint);
  const initialCollapsed = useMemo(() => new Set(layout?.collapsed_branches ?? blueprint.components.map((component) => component.branch_id).filter((value): value is string => Boolean(value))), []);
  const [collapsedBranches, setCollapsedBranches] = useState(initialCollapsed);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, applyEdgeChanges] = useEdgesState<Edge>([]);
  const [catalog, setCatalog] = useState<AutomationComponentDefinition[]>([]);
  const [engines, setEngines] = useState<DocumentEngine[]>([]);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [plannerOpen, setPlannerOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const structureRef = useRef("");
  const currentLayout = useRef(layout);
  const collapsedReady = useRef(false);

  useEffect(() => { currentLayout.current = layout; }, [layout]);
  useEffect(() => { void api.automationComponents().then((value) => { setCatalog(value.components); setEngines(value.document_engines); }); }, []);

  const expandBranch = useCallback((branchId: string) => {
    setCollapsedBranches((current) => { const next = new Set(current); next.delete(branchId); return next; });
  }, []);

  const structureKey = `${draft.components.map((component) => `${component.id}:${component.branch_id ?? ""}`).join("|")}::${draft.connections.map((edge) => `${edge.id}:${edge.source_component}.${edge.source_port}->${edge.target_component}.${edge.target_port}`).join("|")}::${[...collapsedBranches].sort().join("|")}`;

  useEffect(() => {
    const positions = new Map((layout?.nodes ?? []).map((node) => [node.component_id, { x: node.x, y: node.y }]));
    const nextEdges = displayEdges(draft, collapsedBranches);
    const nextNodes = displayNodes(draft, componentOutputs, collapsedBranches, positions, expandBranch);
    setEdges(nextEdges);
    const hasEveryPosition = nextNodes.every((node) => node.id.startsWith("branch:") || positions.has(node.id));
    if (structureRef.current === structureKey) {
      setNodes((current) => nextNodes.map((node) => ({ ...node, position: current.find((item) => item.id === node.id)?.position ?? node.position })));
      return;
    }
    structureRef.current = structureKey;
    if (hasEveryPosition && nextNodes.length) setNodes(nextNodes);
    else void autoLayout(nextNodes, nextEdges).then(setNodes);
  }, [structureKey]);

  useEffect(() => {
    setNodes((current) => current.map((node) => node.type === "pipelineComponent" ? {
      ...node,
      data: {
        ...node.data,
        component: draft.components.find((component) => component.id === node.id) ?? node.data.component,
        outputs: componentOutputs.filter((output) => output.component_id === node.id),
      },
    } : node));
  }, [componentOutputs, draft.components]);

  useEffect(() => {
    setDraft(blueprint);
    setSaved(true);
  }, [blueprint]);

  const selectedComponent = useMemo(() => draft.components.find((component) => component.id === selected) ?? null, [draft, selected]);

  async function persistLayout(positionOverride?: { id: string; x: number; y: number }, branchOverride = collapsedBranches) {
    if (!runId || !baseArtifactId) return;
    const previous = new Map((currentLayout.current?.nodes ?? []).map((node) => [node.component_id, node]));
    for (const node of nodes) {
      if (node.type !== "pipelineComponent") continue;
      previous.set(node.id, { component_id: node.id, x: positionOverride?.id === node.id ? positionOverride.x : node.position.x, y: positionOverride?.id === node.id ? positionOverride.y : node.position.y, collapsed: true });
    }
    const next: PipelineLayout = { version: "1", nodes: [...previous.values()], collapsed_branches: [...branchOverride].sort() };
    try {
      const workspace = await api.updateStagingLayout(runId, baseArtifactId, next);
      currentLayout.current = workspace.pipeline_layout;
      onSaved?.(workspace);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  useEffect(() => {
    if (!collapsedReady.current) {
      collapsedReady.current = true;
      return;
    }
    void persistLayout(undefined, collapsedBranches);
  }, [collapsedBranches]);

  function collapseAllBranches() {
    setCollapsedBranches(new Set(draft.components.map((component) => component.branch_id).filter((value): value is string => Boolean(value))));
  }

  async function runAutoLayout() {
    setBusy(true);
    try {
      const laidOut = await autoLayout(nodes, edges);
      setNodes(laidOut);
      if (runId && baseArtifactId) {
        const next: PipelineLayout = { version: "1", nodes: laidOut.filter((node) => node.type === "pipelineComponent").map((node) => ({ component_id: node.id, x: node.position.x, y: node.position.y, collapsed: true })), collapsed_branches: [...collapsedBranches].sort() };
        const workspace = await api.updateStagingLayout(runId, baseArtifactId, next);
        currentLayout.current = workspace.pipeline_layout;
        onSaved?.(workspace);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally { setBusy(false); }
  }

  function updateComponent(componentId: string, update: (component: PipelineComponent) => PipelineComponent) {
    setDraft((current) => ({ ...current, components: current.components.map((component) => component.id === componentId ? update(component) : component) }));
    setSaved(false);
  }

  function addComponent(definition: AutomationComponentDefinition) {
    if (!definition.repeatable && draft.components.some((item) => item.catalog_id === definition.catalog_id)) { setError(t("This component can only be added once.")); return; }
    const id = `${definition.catalog_id.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}-${crypto.randomUUID().slice(0, 6)}`;
    const component: PipelineComponent = { id, kind: definition.kind, title: definition.title, description: definition.description, inputs: definition.inputs, outputs: definition.outputs, settings: { ...definition.default_settings }, control: { execution: "auto", gate_handler: "planner", max_retries: null }, enabled: true, optional: false, evidence_layer: definition.evidence_layer, configured_by: "human", catalog_id: definition.catalog_id, branch_id: null, group_id: null };
    setDraft((current) => ({ ...current, components: [...current.components, component] }));
    setSelected(id); setCatalogOpen(false); setSaved(false); setError(null);
  }

  function removeComponent(componentId: string) {
    if (componentId === "data-source") return;
    setDraft((current) => ({ ...current, components: current.components.filter((component) => component.id !== componentId), connections: current.connections.filter((edge) => edge.source_component !== componentId && edge.target_component !== componentId) }));
    setSelected(null); setSaved(false);
  }

  function connect(connection: Connection) {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return;
    if (!pipelinePortsMatch(draft, connection.source, connection.sourceHandle, connection.target, connection.targetHandle)) { setError(t("Connections require matching output and input contracts.")); return; }
    const semantic: PipelineConnection = { id: `human:${crypto.randomUUID()}`, source_component: connection.source, source_port: connection.sourceHandle.slice(4), target_component: connection.target, target_port: connection.targetHandle.slice(3) };
    setDraft((current) => ({ ...current, connections: [...current.connections, semantic] })); setSaved(false); setError(null);
  }

  const onEdgesChange: OnEdgesChange<Edge> = (changes) => {
    applyEdgeChanges(changes);
    const removed = new Set(changes.filter((change) => change.type === "remove").map((change) => change.id));
    if (removed.size) { setDraft((current) => ({ ...current, connections: current.connections.filter((edge) => !removed.has(edge.id)) })); setSaved(false); }
  };

  async function saveBlueprint() {
    setBusy(true); setError(null);
    try {
      if (runId && baseArtifactId) onSaved?.(await api.updateStagingPipeline(runId, baseArtifactId, draft));
      onChange?.(draft); setSaved(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setBusy(false); }
  }

  return (
    <section className="relative flex h-full min-h-[34rem] flex-col overflow-hidden bg-surface-sunken">
      <header className="z-20 flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2.5">
        {onExitAdvanced && <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={onExitAdvanced}>← {t("Back to proposal")}</button>}
        <div className="mr-auto"><h2 className="text-sm font-semibold text-ink">{t("Workflow")}</h2><p className="text-[10px] text-ink-mute">{t("The accepted graph defines the plan; layout is visual state only.")}</p></div>
        <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => setCatalogOpen(true)}>+ {t("Add component")}</button>
        <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => void runAutoLayout()} disabled={busy}>{t("Auto layout")}</button>
        <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={collapseAllBranches} disabled={!draft.components.some((component) => component.branch_id)}>{t("Collapse branches")}</button>
        <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => setPlannerOpen(true)}>{t("Planner")}</button>
        <button type="button" className="btn-primary !py-1.5 text-xs" onClick={() => void saveBlueprint()} disabled={busy || saved}>{saved ? t("Saved") : t("Save workflow")}</button>
      </header>
      {error && <p className="absolute left-4 top-16 z-30 max-w-xl rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700 shadow-card">{error}</p>}
      <div className="min-h-0 flex-1">
        {nodes.length === 0 ? <div className="grid h-full place-items-center"><Spinner label={t("Preparing workflow…")} /></div> : (
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} onNodeClick={(_, node) => { if (node.type === "pipelineComponent") setSelected(node.id); }} onNodeDragStop={(_, node) => { if (node.type === "pipelineComponent") void persistLayout({ id: node.id, x: node.position.x, y: node.position.y }); }} fitView fitViewOptions={{ padding: 0.2, maxZoom: 1 }} {...WORKFLOW_INTERACTION} zoomActivationKeyCode={["Control", "Meta"]} deleteKeyCode={collapsedBranches.size ? null : ["Backspace", "Delete"]} aria-label={t("Accepted data science workflow")}>
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cbd5e1" /><Controls showInteractive={false} position="bottom-left" />
          </ReactFlow>
        )}
      </div>

      {catalogOpen && <Drawer side="left" title={t("Add component")} onClose={() => setCatalogOpen(false)}><ComponentLibrary catalog={catalog} onAdd={addComponent} /></Drawer>}
      {selectedComponent && <Drawer side="right" title={t("Component inspector")} onClose={() => setSelected(null)}><ComponentInspector component={selectedComponent} blueprint={draft} engines={engines} outputs={componentOutputs.filter((output) => output.component_id === selectedComponent.id)} onRemove={() => removeComponent(selectedComponent.id)} onChange={(update) => updateComponent(selectedComponent.id, update)} /></Drawer>}
      {plannerOpen && <div className="absolute inset-y-0 right-0 z-40 flex w-[min(380px,92vw)] pt-[53px] shadow-2xl"><PlannerPanel runId={runId} open onToggle={() => setPlannerOpen(false)} /></div>}
    </section>
  );
}

function Drawer({ side, title, onClose, children }: { side: "left" | "right"; title: string; onClose: () => void; children: React.ReactNode }) {
  return <aside className={`absolute inset-y-0 z-40 flex w-[min(380px,92vw)] flex-col bg-surface pt-[53px] shadow-2xl ${side === "left" ? "left-0 border-r border-line" : "right-0 border-l border-line"}`}><header className="flex h-12 shrink-0 items-center border-b border-line px-4"><h3 className="flex-1 text-sm font-semibold text-ink">{title}</h3><button type="button" className="btn-ghost !px-2 !py-1" onClick={onClose}>×</button></header><div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div></aside>;
}

function ComponentLibrary({ catalog, onAdd }: { catalog: AutomationComponentDefinition[]; onAdd: (definition: AutomationComponentDefinition) => void }) {
  const [search, setSearch] = useState("");
  const visible = catalog.filter((item) => !search.trim() || `${local(item.title)} ${local(item.description)} ${item.category}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const categories = [...new Set(visible.map((item) => item.category))];
  return <div><p className="text-xs leading-relaxed text-ink-mute">{t("Components expose registered contracts. Templates and visual groups are not runtime nodes.")}</p><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("Search components")} className="field mt-3 text-xs" /><div className="mt-5 space-y-5">{categories.map((category) => <section key={category}><p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t(category)}</p><div className="space-y-2">{visible.filter((item) => item.category === category).map((item) => <button key={item.catalog_id} type="button" onClick={() => onAdd(item)} className="w-full rounded-xl border border-line px-3 py-2.5 text-left hover:border-brand-300 hover:bg-brand-50"><p className="text-xs font-semibold text-ink">{local(item.title)}</p><p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-ink-mute">{local(item.description)}</p></button>)}</div></section>)}</div></div>;
}

function ComponentInspector({ component, blueprint, engines, outputs, onRemove, onChange }: { component: PipelineComponent; blueprint: PipelineBlueprint; engines: DocumentEngine[]; outputs: PipelineOutputReference[]; onRemove: () => void; onChange: (update: (component: PipelineComponent) => PipelineComponent) => void }) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const incoming = blueprint.connections.filter((edge) => edge.target_component === component.id);
  const updateSetting = (key: string, value: unknown) => onChange((current) => ({ ...current, configured_by: "human", settings: { ...current.settings, [key]: value } }));
  return <div className="space-y-5"><section><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Overview")}</p><h4 className="mt-1 text-base font-semibold text-ink">{local(component.title)}</h4><p className="mt-1 text-xs leading-relaxed text-ink-mute">{local(component.description)}</p></section><section className="border-t border-line pt-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Inputs")}</p><ul className="mt-2 space-y-1.5">{component.inputs.map((port) => { const binding = incoming.find((edge) => edge.target_port === port.id); return <li key={port.id} className="rounded-lg bg-surface-sunken px-3 py-2"><p className="text-xs font-medium text-ink">{local(port.label)}</p><p className="mt-0.5 font-mono text-[9px] text-ink-faint">{binding ? `${binding.source_component}.${binding.source_port}` : port.required ? t("Required input missing") : t("Optional — not connected")}</p></li>; })}</ul></section><section className="border-t border-line pt-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Settings")}</p><div className="mt-2 space-y-2">{Object.entries(component.settings).map(([key, value]) => component.catalog_id === "document.extract" && key === "engine" ? <label key={key} className="block text-xs font-medium text-ink">{t("Document engine")}<select className="field mt-1 text-xs" value={String(value)} onChange={(event) => updateSetting(key, event.target.value)}>{engines.map((engine) => <option key={engine.id} value={engine.id} disabled={!engine.selectable}>{engine.label}{!engine.selectable ? ` · ${t("unavailable")}` : ""}</option>)}</select></label> : typeof value === "boolean" ? <label key={key} className="flex items-center justify-between rounded-lg border border-line px-3 py-2 text-xs text-ink"><span>{key.replaceAll("_", " ")}</span><input type="checkbox" checked={value} onChange={(event) => updateSetting(key, event.target.checked)} /></label> : <label key={key} className="block text-xs font-medium text-ink">{key.replaceAll("_", " ")}<input className="field mt-1 text-xs" value={String(value ?? "")} onChange={(event) => updateSetting(key, typeof value === "number" ? Number(event.target.value) : event.target.value)} /></label>)}</div></section><section className="border-t border-line pt-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Review policy")}</p><label className="mt-2 block text-xs font-medium text-ink">{t("After this component")}<select className="field mt-1 text-xs" value={component.control.execution} onChange={(event) => onChange((current) => ({ ...current, configured_by: "human", control: { ...current.control, execution: event.target.value as "auto" | "pause_after" } }))}><option value="auto">{t("Continue")}</option><option value="pause_after">{t("Pause for review")}</option></select></label><label className="mt-2 block text-xs font-medium text-ink">{t("Preferred reviewer")}<select className="field mt-1 text-xs" value={component.control.gate_handler} onChange={(event) => onChange((current) => ({ ...current, configured_by: "human", control: { ...current.control, gate_handler: event.target.value as "planner" | "human" } }))}><option value="planner">{t("Planner where policy permits")}</option><option value="human">{t("Human")}</option></select></label><p className="mt-2 text-[10px] leading-relaxed text-ink-faint">{t("Hard safety policy determines the allowed resolver and actions. This preference cannot override it.")}</p></section><section className="border-t border-line pt-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Outputs")}</p><div className="mt-2 space-y-2">{outputs.flatMap((output) => output.artifact_ids.map((artifactId) => <button key={artifactId} type="button" className="w-full rounded-lg border border-line px-3 py-2 text-left hover:bg-brand-50" onClick={() => void api.artifactPreview(artifactId).then(setPreview)}><p className="text-xs font-medium text-ink">{output.port_id.replaceAll("_", " ")}</p><p className="mt-0.5 text-[10px] text-brand-700">{t("Open artifact")}</p></button>))}{!outputs.some((output) => output.artifact_ids.length) && <p className="text-xs text-ink-mute">{t("No artifacts produced yet.")}</p>}</div></section>{component.id !== "data-source" && <button type="button" className="btn-ghost w-full text-stop-700" onClick={onRemove}>{t("Remove component")}</button>}{preview && <div className="rounded-xl border border-line bg-surface-sunken p-3"><div className="flex items-start"><p className="flex-1 text-xs font-semibold text-ink">{artifactTitle(preview, activeLanguage(), t)}</p><button type="button" onClick={() => setPreview(null)}>×</button></div>{preview.summary && <p className="mt-2 text-[11px] leading-relaxed text-ink-mute">{local(preview.summary)}</p>}<ArtifactMetadata preview={preview} /></div>}</div>;
}
