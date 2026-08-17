"""Agent-authored feature hypothesis scored only by the host."""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.comprehension import ModelConfidence
from ads.contracts.problem import Metric, TaskType


class FeatureExperimentManifest(FrozenModel):
    """Agent-authored manifest; scores and verdict fields are intentionally absent."""

    schema_version: Literal["1"] = "1"
    title: str = Field(min_length=5, max_length=120)
    hypothesis: str = Field(min_length=10, max_length=500)
    source_columns: list[str] = Field(min_length=1, max_length=100)
    engineered_features: list[str] = Field(min_length=1, max_length=100)
    model_family: str = Field(min_length=2, max_length=120)
    verification_question: str = Field(min_length=10, max_length=500)
    confidence: ModelConfidence = Field(
        description="Uncalibrated model self-assessment; never gate evidence."
    )
    predictions_ref: str = Field(pattern=r"^artifact://[A-Za-z0-9_.\-/]+\.csv$")

    @model_validator(mode="after")
    def _unique_names(self) -> FeatureExperimentManifest:
        if len(self.source_columns) != len(set(self.source_columns)):
            raise ValueError("Source columns must be unique.")
        if len(self.engineered_features) != len(set(self.engineered_features)):
            raise ValueError("Engineered feature names must be unique.")
        return self


class FeatureExperiment(Artifact):
    """Host-scored development result; exploratory and never gate-eligible."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.FEATURE_EXPERIMENT
    schema_version: ClassVar[str] = "1"

    source_feature_spec_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_class: Literal["exploratory"] = "exploratory"
    evaluation_split: Literal["inner_development_holdout"] = (
        "inner_development_holdout"
    )
    final_holdout_used: Literal[False] = False
    task_type: TaskType
    metric: Metric
    score: float
    baseline_score: float
    evaluation_row_count: int = Field(ge=1)
    manifest: FeatureExperimentManifest
    code: str = Field(min_length=1, max_length=30_000)
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    predictions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_count: int | None = Field(default=None, ge=1)
    tool_calls: list[str] = Field(min_length=1, max_length=12)
    manifest_ref: str = Field(pattern=r"^artifact://")

    @model_validator(mode="after")
    def _finite_scores(self) -> FeatureExperiment:
        if not math.isfinite(self.score) or not math.isfinite(self.baseline_score):
            raise ValueError("Experiment scores must be finite.")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "title": self.manifest.title,
            "engineered_features": self.manifest.engineered_features,
            "metric": self.metric.value,
            "score": self.score,
            "baseline_score": self.baseline_score,
            "evidence_class": self.evidence_class,
            "final_holdout_used": self.final_holdout_used,
        }


__all__ = ["FeatureExperiment", "FeatureExperimentManifest"]
