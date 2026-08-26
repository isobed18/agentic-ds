"""Durable output of the pre-pipeline data-understanding workspace.

Staging is not transient UI state.  It combines the measured first two stages,
the planner's explicitly non-authoritative interpretation, the conversation,
and the configuration a person may accept before execution.  New planner turns
write new immutable snapshots so the complete history remains auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.documents import DocumentExtractionSummary
from ads.contracts.registry import canonical_contract_id


class LocalizedText(FrozenModel):
    """English canonical text plus Turkish from the same agent response."""

    en: str = Field(min_length=1, max_length=20_000)
    tr: str = Field(min_length=1, max_length=20_000)


class StagingMessage(FrozenModel):
    role: Literal["user", "planner"]
    content: LocalizedText
    model: str | None = None


class RelationshipExplanation(FrozenModel):
    """Agent prose attached to an exact measured relationship."""

    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    cardinality: str
    overlap_rate: float = Field(ge=0.0, le=1.0)
    orphan_rate: float = Field(ge=0.0, le=1.0)
    explanation: LocalizedText
    why_it_matters: LocalizedText
    verification_question: LocalizedText
    evidence_status: Literal["measured_with_agent_interpretation"] = (
        "measured_with_agent_interpretation"
    )


class StagingReport(FrozenModel):
    title: LocalizedText
    summary: LocalizedText
    findings: list[LocalizedText] = Field(default_factory=list)
    verification_questions: list[LocalizedText] = Field(default_factory=list)


class StagingReportArtifact(Artifact):
    """One immutable report attached to the graph component that produced it."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.STAGING_REPORT
    schema_version: ClassVar[str] = "1"

    producer_component_id: str
    title: LocalizedText
    summary_text: LocalizedText
    findings: list[LocalizedText] = Field(default_factory=list)
    verification_questions: list[LocalizedText] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "producer_component_id": self.producer_component_id,
            "title": self.title.en,
            "findings": len(self.findings),
        }


PipelineDataType = str


class PipelinePort(FrozenModel):
    """A named, typed component boundary shown directly in the builder."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: LocalizedText
    data_type: PipelineDataType
    required: bool = True
    multiple: bool = False

    @field_validator("data_type")
    @classmethod
    def _registered_contract(cls, value: str) -> str:
        canonical_contract_id(value)
        return value


class PipelineNodeControl(FrozenModel):
    """Pre-run control policy for one executable graph component.

    ``pause_after`` is deliberately an execution boundary, not a gate verdict:
    the node runs, its immutable artifacts become inspectable, and the graph
    pauses before downstream work.  ``gate_handler`` only chooses who may answer
    an ordinary escalation; deterministic hard rules remain non-overridable.
    """

    execution: Literal["auto", "pause_after"] = "auto"
    gate_handler: Literal["human", "planner"] = "planner"
    max_retries: int | None = Field(default=None, ge=0, le=9)


class PipelineComponent(FrozenModel):
    """One configurable unit in the pre-run pipeline blueprint.

    ``evidence_layer`` is domain state rather than presentation styling. It prevents
    an agent recommendation from silently becoming a measured fact or accepted
    human decision when the graph is rendered or persisted.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    kind: Literal[
        "data_source",
        "intake",
        "schema_discovery",
        "document_understanding",
        "integration",
        "report",
        "planner",
        "ml_pipeline",
        "human_review",
        "problem_discovery",
        "validation",
        "analysis",
        "feature_engineering",
        "splitting",
        "training",
        "evaluation",
        "template",
    ]
    title: LocalizedText
    description: LocalizedText
    inputs: list[PipelinePort] = Field(default_factory=list)
    outputs: list[PipelinePort] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    control: PipelineNodeControl = Field(default_factory=PipelineNodeControl)
    enabled: bool = True
    optional: bool = False
    evidence_layer: Literal["measured", "agent_proposal", "human_decision", "executor"]
    configured_by: Literal["system", "planner", "human"] = "system"
    catalog_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    branch_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    group_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")


class PipelineConnection(FrozenModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]+$")
    source_component: str
    source_port: str
    target_component: str
    target_port: str


class PipelineOutputReference(FrozenModel):
    """Runtime state for one exact output port in a persisted blueprint."""

    component_id: str
    port_id: str
    data_type: PipelineDataType
    status: Literal["ready", "pending", "needs_review", "unavailable", "not_started"]
    artifact_ids: list[str] = Field(default_factory=list)
    summary: LocalizedText


class PipelineBlueprint(FrozenModel):
    """Persisted component graph. Inputs and outputs are the executable boundary."""

    version: Literal["1"] = "1"
    revision: int = Field(default=1, ge=1)
    name: LocalizedText
    components: list[PipelineComponent]
    connections: list[PipelineConnection]

    def validate_connections(self) -> None:
        components = {component.id: component for component in self.components}
        if len(components) != len(self.components):
            raise ValueError("pipeline component ids must be unique")
        connection_ids = {connection.id for connection in self.connections}
        if len(connection_ids) != len(self.connections):
            raise ValueError("pipeline connection ids must be unique")
        incoming: dict[tuple[str, str], int] = {}
        adjacency: dict[str, set[str]] = {component_id: set() for component_id in components}
        for connection in self.connections:
            source = components.get(connection.source_component)
            target = components.get(connection.target_component)
            if source is None or target is None:
                raise ValueError(
                    f"pipeline connection {connection.id!r} references an unknown component"
                )
            source_port = next(
                (port for port in source.outputs if port.id == connection.source_port), None
            )
            target_port = next(
                (port for port in target.inputs if port.id == connection.target_port), None
            )
            if source_port is None or target_port is None:
                raise ValueError(
                    f"pipeline connection {connection.id!r} references an unknown port"
                )
            if canonical_contract_id(source_port.data_type) != canonical_contract_id(
                target_port.data_type
            ):
                raise ValueError(
                    f"pipeline connection {connection.id!r} links {source_port.data_type!r} "
                    f"to incompatible {target_port.data_type!r}"
                )
            target_key = (target.id, target_port.id)
            incoming[target_key] = incoming.get(target_key, 0) + 1
            if incoming[target_key] > 1 and not target_port.multiple:
                raise ValueError(
                    f"pipeline input {target.id}.{target_port.id} accepts only one connection"
                )
            adjacency[source.id].add(target.id)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(component_id: str) -> None:
            if component_id in visiting:
                raise ValueError("pipeline graph must be acyclic")
            if component_id in visited:
                return
            visiting.add(component_id)
            for target_id in adjacency[component_id]:
                visit(target_id)
            visiting.remove(component_id)
            visited.add(component_id)

        for component_id in components:
            visit(component_id)


