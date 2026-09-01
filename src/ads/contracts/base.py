"""Base types for all inter-stage contracts.

Every artifact that crosses a stage boundary is a subclass of ``Artifact``. Two
invariants from the architecture report are enforced here rather than by
convention:

1. Artifacts are **versioned**. ``schema_version`` is declared per class so old
   runs stay readable after a contract changes.
2. Artifacts are **immutable**. Models are frozen; a stage produces a new
   artifact rather than mutating an existing one, which is what makes fork,
   time-travel and audit cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ArtifactType(StrEnum):
    """Closed vocabulary of artifact kinds.

    Used by :class:`ads.contracts.agent.ContextPolicy` to declare which slices of
    run state an agent is allowed to see, so this must stay a closed set.
    """

    DATA_CARD = "data_card"
    INTEGRATION_PLAN = "integration_plan"
    INTEGRATION_TRIAL = "integration_trial"
    PROBLEM_CANDIDATES = "problem_candidates"
    PROBLEM_DEFINITION = "problem_definition"
    VALIDATION_STRATEGY = "validation_strategy"
    VALIDATION_TRIAL = "validation_trial"
    EDA_REPORT = "eda_report"
    EXPLORATORY_ANALYSIS = "exploratory_analysis"
    FEATURE_SPEC = "feature_spec"
    FEATURE_EXPERIMENT = "feature_experiment"
    LEAKAGE_REPORT = "leakage_report"
    CANDIDATE_SET = "candidate_set"
    TRAINED_MODEL = "trained_model"
    MODEL_EXPERIMENT = "model_experiment"
    EVALUATION_REPORT = "evaluation_report"
    FINAL_REPORT = "final_report"
    AGENT_AUDIT = "agent_audit"
    MEASUREMENT_BUNDLE = "measurement_bundle"
    COMPREHENSION_BRIEF = "comprehension_brief"
    STAGING_WORKSPACE = "staging_workspace"
    STAGING_REPORT = "staging_report"
    DOCUMENT_EXTRACTION = "document_extraction"
    DOCUMENT_TABLE_REVIEW = "document_table_review"
    AUTOMATION_EXECUTION_PLAN = "automation_execution_plan"
    GRAPH_PATCH = "graph_patch"
    TABLE_ASSET = "table_asset"
    SPLIT_MANIFEST = "split_manifest"
    NODE_ATTEMPT = "node_attempt"
    CRITIQUE = "critique"
    GATE_DECISION = "gate_decision"


# Artifact kinds that exist for provenance and engineering, not for a person
# deciding what to do next. They are still persisted and still reachable behind
# an explicit diagnostics affordance (#305) -- the point is only to keep the
# default artifact view to source/profile summaries, approved tables, EDA
# visuals, models, reports, and decisions, instead of burying them under an
# agent audit and a measurement bundle emitted on every single stage attempt.
DIAGNOSTIC_ARTIFACT_TYPES: frozenset[ArtifactType] = frozenset(
    {
        ArtifactType.AGENT_AUDIT,
        ArtifactType.MEASUREMENT_BUNDLE,
        ArtifactType.INTEGRATION_TRIAL,
        ArtifactType.VALIDATION_TRIAL,
        ArtifactType.FEATURE_EXPERIMENT,
        ArtifactType.MODEL_EXPERIMENT,
        ArtifactType.NODE_ATTEMPT,
        ArtifactType.CRITIQUE,
        ArtifactType.GRAPH_PATCH,
    }
)


def is_diagnostic_artifact(artifact_type: ArtifactType | str) -> bool:
    """Whether an artifact kind is a diagnostic hidden from the default view.

    Accepts the enum or its string value so callers reading a persisted index
    (where the type is a bare string) do not each have to reconstruct the enum.
    """
    try:
        return ArtifactType(artifact_type) in DIAGNOSTIC_ARTIFACT_TYPES
    except ValueError:
        return False


class FrozenModel(BaseModel):
    """Immutable, strictly-validated base for every contract type."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class Artifact(FrozenModel):
    """Base class for anything persisted to the artifact store.

    Subclasses declare ``artifact_type`` and ``schema_version`` as ClassVars. The
    store reads them to build the on-disk index without needing to instantiate
    the model.
    """

    artifact_type: ClassVar[ArtifactType]
    schema_version: ClassVar[str] = "1"

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def type_key(cls) -> str:
        """Stable ``type@version`` identifier used in the store index."""
        return f"{cls.artifact_type.value}@{cls.schema_version}"

    def summary(self) -> dict[str, Any]:
        """Small, queryable projection stored alongside the artifact.

        Kept deliberately tiny: this is what the Gate Evaluator and the run
        timeline read without deserializing the full payload. Subclasses should
        override to surface the few fields worth indexing.
        """
        return {}
