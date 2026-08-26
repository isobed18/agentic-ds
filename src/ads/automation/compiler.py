"""Compile a saved visual blueprint into a deterministic execution plan.

The compiler contains no executor code.  It binds catalog capabilities to the
existing runtime seams and freezes graph order/control policy before execution.
That keeps React Flow and planner-authored graph edits out of the trusted
runtime boundary.
"""

from __future__ import annotations

import hashlib
import json

from ads.automation.catalog import automation_component_catalog
from ads.automation.settings import validate_component_settings
from ads.contracts.automation import (
    AutomationExecutionPlan,
    AutomationPlanNode,
    ExecutionEdgeBinding,
    ExecutionOutputContract,
)
from ads.contracts.registry import canonical_contract_id
from ads.contracts.staging import PipelineBlueprint
from ads.staging.blueprint import validate_executable_blueprint

_STAGE_BY_CATALOG = {
    "data.profile_tables": "intake",
    "data.find_relationships": "schema_discovery",
    "data.integrate": "integration",
    "agent.define_problem": "problem_discovery",
    "agent.plan_validation": "validation_strategy",
    "analysis.eda": "eda",
    "analysis.leakage": "leakage_audit",
    "data.features": "feature_pipeline",
    "data.split": "splitting",
    "ml.train": "training",
    "ml.evaluate": "evaluation",
    "report.final": "report",
}


def _executor(catalog_id: str) -> str:
    if catalog_id == "data.upload":
        return "source"
    if catalog_id in {"data.profile_tables", "data.find_relationships"}:
        return "staging"
    if catalog_id == "document.extract":
        return "document"
    if catalog_id == "document.review_tables":
        return "review"
    if catalog_id.startswith("agent."):
        return "agent"
    return "pipeline"


def _topological_order(blueprint: PipelineBlueprint) -> list[str]:
    enabled = {item.id for item in blueprint.components if item.enabled}
    incoming = {component_id: 0 for component_id in enabled}
    adjacency = {component_id: set() for component_id in enabled}
    for edge in blueprint.connections:
        if edge.source_component not in enabled or edge.target_component not in enabled:
            continue
        if edge.target_component not in adjacency[edge.source_component]:
            adjacency[edge.source_component].add(edge.target_component)
            incoming[edge.target_component] += 1
    ready = sorted(component_id for component_id, count in incoming.items() if count == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(adjacency[current]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(enabled):
        raise ValueError("automation graph must be acyclic")
    return ordered


def compile_automation_plan(blueprint: PipelineBlueprint) -> AutomationExecutionPlan:
    """Validate and freeze an executable blueprint without running any node."""
    validate_executable_blueprint(blueprint)
    order = _topological_order(blueprint)
    components = {item.id: item for item in blueprint.components if item.enabled}
    catalog = {definition.catalog_id: definition for definition in automation_component_catalog()}
    exact_bindings: list[ExecutionEdgeBinding] = []
    for edge in blueprint.connections:
        if edge.source_component not in components or edge.target_component not in components:
            continue
        source = components[edge.source_component]
        target = components[edge.target_component]
        source_port = next(port for port in source.outputs if port.id == edge.source_port)
        target_port = next(port for port in target.inputs if port.id == edge.target_port)
        source_contract = canonical_contract_id(source_port.data_type)
        target_contract = canonical_contract_id(target_port.data_type)
        if source_contract != target_contract:
            raise ValueError(f"edge {edge.id!r} has incompatible registered contracts")
        exact_bindings.append(
            ExecutionEdgeBinding(
                edge_id=edge.id,
                target_component=edge.target_component,
                target_port=edge.target_port,
                source_component=edge.source_component,
                source_port=edge.source_port,
                contract_id=target_contract,
            )
        )

    nodes: list[AutomationPlanNode] = []
    pause_component: str | None = None
    pause_stage: str | None = None
    for component_id in order:
        component = components[component_id]
        catalog_id = component.catalog_id or (
            "data.upload" if component.kind == "data_source" else component.kind
        )
        definition = catalog.get(catalog_id)
        if definition is None:
            raise ValueError(f"component {component_id!r} has no registered catalog definition")
        stage_id = _STAGE_BY_CATALOG.get(catalog_id)
        nodes.append(
            AutomationPlanNode(
                instance_id=component_id,
                component_id=component_id,
                catalog_id=catalog_id,
                catalog_version=definition.catalog_version,
                scope_id=component.branch_id or "root",
                executor=_executor(catalog_id),
                executor_id=definition.executor_id,
                stage_id=stage_id,
                resolved_settings=validate_component_settings(catalog_id, component.settings),
                control=component.control,
                gate_policy_id=(
                    "hard.leakage@1"
                    if catalog_id == "analysis.leakage"
                    else definition.gate_policy_id
                ),
                input_bindings=[
                    binding
                    for binding in exact_bindings
                    if binding.target_component == component_id
                ],
                output_contracts=[
                    ExecutionOutputContract(
                        port_id=port.id,
                        contract_id=canonical_contract_id(port.data_type),
                        required=port.required,
                        multiple=port.multiple,
                    )
                    for port in component.outputs
                ],
            )
        )
        if component.control.execution == "pause_after" and pause_component is None:
            if not definition.pausable:
                raise ValueError(
                    f"component {component_id!r} cannot be an execution pause boundary"
                )
            pause_component = component_id
            pause_stage = stage_id

    fingerprint_payload = blueprint.model_dump(mode="json", exclude={"name", "revision"})
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AutomationExecutionPlan(
        plan_id=f"plan-{fingerprint[:16]}",
        blueprint_fingerprint=fingerprint,
        node_order=order,
        nodes=nodes,
        exact_bindings=exact_bindings,
        pause_after_component=pause_component,
        pause_after_stage=pause_stage,
    )


__all__ = ["compile_automation_plan"]
