import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  addEdge,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  api,
  type ArtifactPreview,
  type AutomationComponentDefinition,
  type DocumentEngine,
  type PipelineBlueprint,
  type PipelineComponent,
  type PipelineConnection,
  type PipelineOutputReference,
} from "../lib/api";
import { activeLanguage, t } from "../lib/i18n";
import { Badge, Spinner, cx } from "./ui";
import { pipelineConnectionFromHandles, pipelinePortsMatch } from "./pipelineGraph";

interface ComponentNodeData extends Record<string, unknown> {
  component: PipelineComponent;
  expanded: boolean;
  onToggle: (id: string) => void;
  outputs: PipelineOutputReference[];
}

type ComponentFlowNode = Node<ComponentNodeData, "pipelineComponent">;

const NODE_WIDTH = 270;
const COLLAPSED_HEIGHT = 106;
const EXPANDED_HEIGHT = 248;

function local(value: { en: string; tr: string }): string {
  return activeLanguage() === "tr" ? value.tr : value.en;
}

function evidenceTone(layer: PipelineComponent["evidence_layer"]): "ok" | "brand" | "warn" | "neutral" {
  if (layer === "measured") return "ok";
  if (layer === "agent_proposal") return "brand";
  if (layer === "human_decision") return "warn";
  return "neutral";
}

function evidenceLabel(layer: PipelineComponent["evidence_layer"]): string {
  return t({
    measured: "Measured",
    agent_proposal: "Agent proposal",
    human_decision: "Human decision",
    executor: "Execution",
  }[layer]);
}

function ComponentNode({ data, selected }: NodeProps<ComponentFlowNode>) {
  const { component, expanded, onToggle, outputs } = data;
  const height = expanded ? EXPANDED_HEIGHT : COLLAPSED_HEIGHT;
  const artifactCount = new Set(outputs.flatMap((output) => output.artifact_ids)).size;
  return (
    <article
      className={cx(
        "w-[270px] overflow-hidden rounded-xl border bg-surface shadow-card transition-all",
        selected ? "border-brand-500 ring-2 ring-brand-100" : "border-line",
        !component.enabled && "opacity-55",
      )}
      style={{ height }}
    >
      {component.inputs.map((port, index) => (
        <Handle
          key={`in:${port.id}`}
          id={`in:${port.id}`}
          type="target"
          position={Position.Left}
          style={{ top: `${32 + ((index + 1) * 52) / (component.inputs.length + 1)}%` }}
          className="!h-2.5 !w-2.5 !border-2 !border-white !bg-brand-500"
        />
      ))}
      {component.outputs.map((port, index) => (
        <Handle
          key={`out:${port.id}`}
          id={`out:${port.id}`}
          type="source"
          position={Position.Right}
          style={{ top: `${32 + ((index + 1) * 52) / (component.outputs.length + 1)}%` }}
          className="!h-2.5 !w-2.5 !border-2 !border-white !bg-brand-500"
        />
      ))}

      <button
        type="button"
        onClick={(event) => { event.stopPropagation(); onToggle(component.id); }}
        className="flex w-full items-start gap-2 px-3.5 py-3 text-left hover:bg-surface-sunken"
        aria-expanded={expanded}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-semibold text-ink">{local(component.title)}</span>
          <span className="mt-1 block truncate text-[10px] text-ink-mute">{local(component.description)}</span>
        </span>
        <span className="text-sm text-ink-faint">{expanded ? "−" : "+"}</span>
      </button>
      <div className="flex flex-wrap gap-1 border-t border-line-soft px-3.5 py-2">
        <Badge tone={evidenceTone(component.evidence_layer)}>{evidenceLabel(component.evidence_layer)}</Badge>
        <Badge tone={component.enabled ? "ok" : "neutral"}>{t(component.enabled ? "Enabled" : "Disabled")}</Badge>
        {component.control.execution === "pause_after" && <Badge tone="warn">{t("Pause after")}</Badge>}
        {component.control.gate_handler === "planner" && <Badge tone="brand">{t("Planner handles gates")}</Badge>}
        {component.branch_id && <Badge tone="brand">{component.branch_id}</Badge>}
        {artifactCount > 0 && <Badge tone="neutral">{artifactCount} {t("artifacts")}</Badge>}
        {outputs.some((output) => output.status === "needs_review") && <Badge tone="warn">{t("Review output")}</Badge>}
        {outputs.length > 0 && outputs.every((output) => output.status === "ready") && <Badge tone="ok">{t("Outputs ready")}</Badge>}
      </div>
      {expanded && (
        <div className="grid grid-cols-2 gap-2 border-t border-line-soft px-3.5 py-3">
          <PortList title={t("Inputs")} ports={component.inputs} side="input" />
          <PortList title={t("Outputs")} ports={component.outputs} side="output" outputs={outputs} />
        </div>
      )}
    </article>
  );
}

