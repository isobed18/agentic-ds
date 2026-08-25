import type { PipelineBlueprint, PipelineConnection } from "../lib/api";

export function pipelinePortsMatch(
  blueprint: PipelineBlueprint,
  sourceComponent: string,
  sourceHandle: string,
  targetComponent: string,
  targetHandle: string,
): boolean {
  const source = blueprint.components.find((component) => component.id === sourceComponent);
  const target = blueprint.components.find((component) => component.id === targetComponent);
  const sourcePort = source?.outputs.find((port) => `out:${port.id}` === sourceHandle);
  const targetPort = target?.inputs.find((port) => `in:${port.id}` === targetHandle);
  return Boolean(sourcePort && targetPort && sourcePort.data_type === targetPort.data_type);
}

export function pipelineConnectionFromHandles(edge: {
  id: string;
  source: string;
  sourceHandle?: string | null;
  target: string;
  targetHandle?: string | null;
}): PipelineConnection | null {
  if (!edge.sourceHandle?.startsWith("out:") || !edge.targetHandle?.startsWith("in:")) return null;
  return {
    id: edge.id,
    source_component: edge.source,
    source_port: edge.sourceHandle.slice(4),
    target_component: edge.target,
    target_port: edge.targetHandle.slice(3),
  };
}
