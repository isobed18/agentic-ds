"""Default deterministic workflow and its component registry."""

from __future__ import annotations

from ads.contracts.base import ArtifactType
from ads.llm import StructuredLLM
from ads.orchestration import ComponentRegistry, StageDefinition, WorkflowSpec, linear_spec
from ads.pipeline.agent_stages import (
    make_problem_discovery_stage,
    make_schema_discovery_stage,
    make_validation_strategy_stage,
)
from ads.pipeline.comprehension_stages import (
    augment_eda_stage,
    augment_feature_pipeline_stage,
    augment_leakage_stage,
    augment_schema_discovery_stage,
    augment_training_stage,
)
from ads.pipeline.stages import (
    evaluation_stage,
    feature_pipeline_stage,
    intake_stage,
    integration_stage,
    leakage_audit_stage,
    profiling_stage,
    reporting_stage,
    splitting_stage,
    training_stage,
)

_COMPONENTS = {
    "pipeline.intake": intake_stage,
    "pipeline.integration": integration_stage,
    "pipeline.profiling": profiling_stage,
    "pipeline.leakage_audit": leakage_audit_stage,
    "pipeline.splitting": splitting_stage,
    "pipeline.training": training_stage,
    "pipeline.evaluation": evaluation_stage,
    "pipeline.feature_pipeline": feature_pipeline_stage,
    "pipeline.reporting": reporting_stage,
}


def build_default_registry() -> ComponentRegistry:
    """Register every deterministic adapter named by :func:`build_default_spec`."""
    registry = ComponentRegistry()
    for name, component in _COMPONENTS.items():
        registry.register(name, component)
    return registry