function PortList({ title, ports, side, outputs = [] }: {
  title: string;
  ports: PipelineComponent["inputs"];
  side: "input" | "output";
  outputs?: PipelineOutputReference[];
}) {
  return (
    <div>
      <p className="text-[9px] font-semibold uppercase tracking-wide text-ink-faint">{title}</p>
      <ul className="mt-1.5 space-y-1">
        {ports.map((port) => (
          <li key={port.id} className="rounded-md bg-surface-sunken px-2 py-1.5">
            <p className="truncate text-[9.5px] font-medium text-ink" title={local(port.label)}>{local(port.label)}</p>
            <p className="truncate font-mono text-[8px] text-ink-faint">{port.data_type}</p>
            {outputs.find((output) => output.port_id === port.id) && (
              <p className="mt-0.5 truncate text-[8px] font-medium text-brand-700">
                {t(outputs.find((output) => output.port_id === port.id)!.status.replace("_", " "))}
              </p>
            )}
          </li>
        ))}
        {!ports.length && <li className="text-[9px] text-ink-faint">{t(side === "input" ? "Starts here" : "No output")}</li>}
      </ul>
    </div>
  );
}

const nodeTypes = { pipelineComponent: ComponentNode };

function toEdges(blueprint: PipelineBlueprint): Edge[] {
  return blueprint.connections.map((connection) => ({
    id: connection.id,
    source: connection.source_component,
    sourceHandle: `out:${connection.source_port}`,
    target: connection.target_component,
    targetHandle: `in:${connection.target_port}`,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8", width: 14, height: 14 },
    style: { stroke: "#94a3b8", strokeWidth: 1.6 },
  }));
}

async function layoutNodes(
  blueprint: PipelineBlueprint,
  expanded: Set<string>,
  componentOutputs: PipelineOutputReference[],
  onToggle: (id: string) => void,
): Promise<ComponentFlowNode[]> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  const graph = await elk.layout({
    id: "pipeline-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "58",
      "elk.layered.spacing.nodeNodeBetweenLayers": "115",
      "elk.padding": "[top=30,left=30,bottom=30,right=30]",
    },
    children: blueprint.components.map((component) => ({
      id: component.id,
      width: NODE_WIDTH,
      height: expanded.has(component.id) ? EXPANDED_HEIGHT : COLLAPSED_HEIGHT,
    })),
    edges: blueprint.connections.map((connection) => ({
      id: connection.id,
      sources: [connection.source_component],
      targets: [connection.target_component],
    })),
  });
  const positions = new Map(
    (graph.children ?? []).map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );
  return blueprint.components.map((component) => ({
    id: component.id,
    type: "pipelineComponent",
    position: positions.get(component.id) ?? { x: 0, y: 0 },
    data: {
      component,
      expanded: expanded.has(component.id),
      onToggle,
      outputs: componentOutputs.filter((output) => output.component_id === component.id),
    },
  }));
}

