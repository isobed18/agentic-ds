"""Immutable compilation output for a graph-authored automation run."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.staging import PipelineNodeControl


class AutomationPlanNode(FrozenModel):
    component_id: str
    catalog_id: str
    executor: Literal["source", "staging", "document", "pipeline", "agent", "review"]
    stage_id: str | None = None
    control: PipelineNodeControl = Field(default_factory=PipelineNodeControl)


class AutomationExecutionPlan(Artifact):
    """Validated, deterministic execution projection of a saved blueprint."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.AUTOMATION_EXECUTION_PLAN
    schema_version: ClassVar[str] = "1"

    blueprint_fingerprint: str
    node_order: list[str]
    nodes: list[AutomationPlanNode]
    pause_after_component: str | None = None
    pause_after_stage: str | None = None

    def summary(self) -> dict[str, object]:
        return {
            "nodes": len(self.nodes),
            "pause_after_component": self.pause_after_component,
            "pause_after_stage": self.pause_after_stage,
        }


__all__ = ["AutomationExecutionPlan", "AutomationPlanNode"]
