"""Contracts for deterministic candidate training and evaluation."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.gates import QualitySignals
from ads.contracts.problem import Metric, TaskType

_LOWER_IS_BETTER = frozenset({Metric.RMSE, Metric.MAE, Metric.MAPE})


class MetricEvaluation(FrozenModel):
    """One metric measured on every inner fold and once on the outer holdout."""

    metric: Metric
    fold_scores: list[float] = Field(min_length=2)
    cv_mean: float
    cv_std: float = Field(ge=0.0)
    holdout_score: float


class SklearnComponentRecipe(FrozenModel):
    """JSON-safe constructor recipe for a scikit-learn component graph."""

    class_path: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ModelBlobReference(FrozenModel):
    """Portable reference to a fitted pipeline and its provenance sidecar."""

    artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = "model.joblib"
    provenance_filename: str = "provenance.json"


class CandidateResult(FrozenModel):
    """Persistable measurements for one fitted candidate model."""

    candidate_id: str
    display_name: str
    estimator_class: str
    hyperparameters: dict[str, bool | int | float | str | None] = Field(default_factory=dict)
    estimator_recipe: SklearnComponentRecipe | None = None
    is_baseline: bool = False
    metrics: list[MetricEvaluation] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_metrics(self) -> CandidateResult:
        metric_names = [evaluation.metric for evaluation in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("Candidate metric evaluations must be unique by metric.")
        return self

    def evaluation_for(self, metric: Metric) -> MetricEvaluation:
        """Return this candidate's evaluation for ``metric``."""
        for evaluation in self.metrics:
            if evaluation.metric == metric:
                return evaluation
        raise KeyError(f"Candidate {self.candidate_id!r} has no evaluation for {metric.value!r}.")


class TrainingReport(Artifact):
    """Candidate comparison selected by inner CV and evaluated on one holdout."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.TRAINED_MODEL
    schema_version: ClassVar[str] = "3"

    task_type: TaskType
    primary_metric: Metric
    results: list[CandidateResult] = Field(min_length=1)
    winner_id: str
    input_row_count: int | None = Field(default=None, ge=0)
    target_null_rows_dropped: int | None = Field(default=None, ge=0)
    training_row_count: int | None = Field(default=None, ge=1)
    preprocessor_recipe: SklearnComponentRecipe | None = None
    model_blob: ModelBlobReference | None = None

    @model_validator(mode="after")
    def _consistent_results(self) -> TrainingReport:
        row_counts = (
            self.input_row_count,
            self.target_null_rows_dropped,
            self.training_row_count,
        )
        if any(value is None for value in row_counts) and any(
            value is not None for value in row_counts
        ):
            raise ValueError("Training row counts must either all be recorded or all be absent.")
        if (
            self.training_row_count is not None
            and self.target_null_rows_dropped is not None
            and self.input_row_count is not None
            and self.training_row_count + self.target_null_rows_dropped
            != self.input_row_count
        ):
            raise ValueError(
                "training_row_count + target_null_rows_dropped must equal input_row_count."
            )
        candidate_ids = [result.candidate_id for result in self.results]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Candidate ids must be unique.")
        if self.winner_id not in candidate_ids:
            raise ValueError(f"winner_id={self.winner_id!r} is not a candidate result.")
        baselines = [result for result in self.results if result.is_baseline]
        if len(baselines) != 1 or not self.results[0].is_baseline:
            raise ValueError("Exactly one baseline is required and it must be first.")
        for result in self.results:
            result.evaluation_for(self.primary_metric)
        return self

    @property
    def winner(self) -> CandidateResult:
        """The CV-selected candidate."""
        return next(result for result in self.results if result.candidate_id == self.winner_id)

    @property
    def baseline(self) -> CandidateResult:
        """The mandatory naive baseline, which is always the first result."""
        return self.results[0]

    @staticmethod
    def _quality_score(metric: Metric, score: float) -> float:
        # Gate baseline deltas are uniformly higher-is-better. Preserve familiar,
        # positive loss values in the report and orient only the gate projection.
        return -score if metric in _LOWER_IS_BETTER else score

    def to_quality_signals(self) -> QualitySignals:
        """Project measured model quality into the Gate Evaluator's input shape."""
        winner = self.winner.evaluation_for(self.primary_metric)
        baseline = self.baseline.evaluation_for(self.primary_metric)
        return QualitySignals(
            best_score=self._quality_score(self.primary_metric, winner.holdout_score),
            naive_baseline_score=self._quality_score(self.primary_metric, baseline.holdout_score),
            cv_mean=self._quality_score(self.primary_metric, winner.cv_mean),
            cv_std=winner.cv_std,
        )

    def summary(self) -> dict[str, Any]:
        winner = self.winner.evaluation_for(self.primary_metric)
        return {
            "task_type": self.task_type.value,
            "primary_metric": self.primary_metric.value,
            "n_candidates": len(self.results),
            "winner_id": self.winner_id,
            "winner_holdout_score": winner.holdout_score,
            "input_row_count": self.input_row_count,
            "target_null_rows_dropped": self.target_null_rows_dropped,
            "training_row_count": self.training_row_count,
            "model_artifact_id": self.model_blob.artifact_id if self.model_blob else None,
        }


__all__ = [
    "CandidateResult",
    "MetricEvaluation",
    "ModelBlobReference",
    "SklearnComponentRecipe",
    "TrainingReport",
]