export function PipelineBuilder({ runId = null, baseArtifactId = null, blueprint, componentOutputs = [], plannerPanel, onSaved, onChange }: {
  runId?: string | null;
  baseArtifactId?: string | null;
  blueprint: PipelineBlueprint;
  componentOutputs?: PipelineOutputReference[];
  plannerPanel?: ReactNode;
  onSaved?: (workspace: Awaited<ReturnType<typeof api.updateStagingPipeline>>) => void;
  onChange?: (blueprint: PipelineBlueprint) => void;
}) {
  const [draft, setDraft] = useState(blueprint);
  const [nodes, setNodes] = useState<ComponentFlowNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>(() => toEdges(blueprint));
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(["understand-documents", "planner"]));
  const [selected, setSelected] = useState<string | null>("understand-documents");
  const [engines, setEngines] = useState<DocumentEngine[]>([]);
  const [catalog, setCatalog] = useState<AutomationComponentDefinition[]>([]);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<"node" | "planner">("node");

  useEffect(() => {
    void api.automationComponents().then((value) => {
      setEngines(value.document_engines);
      setCatalog(value.components);
    });
  }, []);
  useEffect(() => {
    setDraft(blueprint);
    setEdges(toEdges(blueprint));
    setSaved(true);
  }, [blueprint]);

  const toggle = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const layoutKey = `${draft.components.map((item) => `${item.id}:${item.enabled}`).join("|")}:${[...expanded].sort().join("|")}:${componentOutputs.map((output) => `${output.component_id}:${output.port_id}:${output.status}`).join("|")}`;
  useEffect(() => {
    let cancelled = false;
    void layoutNodes(draft, expanded, componentOutputs, toggle).then((value) => { if (!cancelled) setNodes(value); });
    return () => { cancelled = true; };
  }, [layoutKey]);

  const selectedComponent = useMemo(
    () => draft.components.find((component) => component.id === selected) ?? null,
    [draft, selected],
  );

  function updateComponent(componentId: string, update: (component: PipelineComponent) => PipelineComponent) {
    setDraft((current) => ({
      ...current,
      components: current.components.map((component) => component.id === componentId ? update(component) : component),
    }));
    setSaved(false);
  }

  function addComponent(definition: AutomationComponentDefinition) {
    if (!definition.repeatable && draft.components.some((item) => (
      item.catalog_id === definition.catalog_id || item.kind === definition.kind
    ))) {
      setError(t("This component can only be added once."));
      return;
    }
    const stem = definition.catalog_id.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const suffix = crypto.randomUUID().slice(0, 6);
    const component: PipelineComponent = {
      id: `${stem}-${suffix}`,
      kind: definition.kind,
      title: definition.title,
      description: definition.description,
      inputs: definition.inputs,
      outputs: definition.outputs,
      settings: { ...definition.default_settings },
      control: { execution: "auto", gate_handler: "planner", max_retries: null },
      enabled: true,
      optional: false,
      evidence_layer: definition.evidence_layer,
      configured_by: "human",
      catalog_id: definition.catalog_id,
      branch_id: null,
      group_id: null,
    };
    setDraft((current) => ({ ...current, components: [...current.components, component] }));
    setSelected(component.id);
    setSaved(false);
    setError(null);
  }

  function removeComponent(componentId: string) {
    if (componentId === "data-source") return;
    setDraft((current) => ({
      ...current,
      components: current.components.filter((component) => component.id !== componentId),
    }));
    setEdges((current) => current.filter((edge) => edge.source !== componentId && edge.target !== componentId));
    setSelected(null);
    setSaved(false);
  }

  async function runDocuments() {
    if (!runId) return;
    setBusy(true);
    setError(null);
    try {
      if (!saved) await save();
      const workspace = await api.runDocumentUnderstanding(runId);
      onSaved?.(workspace);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  function connect(connection: Connection) {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return;
    if (!pipelinePortsMatch(
      draft, connection.source, connection.sourceHandle, connection.target, connection.targetHandle,
    )) {
      setError(t("Connections require matching output and input types."));
      return;
    }
    setError(null);
    setEdges((current) => addEdge({ ...connection, id: `human:${crypto.randomUUID()}`, type: "smoothstep" }, current));
    setSaved(false);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const connections = edges.map(pipelineConnectionFromHandles).filter((item): item is PipelineConnection => item !== null);
      const next = { ...draft, connections };
      if (runId && baseArtifactId) {
        const workspace = await api.updateStagingPipeline(runId, baseArtifactId, next);
        onSaved?.(workspace);
      }
      onChange?.(next);
      setSaved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <header className="mb-3 flex flex-wrap items-start gap-3">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{t("Build the pipeline")}</h2>
            <Badge tone="brand">{t("MVP blueprint")}</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-xs text-ink-mute">
            {t("Open components to inspect their inputs and outputs. Drag matching ports to connect them, then save the blueprint before running.")}
          </p>
        </div>
        <button onClick={() => void save()} disabled={busy || saved} className="btn-primary !py-1.5 text-xs">
          {busy ? t("Saving…") : saved ? t("Pipeline saved") : runId ? t("Save pipeline") : t("Use this pipeline")}
        </button>
      </header>

      {error && <p className="mb-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{error}</p>}
      <div className="grid min-h-[650px] gap-3 xl:grid-cols-[250px_minmax(0,1fr)_330px]">
        <ComponentLibrary
          catalog={catalog}
          search={catalogSearch}
          onSearch={setCatalogSearch}
          onAdd={addComponent}
        />
        <div className="h-[650px] overflow-hidden rounded-xl border border-line bg-surface-sunken">
          {nodes.length === 0 ? <div className="flex h-full items-center justify-center"><Spinner label={t("Laying out the pipeline…")} /></div> : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onConnect={connect}
              onEdgesChange={(changes) => {
                if (changes.some((change) => change.type === "remove")) setSaved(false);
                setEdges((current) => {
                  const removed = new Set(changes.filter((change) => change.type === "remove").map((change) => change.id));
                  return current.filter((edge) => !removed.has(edge.id));
                });
              }}
              onNodeClick={(_, node) => { setSelected(node.id); setInspectorTab("node"); }}
              fitView
              fitViewOptions={{ padding: 0.14, maxZoom: 1 }}
              minZoom={0.2}
              maxZoom={1.4}
              panOnScroll
              zoomOnScroll={false}
              deleteKeyCode={["Backspace", "Delete"]}
              aria-label={t("Customizable data-to-ML pipeline")}
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cbd5e1" />
              <Controls showInteractive={false} position="bottom-left" />
            </ReactFlow>
          )}
        </div>

        <aside className="min-h-0 overflow-y-auto rounded-xl border border-line bg-surface-sunken">
          {plannerPanel && (
            <div className="sticky top-0 z-10 flex gap-1 border-b border-line bg-surface px-2 py-2">
              {(["node", "planner"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setInspectorTab(tab)}
                  className={cx("flex-1 rounded-md px-2 py-1.5 text-[11px] font-semibold", inspectorTab === tab ? "bg-brand-50 text-brand-700" : "text-ink-mute hover:bg-surface-sunken")}
                >
                  {t(tab === "node" ? "Component" : "Planner")}
                </button>
              ))}
            </div>
          )}
          {inspectorTab === "planner" && plannerPanel ? plannerPanel : (
            <ComponentPreferences
              component={selectedComponent}
              engines={engines}
              outputs={componentOutputs.filter((output) => output.component_id === selectedComponent?.id)}
              busy={busy}
              canRunDocuments={Boolean(runId)}
              onRunDocuments={() => void runDocuments()}
              onRemove={() => selectedComponent && removeComponent(selectedComponent.id)}
              onChange={(update) => selectedComponent && updateComponent(selectedComponent.id, update)}
            />
          )}
        </aside>
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-[10px] text-ink-mute">
        <Badge tone="ok">{t("Measured")}</Badge><span>{t("host measurements")}</span>
        <Badge tone="brand">{t("Agent proposal")}</Badge><span>{t("review before accepting")}</span>
        <Badge tone="warn">{t("Human decision")}</Badge><span>{t("becomes active only after acceptance")}</span>
      </div>
    </section>
  );
}