def build_default_spec() -> WorkflowSpec:
    """Return the fixed-shape deterministic spine with corrective retry edges."""
    stages = [
        StageDefinition(
            id="intake",
            component="pipeline.intake",
            produces=(
                ArtifactType.DATA_CARD,
                ArtifactType.INTEGRATION_PLAN,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            description="Load/profile source tables and persist approved run inputs.",
        ),
        StageDefinition(
            id="integration",
            component="pipeline.integration",
            consumes=(ArtifactType.DATA_CARD, ArtifactType.INTEGRATION_PLAN),
            produces=(ArtifactType.DATA_CARD,),
            description="Execute the approved integration plan and verify ABT grain.",
        ),
        StageDefinition(
            id="eda",
            component="pipeline.profiling",
            consumes=(ArtifactType.DATA_CARD, ArtifactType.PROBLEM_DEFINITION),
            produces=(ArtifactType.EDA_REPORT,),
            description="Profile the analytical base table for the confirmed problem.",
        ),
        StageDefinition(
            id="leakage_audit",
            component="pipeline.leakage_audit",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.INTEGRATION_PLAN,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(ArtifactType.LEAKAGE_REPORT, ArtifactType.PROBLEM_DEFINITION),
            description="Detect leakage and apply mechanical feature-drop corrections.",
        ),
        StageDefinition(
            id="feature_pipeline",
            component="pipeline.feature_pipeline",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.LEAKAGE_REPORT,
            ),
            produces=(ArtifactType.FEATURE_SPEC,),
            description="Declare executor-owned feature routing for fold-local fitting.",
        ),
        StageDefinition(
            id="splitting",
            component="pipeline.splitting",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.FEATURE_SPEC,
            ),
            description="Build the configured split and measure its retained support.",
        ),
        StageDefinition(
            id="training",
            component="pipeline.training",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.FEATURE_SPEC,
            ),
            produces=(ArtifactType.TRAINED_MODEL,),
            description="Fit and compare the fixed deterministic model menu.",
        ),
        StageDefinition(
            id="evaluation",
            component="pipeline.evaluation",
            consumes=(
                ArtifactType.TRAINED_MODEL,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(ArtifactType.EVALUATION_REPORT,),
            description="Assemble measured holdout, baseline, leakage, and split evidence.",
        ),
        StageDefinition(
            id="report",
            component="pipeline.reporting",
            consumes=(
                ArtifactType.EVALUATION_REPORT,
                ArtifactType.TRAINED_MODEL,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(ArtifactType.EVALUATION_REPORT, ArtifactType.FINAL_REPORT),
            description="Render the complete auditable Markdown report.",
        ),
    ]
    return linear_spec(
        "deterministic-default",
        "1",
        stages,
        retry_stages=(
            "integration",
            "eda",
            "leakage_audit",
            "splitting",
            "training",
            "evaluation",
        ),
    )


def build_full_registry(llm: StructuredLLM, *, panel_size: int = 1) -> ComponentRegistry:
    """Register deterministic and agent-backed components against ``llm``."""
    registry = build_default_registry()
    registry.register(
        "pipeline.schema_discovery",
        augment_schema_discovery_stage(
            make_schema_discovery_stage(llm, panel_size=panel_size), llm
        ),
    )
    registry.register(
        "pipeline.problem_discovery",
        make_problem_discovery_stage(llm, panel_size=panel_size),
    )
    registry.register(
        "pipeline.validation_strategy",
        make_validation_strategy_stage(llm, panel_size=panel_size),
    )
    registry.components["pipeline.profiling"] = augment_eda_stage(profiling_stage, llm)
    registry.components["pipeline.leakage_audit"] = augment_leakage_stage(leakage_audit_stage, llm)
    registry.components["pipeline.feature_pipeline"] = augment_feature_pipeline_stage(
        feature_pipeline_stage, llm
    )
    registry.components["pipeline.training"] = augment_training_stage(training_stage, llm)
    return registry


def build_full_spec_definition() -> WorkflowSpec:
    """Return the complete agentic graph without binding an LLM runtime.

    The UI and documentation need to show the whole plan before a run starts.
    Constructing that declarative graph must not require Ollama to be available.
    """
    stages = [
        StageDefinition(
            id="intake",
            component="pipeline.intake",
            produces=(ArtifactType.DATA_CARD,),
            description="Load and deterministically profile every source table.",
        ),
        StageDefinition(
            id="schema_discovery",
            component="pipeline.schema_discovery",
            consumes=(ArtifactType.DATA_CARD,),
            produces=(
                ArtifactType.INTEGRATION_PLAN,
                ArtifactType.INTEGRATION_TRIAL,
                ArtifactType.MEASUREMENT_BUNDLE,
                ArtifactType.COMPREHENSION_BRIEF,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "Planner agent interprets relationships into an integration plan; a separate "
                "advisory pass proposes source comprehension from measured evidence."
            ),
        ),
        StageDefinition(
            id="integration",
            component="pipeline.integration",
            consumes=(ArtifactType.DATA_CARD, ArtifactType.INTEGRATION_PLAN),
            produces=(ArtifactType.DATA_CARD,),
            description="Execute the proposed plan and verify analytical-base-table grain.",
        ),
        StageDefinition(
            id="problem_discovery",
            component="pipeline.problem_discovery",
            consumes=(ArtifactType.DATA_CARD,),
            produces=(
                ArtifactType.PROBLEM_CANDIDATES,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "An active scout chooses measurements or read-only code, then the planner "
                "proposes problems with host-measured feasibility support."
            ),
        ),
        StageDefinition(
            id="validation_strategy",
            component="pipeline.validation_strategy",
            consumes=(ArtifactType.DATA_CARD, ArtifactType.PROBLEM_DEFINITION),
            produces=(
                ArtifactType.VALIDATION_STRATEGY,
                ArtifactType.VALIDATION_TRIAL,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "An active scout trials candidate splits; the exact final strategy is "
                "re-executed and fingerprint-bound to executor-owned diagnostics."
            ),
        ),
        StageDefinition(
            id="eda",
            component="pipeline.profiling",
            consumes=(ArtifactType.DATA_CARD, ArtifactType.PROBLEM_DEFINITION),
            produces=(
                ArtifactType.EDA_REPORT,
                ArtifactType.EXPLORATORY_ANALYSIS,
                ArtifactType.MEASUREMENT_BUNDLE,
                ArtifactType.COMPREHENSION_BRIEF,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "Profile the analytical base table, then run a separate advisory pass over "
                "the measured descriptors."
            ),
        ),
        StageDefinition(
            id="leakage_audit",
            component="pipeline.leakage_audit",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.INTEGRATION_PLAN,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "Run the mandatory leakage floor, then let an agent request a "
                "registered falsifiable challenge without clearing the gate itself."
            ),
        ),
        StageDefinition(
            id="feature_pipeline",
            component="pipeline.feature_pipeline",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.LEAKAGE_REPORT,
            ),
            produces=(
                ArtifactType.FEATURE_SPEC,
                ArtifactType.FEATURE_EXPERIMENT,
                ArtifactType.MEASUREMENT_BUNDLE,
                ArtifactType.COMPREHENSION_BRIEF,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "Persist the mandatory fold-local feature floor, then optionally run one "
                "isolated, host-scored authored feature experiment."
            ),
        ),
        StageDefinition(
            id="splitting",
            component="pipeline.splitting",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.FEATURE_SPEC,
                ArtifactType.VALIDATION_TRIAL,
            ),
            description="Build the selected split and measure retained support.",
        ),
        StageDefinition(
            id="training",
            component="pipeline.training",
            consumes=(
                ArtifactType.DATA_CARD,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.FEATURE_SPEC,
            ),
            produces=(
                ArtifactType.TRAINED_MODEL,
                ArtifactType.MODEL_EXPERIMENT,
                ArtifactType.MEASUREMENT_BUNDLE,
                ArtifactType.COMPREHENSION_BRIEF,
                ArtifactType.AGENT_AUDIT,
            ),
            description=(
                "Fit the deterministic candidate menu, then optionally run one isolated, "
                "host-scored agent-authored development experiment."
            ),
        ),
        StageDefinition(
            id="evaluation",
            component="pipeline.evaluation",
            consumes=(
                ArtifactType.TRAINED_MODEL,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(ArtifactType.EVALUATION_REPORT,),
            description="Assemble holdout, baseline, leakage, split, and gate evidence.",
        ),
        StageDefinition(
            id="report",
            component="pipeline.reporting",
            consumes=(
                ArtifactType.EVALUATION_REPORT,
                ArtifactType.TRAINED_MODEL,
                ArtifactType.LEAKAGE_REPORT,
                ArtifactType.PROBLEM_DEFINITION,
                ArtifactType.VALIDATION_STRATEGY,
            ),
            produces=(ArtifactType.EVALUATION_REPORT, ArtifactType.FINAL_REPORT),
            description="Render the complete auditable Markdown report.",
        ),
    ]
    return linear_spec(
        "agent-backed-full",
        "2",
        stages,
        retry_stages=(
            "schema_discovery",
            "integration",
            "problem_discovery",
            "validation_strategy",
            "eda",
            "leakage_audit",
            "feature_pipeline",
            "splitting",
            "training",
            "evaluation",
        ),
    )


def build_full_spec(
    llm: StructuredLLM,
    *,
    panel_size: int = 1,
) -> tuple[WorkflowSpec, ComponentRegistry]:
    """Build the complete MVP workflow and its LLM-bound component registry.

    A registry is returned with the spec because component names deliberately
    contain no executable objects; binding the injected LLM is a registry concern.
    """
    return build_full_spec_definition(), build_full_registry(llm, panel_size=panel_size)


__all__ = [
    "build_default_registry",
    "build_default_spec",
    "build_full_registry",
    "build_full_spec_definition",
    "build_full_spec",
]
