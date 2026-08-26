"""Immutable compilation contracts for graph-authored automation runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.staging import PipelineNodeControl


class ExecutionEdgeBinding(FrozenModel):
    """One exact accepted ``source.output -> target.input`` declaration."""

    edge_id: str
    target_component: str
    target_port: str
    source_component: str
    source_port: str
    contract_id: str


class ExecutionOutputContract(FrozenModel):
    port_id: str
    contract_id: str
    required: bool = True
    multiple: bool = False


NodeStatus = Literal[
    "not_started",
    "queued",
    "running",
    "succeeded",
    "awaiting_review",
    "paused",
    "failed",
    "skipped",
    "cached",
]
OutputAvailability = Literal[
    "not_produced",
    "produced",
    "blocked",
    "released",
    "unavailable",
]


class NodeInputBinding(FrozenModel):
    port_id: str
    contract_id: str
    source_component: str
    source_port: str
    artifact_ids: list[str] = Field(default_factory=list)


class NodeOutputBinding(FrozenModel):
    port_id: str
    contract_id: str
    artifact_ids: list[str] = Field(default_factory=list)
    availability: OutputAvailability


class AutomationPlanNode(FrozenModel):
    """One frozen executable component instance, not merely a stage kind."""

    instance_id: str
    # Kept in v2 payloads while older clients migrate to ``instance_id``.
    component_id: str
    catalog_id: str
    catalog_version: str = "1"
    scope_id: str = "root"
    executor: Literal["source", "staging", "document", "pipeline", "agent", "review"]
    executor_id: str
    stage_id: str | None = None
    resolved_settings: dict[str, Any] = Field(default_factory=dict)
    control: PipelineNodeControl = Field(default_factory=PipelineNodeControl)
    gate_policy_id: str = "default"
    input_bindings: list[ExecutionEdgeBinding] = Field(default_factory=list)
    output_contracts: list[ExecutionOutputContract] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _read_v1_node(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        migrated = dict(raw)
        component_id = str(migrated.get("component_id") or migrated.get("instance_id") or "")
        migrated.setdefault("instance_id", component_id)
        catalog_id = str(migrated.get("catalog_id") or "unknown")
        migrated.setdefault("executor_id", f"ads.executor.{catalog_id}")
        return migrated

    @model_validator(mode="after")
    def _identity_is_exact(self) -> AutomationPlanNode:
        if self.component_id != self.instance_id:
            raise ValueError("component_id and instance_id must identify the same graph node")
        return self


class AutomationExecutionPlan(Artifact):
    """Validated execution projection of one accepted blueprint revision."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.AUTOMATION_EXECUTION_PLAN
    schema_version: ClassVar[str] = "2"

    plan_id: str
    plan_revision: int = Field(default=1, ge=1)
    contract_registry_version: Literal["1"] = "1"
    blueprint_fingerprint: str
    node_order: list[str]
    nodes: list[AutomationPlanNode]
    exact_bindings: list[ExecutionEdgeBinding] = Field(default_factory=list)
    pause_after_component: str | None = None
    pause_after_stage: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _read_v1_plan(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        migrated = dict(raw)
        fingerprint = str(migrated.get("blueprint_fingerprint") or "legacy")
        migrated.setdefault("plan_id", f"plan-{fingerprint[:16]}")
        return migrated

    def summary(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "nodes": len(self.nodes),
            "bindings": len(self.exact_bindings),
            "pause_after_component": self.pause_after_component,
            "pause_after_stage": self.pause_after_stage,
        }


class NodeAttempt(Artifact):
    """Durable state for one exact execution of one component instance."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.NODE_ATTEMPT
    schema_version: ClassVar[str] = "1"

    plan_id: str
    plan_revision: int = Field(ge=1)
    scope_id: str
    component_instance_id: str
    catalog_id: str
    attempt: int = Field(ge=1)
    started_at: datetime
    ended_at: datetime | None = None
    status: NodeStatus
    input_bindings: list[NodeInputBinding] = Field(default_factory=list)
    output_bindings: list[NodeOutputBinding] = Field(default_factory=list)
    gate_decision_artifact_id: str | None = None
    error: str | None = None
    execution_key: str

    def summary(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "scope_id": self.scope_id,
            "component_instance_id": self.component_instance_id,
            "catalog_id": self.catalog_id,
            "attempt": self.attempt,
            "status": self.status,
            "execution_key": self.execution_key,
        }


__all__ = [
    "AutomationExecutionPlan",
    "AutomationPlanNode",
    "ExecutionEdgeBinding",
    "ExecutionOutputContract",
    "NodeAttempt",
    "NodeInputBinding",
    "NodeOutputBinding",
    "NodeStatus",
    "OutputAvailability",
]