function ComponentLibrary({ catalog, search, onSearch, onAdd }: {
  catalog: AutomationComponentDefinition[];
  search: string;
  onSearch: (value: string) => void;
  onAdd: (definition: AutomationComponentDefinition) => void;
}) {
  const needle = search.trim().toLocaleLowerCase();
  const visible = catalog.filter((item) => (
    !needle
    || local(item.title).toLocaleLowerCase().includes(needle)
    || local(item.description).toLocaleLowerCase().includes(needle)
    || item.category.includes(needle)
  ));
  const categories = [...new Set(visible.map((item) => item.category))];
  return (
    <aside className="h-[650px] overflow-y-auto rounded-xl border border-line bg-surface p-3">
      <div className="sticky top-0 z-10 bg-surface pb-2">
        <h3 className="text-xs font-semibold text-ink">{t("Components")}</h3>
        <p className="mt-1 text-[10px] leading-relaxed text-ink-mute">
          {t("Add one responsibility at a time. Connections define the data contract.")}
        </p>
        <input
          value={search}
          onChange={(event) => onSearch(event.target.value)}
          placeholder={t("Search components")}
          className="field mt-2 !py-1.5 text-xs"
        />
      </div>
      <div className="space-y-4 pt-1">
        {categories.map((category) => (
          <section key={category}>
            <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
              {t(category)}
            </p>
            <div className="space-y-1.5">
              {visible.filter((item) => item.category === category).map((item) => (
                <button
                  key={item.catalog_id}
                  type="button"
                  onClick={() => onAdd(item)}
                  className="w-full rounded-lg border border-line bg-surface px-2.5 py-2 text-left hover:border-brand-300 hover:bg-brand-50"
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink">{local(item.title)}</span>
                    <span className="text-base leading-none text-brand-600">+</span>
                  </span>
                  <span className="mt-0.5 block line-clamp-2 text-[9px] leading-relaxed text-ink-mute">{local(item.description)}</span>
                  <span className="mt-1.5 block font-mono text-[8px] text-ink-faint">
                    {item.inputs.length} {t("in")} · {item.outputs.length} {t("out")}
                  </span>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
    </aside>
  );
}

function ComponentPreferences({
  component,
  engines,
  outputs,
  busy,
  canRunDocuments,
  onRunDocuments,
  onRemove,
  onChange,
}: {
  component: PipelineComponent | null;
  engines: DocumentEngine[];
  outputs: PipelineOutputReference[];
  busy: boolean;
  canRunDocuments: boolean;
  onRunDocuments: () => void;
  onRemove: () => void;
  onChange: (update: (component: PipelineComponent) => PipelineComponent) => void;
}) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  useEffect(() => {
    setPreview(null);
    setPreviewError(null);
  }, [component?.id]);
  if (!component) {
    return <aside className="rounded-xl border border-line bg-surface-sunken p-4 text-xs text-ink-mute">{t("Select a component to configure it.")}</aside>;
  }
  const engineId = String(component.settings.engine ?? "");
  const engine = engines.find((item) => item.id === engineId);
  const updateSetting = (key: string, value: unknown) => onChange((current) => ({
    ...current,
    configured_by: "human",
    settings: { ...current.settings, [key]: value },
  }));
  return (
    <div className="p-4">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">{local(component.title)}</h3>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{local(component.description)}</p>
        </div>
        <button
          onClick={() => onChange((current) => ({ ...current, enabled: !current.enabled, configured_by: "human" }))}
          className={cx("rounded-full px-2.5 py-1 text-[10px] font-semibold", component.enabled ? "bg-ok-50 text-ok-700" : "bg-line-soft text-ink-mute")}
        >
          {t(component.enabled ? "Enabled" : "Disabled")}
        </button>
      </div>

      <div className="mt-4 space-y-2 border-t border-line pt-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Execution control")}</p>
        <SelectSetting
          label={t("After this component")}
          value={component.control.execution}
          values={["auto", "pause_after"]}
          onChange={(value) => onChange((current) => ({ ...current, configured_by: "human", control: { ...current.control, execution: value as "auto" | "pause_after" } }))}
        />
        <SelectSetting
          label={t("Ordinary gate handled by")}
          value={component.control.gate_handler}
          values={["planner", "human"]}
          onChange={(value) => onChange((current) => ({ ...current, configured_by: "human", control: { ...current.control, gate_handler: value as "planner" | "human" } }))}
        />
        <label className="block text-[11px] font-medium text-ink">
          {t("Retry limit")}
          <input
            className="field mt-1 text-xs"
            type="number"
            min={0}
            max={9}
            value={component.control.max_retries ?? ""}
            placeholder={t("Use policy default")}
            onChange={(event) => onChange((current) => ({
              ...current,
              configured_by: "human",
              control: {
                ...current.control,
                max_retries: event.target.value === "" ? null : Number(event.target.value),
              },
            }))}
          />
        </label>
        <p className="text-[9px] leading-relaxed text-ink-faint">{t("Hard safety rules always remain active.")}</p>
      </div>

      {component.kind === "document_understanding" && (
        <div className="mt-4 space-y-3 border-t border-line pt-3">
          <label className="block text-[11px] font-medium text-ink">
            {t("Document engine")}
            <select value={engineId} onChange={(event) => updateSetting("engine", event.target.value)} className="field mt-1.5 text-xs">
              {engines.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.selectable}>
                  {item.label}{!item.selectable ? ` · ${t("comparison only")}` : item.available ? "" : ` · ${t("not installed")}`}
                </option>
              ))}
            </select>
          </label>
          {engine && (
            <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={engine.selectable && engine.available ? "ok" : "warn"}>
                  {t(!engine.selectable ? "Excluded by dependency policy" : engine.available ? "Available" : "Needs installation")}
                </Badge>
                <span className="text-[10px] text-ink-faint">{t("local")}</span>
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-ink-mute">{local(engine.description)}</p>
            </div>
          )}
          <Choice label={t("OCR when needed")} checked={component.settings.ocr !== "never"} onChange={(value) => updateSetting("ocr", value ? "auto" : "never")} />
          <Choice label={t("Extract candidate tables")} checked={component.settings.extract_tables === true} onChange={(value) => updateSetting("extract_tables", value)} />
          <Choice label={t("Extract figures and charts")} checked={component.settings.extract_figures === true} onChange={(value) => updateSetting("extract_figures", value)} />
          <Choice label={t("Allow accepted document tables into training data")} checked={component.settings.use_extracted_tables_for_training === true} onChange={(value) => updateSetting("use_extracted_tables_for_training", value)} />
          <p className="rounded-lg bg-warn-50 px-3 py-2 text-[10px] leading-relaxed text-warn-700">
            {t("Document tables remain candidates until a person accepts their schema and provenance.")}
          </p>
          <button
            type="button"
            disabled={!canRunDocuments || busy || !engine?.available}
            onClick={onRunDocuments}
            className="btn-primary w-full !py-1.5 text-xs"
          >
            {busy ? t("Running…") : t("Run document understanding")}
          </button>
        </div>
      )}

      {component.kind === "report" && (
        <label className="mt-4 block border-t border-line pt-3 text-[11px] font-medium text-ink">
          {t("Report instructions")}
          <textarea
            value={String(component.settings.instructions ?? "")}
            onChange={(event) => updateSetting("instructions", event.target.value)}
            rows={4}
            className="field mt-1.5 resize-none text-xs"
            placeholder={t("Explain what this report should help a person decide.")}
          />
        </label>
      )}

      {component.kind === "problem_discovery" && (
        <div className="mt-4 space-y-2 border-t border-line pt-3">
          <TextSetting label={t("Problem title")} value={component.settings.problem_title} onChange={(value) => updateSetting("problem_title", value)} />
          <TextSetting label={t("Target column")} value={component.settings.target_column} onChange={(value) => updateSetting("target_column", value || null)} />
          <SelectSetting label={t("Task type")} value={component.settings.task_type} values={["binary_classification", "multiclass_classification", "regression", "anomaly_detection"]} onChange={(value) => updateSetting("task_type", value)} />
          <SelectSetting label={t("Primary metric")} value={component.settings.primary_metric} values={["roc_auc", "average_precision", "f1", "balanced_accuracy", "accuracy", "rmse", "mae", "r2", "mape", "silhouette"]} onChange={(value) => updateSetting("primary_metric", value)} />
        </div>
      )}

      {component.kind === "validation" && (
        <label className="mt-4 block border-t border-line pt-3 text-[11px] font-medium text-ink">
          {t("Cross-validation folds")}
          <input className="field mt-1.5 text-xs" type="number" min={2} max={20} value={Number(component.settings.n_folds ?? 5)} onChange={(event) => updateSetting("n_folds", Number(event.target.value))} />
        </label>
      )}

      {component.kind === "training" && (
        <div className="mt-4 space-y-2 border-t border-line pt-3">
          <TextSetting label={t("Preferred model family")} value={component.settings.preferred_family} onChange={(value) => updateSetting("preferred_family", value)} />
          <label className="block text-[11px] font-medium text-ink">{t("Candidate limit")}<input className="field mt-1 text-xs" type="number" min={1} max={20} value={Number(component.settings.candidate_limit ?? 2)} onChange={(event) => updateSetting("candidate_limit", Number(event.target.value))} /></label>
        </div>
      )}

      {!["document_understanding", "report", "problem_discovery", "validation", "training"].includes(component.kind) && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Current preferences")}</p>
          <dl className="mt-2 space-y-1.5">
            {Object.entries(component.settings).map(([key, value]) => (
              <div key={key} className="flex gap-2 text-[10px]"><dt className="font-mono text-ink-mute">{key}</dt><dd className="ml-auto text-right text-ink">{JSON.stringify(value)}</dd></div>
            ))}
            {!Object.keys(component.settings).length && <p className="text-[10px] text-ink-faint">{t("No overrides yet")}</p>}
          </dl>
        </div>
      )}

      <ArtifactInspector
        outputs={outputs}
        preview={preview}
        error={previewError}
        onOpen={(artifactId) => {
          setPreview(null);
          setPreviewError(null);
          void api.artifactPreview(artifactId)
            .then(setPreview)
            .catch((caught) => setPreviewError(caught instanceof Error ? caught.message : String(caught)));
        }}
      />

      {component.id !== "data-source" && (
        <button type="button" onClick={onRemove} className="mt-4 w-full rounded-lg border border-stop-200 px-3 py-1.5 text-[10px] font-semibold text-stop-700 hover:bg-stop-50">
          {t("Remove component")}
        </button>
      )}
    </div>
  );
}

