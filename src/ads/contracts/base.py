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
    CRITIQUE = "critique"
    GATE_DECISION = "gate_decision"


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
