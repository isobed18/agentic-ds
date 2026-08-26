"""Contracts for isolated, agent-authored model experiments.

The manifest contains only what the agent may claim: what code it attempted and
where it wrote predictions. Scores live on :class:`ModelExperiment`, which the
host constructs only after matching those predictions to labels that were never
mounted in the execution environment. The experiment is exploratory and cannot
replace the deterministic training result or satisfy a gate.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.problem import Metric, TaskType


class ModelExperimentManifest(FrozenModel):
    """Agent-authored output; deliberately has no field for a score."""

    schema_version: Literal["1"] = "1"
    title: str = Field(min_length=5, max_length=120)
    hypothesis: str = Field(min_length=10, max_length=500)
    model_family: str = Field(min_length=2, max_length=120)
    declared_feature_columns: list[str] = Field(min_length=1, max_length=200)
    predictions_ref: str = Field(pattern=r"^artifact://[A-Za-z0-9_.\-/]+\.csv$")

    @model_validator(mode="after")
    def _unique_features(self) -> ModelExperimentManifest:
        if len(set(self.declared_feature_columns)) != len(self.declared_feature_columns):
            raise ValueError("Declared feature columns must be unique.")
        return self


class ModelExperiment(Artifact):
    """Host-scored development experiment; useful, exploratory, never gating."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.MODEL_EXPERIMENT
    schema_version: ClassVar[str] = "1"

    source_training_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_class: Literal["exploratory"] = "exploratory"
    evaluation_split: Literal["inner_development_holdout"] = "inner_development_holdout"
    final_holdout_used: Literal[False] = False
    task_type: TaskType
    metric: Metric
    score: float
    baseline_score: float
    evaluation_row_count: int = Field(ge=1)
    manifest: ModelExperimentManifest
    code: str = Field(min_length=1, max_length=30_000)
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    predictions_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_count: int | None = Field(default=None, ge=1)
    tool_calls: list[str] = Field(min_length=1, max_length=12)
    manifest_ref: str = Field(pattern=r"^artifact://")

    @model_validator(mode="after")
    def _finite_scores(self) -> ModelExperiment:
        if not math.isfinite(self.score) or not math.isfinite(self.baseline_score):
            raise ValueError("Experiment scores must be finite.")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "title": self.manifest.title,
            "model_family": self.manifest.model_family,
            "metric": self.metric.value,
            "score": self.score,
            "baseline_score": self.baseline_score,
            "evaluation_rows": self.evaluation_row_count,
            "evidence_class": self.evidence_class,
            "final_holdout_used": self.final_holdout_used,
            "tool_calls": len(self.tool_calls),
        }


__all__ = ["ModelExperiment", "ModelExperimentManifest"]