function TextSetting({ label, value, onChange }: { label: string; value: unknown; onChange: (value: string) => void }) {
  return <label className="block text-[11px] font-medium text-ink">{label}<input className="field mt-1 text-xs" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} /></label>;
}

function SelectSetting({ label, value, values, onChange }: { label: string; value: unknown; values: string[]; onChange: (value: string) => void }) {
  return <label className="block text-[11px] font-medium text-ink">{label}<select className="field mt-1 text-xs" value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}><option value="">{t("Choose…")}</option>{values.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>;
}

function ArtifactInspector({ outputs, preview, error, onOpen }: {
  outputs: PipelineOutputReference[];
  preview: ArtifactPreview | null;
  error: string | null;
  onOpen: (artifactId: string) => void;
}) {
  const artifacts = [
    ...new Map(
      outputs.flatMap((output) => output.artifact_ids.map((artifactId) => [
        artifactId,
        { artifactId, output },
      ] as const)),
    ).values(),
  ];
  return (
    <section className="mt-4 border-t border-line pt-3">
      <div className="flex items-center gap-2"><h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Artifacts")}</h4><Badge tone={artifacts.length ? "ok" : "neutral"}>{artifacts.length}</Badge></div>
      <div className="mt-2 space-y-1.5">
        {artifacts.map(({ artifactId, output }) => <button key={`${output.port_id}:${artifactId}`} onClick={() => onOpen(artifactId)} className="w-full rounded-lg border border-line bg-surface px-2.5 py-2 text-left hover:border-brand-300"><span className="block text-[10px] font-semibold text-ink">{local(output.summary)}</span><span className="mt-0.5 block truncate font-mono text-[8px] text-ink-faint">{artifactId}</span></button>)}
        {!artifacts.length && <p className="rounded-lg bg-surface px-2.5 py-2 text-[10px] text-ink-faint">{t("Run this component to attach inspectable outputs here.")}</p>}
      </div>
      {error && <p className="mt-2 rounded-lg bg-stop-50 px-2.5 py-2 text-[10px] text-stop-700">{error}</p>}
      {preview && <ArtifactPreviewCard preview={preview} />}
    </section>
  );
}