class GraphPatch(Artifact):
    """Durable bounded edit from one exact graph revision to the next."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.GRAPH_PATCH
    schema_version: ClassVar[str] = "1"

    base_revision: int = Field(ge=1)
    resulting_revision: int = Field(ge=2)
    actor: Literal["planner", "human"]
    additions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    connections: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    updates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    disabled_components: list[str] = Field(default_factory=list, max_length=12)
    problem_branches: list[dict[str, Any]] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _revision_advances(self) -> GraphPatch:
        if self.resulting_revision <= self.base_revision:
            raise ValueError("graph patch must advance the blueprint revision")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "base_revision": self.base_revision,
            "resulting_revision": self.resulting_revision,
            "actor": self.actor,
            "operations": (
                len(self.additions)
                + len(self.connections)
                + len(self.updates)
                + len(self.disabled_components)
                + len(self.problem_branches)
            ),
        }


class PipelineNodeLayout(FrozenModel):
    """UI-only state for one component; never participates in execution semantics."""

    component_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    x: float
    y: float
    collapsed: bool = True


class PipelineLayout(FrozenModel):
    """Persisted manual canvas layout kept outside :class:`PipelineBlueprint`."""

    version: Literal["1"] = "1"
    nodes: list[PipelineNodeLayout] = Field(default_factory=list)
    collapsed_branches: list[str] = Field(default_factory=list)


class RuntimeConfigurationPlan(FrozenModel):
    """Planner-authored proposal. It has no effect until a person accepts it."""

    proposal_id: str = Field(default_factory=lambda: f"plan-{uuid4().hex[:12]}")
    status: Literal["proposed", "accepted", "rejected", "superseded"] = "proposed"
    mode: Literal["fully_auto"] = "fully_auto"
    pipeline_recommendation: Literal["create_pipeline", "defer_pipeline", "no_pipeline"] = (
        "create_pipeline"
    )
    decision_summary: LocalizedText | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    stage_directives: dict[str, list[str]] = Field(default_factory=dict)
    checkpoint_stages: list[str] = Field(default_factory=list)
    auto_proceed_stages: list[str] = Field(default_factory=list)
    max_retries_by_stage: dict[str, int] = Field(default_factory=dict)
    rationale: list[LocalizedText] = Field(default_factory=list)
    # Retained for old artifacts/API clients while ``status`` becomes authoritative.
    accepted: bool = False
    accepted_at: datetime | None = None
    accepted_by: Literal["human"] | None = None


class StagingWorkspace(Artifact):
    artifact_type: ClassVar[ArtifactType] = ArtifactType.STAGING_WORKSPACE
    schema_version: ClassVar[str] = "1"

    source_id: str
    source_fingerprint: str
    intake_artifact_ids: list[str] = Field(default_factory=list)
    schema_artifact_ids: list[str] = Field(default_factory=list)
    cache_reused: bool = False
    relationship_explanations: list[RelationshipExplanation] = Field(default_factory=list)
    reports: list[StagingReport] = Field(default_factory=list)
    pipeline_blueprint: PipelineBlueprint | None = None
    pipeline_layout: PipelineLayout = Field(default_factory=PipelineLayout)
    component_outputs: list[PipelineOutputReference] = Field(default_factory=list)
    document_extractions: list[DocumentExtractionSummary] = Field(default_factory=list)
    recommended_plan: RuntimeConfigurationPlan | None = None
    chat_history: list[StagingMessage] = Field(default_factory=list)
    planner_model: str | None = None
    planner_error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "cache_reused": self.cache_reused,
            "relationships_explained": len(self.relationship_explanations),
            "reports": len(self.reports),
            "pipeline_components": (
                len(self.pipeline_blueprint.components) if self.pipeline_blueprint else 0
            ),
            "pipeline_outputs_ready": sum(
                output.status == "ready" for output in self.component_outputs
            ),
            "document_extractions": len(self.document_extractions),
            "messages": len(self.chat_history),
            "plan_ready": self.recommended_plan is not None,
            "plan_accepted": bool(self.recommended_plan and self.recommended_plan.accepted),
            "planner_error": self.planner_error,
        }


__all__ = [
    "GraphPatch",
    "LocalizedText",
    "PipelineBlueprint",
    "PipelineComponent",
    "PipelineConnection",
    "PipelineDataType",
    "PipelineNodeControl",
    "PipelineNodeLayout",
    "PipelineLayout",
    "PipelineOutputReference",
    "PipelinePort",
    "RelationshipExplanation",
    "RuntimeConfigurationPlan",
    "StagingMessage",
    "StagingReport",
    "StagingReportArtifact",
    "StagingWorkspace",
]