function ArtifactPreviewCard({ preview }: { preview: ArtifactPreview }) {
  return (
    <div className="mt-2 rounded-lg border border-brand-200 bg-brand-50 p-2.5 text-[10px] text-ink-soft">
      <p className="font-semibold text-ink">{preview.artifact_type.replaceAll("_", " ")}</p>
      {preview.title && <p className="mt-2 text-xs font-semibold text-ink">{local(preview.title)}</p>}
      {preview.summary && <p className="mt-1 leading-relaxed text-ink-soft">{local(preview.summary)}</p>}
      {preview.findings && preview.findings.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-brand-100 pt-2">
          {preview.findings.map((finding, index) => <li key={index}>• {local(finding)}</li>)}
        </ul>
      )}
      {preview.verification_questions && preview.verification_questions.length > 0 && (
        <div className="mt-2 rounded bg-warn-50 px-2 py-1.5 text-warn-700">
          {preview.verification_questions.map((question, index) => <p key={index}>{local(question)}</p>)}
        </div>
      )}
      {preview.engine && <p className="mt-1">{t("Engine")}: {preview.engine}</p>}
      {preview.documents?.map((document) => (
        <div key={document.source_file} className="mt-2 border-t border-brand-100 pt-2">
          <p className="font-medium text-ink">{document.title || document.source_file}</p>
          <p className="mt-0.5 text-ink-mute">{document.page_count} {t("pages")} · {document.tables.length} {t("tables")} · {document.figures.length} {t("figures")}</p>
          {document.tables.map((table, index) => <p key={index} className="mt-1 rounded bg-surface px-2 py-1 font-mono text-[8px]">{String(table.title || `${t("Table")} ${index + 1}`)} · {String(table.row_count ?? 0)} {t("rows")}</p>)}
        </div>
      ))}
    </div>
  );
}

function Choice({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-[11px] text-ink-soft">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-0.5" />
      <span>{label}</span>
    </label>
  );
}
