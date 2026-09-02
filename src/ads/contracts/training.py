"""Contracts for deterministic candidate training and evaluation."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

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


class LabelIssueMeasurement(FrozenModel):
    """Row-free Cleanlab summary computed from out-of-fold probabilities."""

    code: Literal["classification_label_issue_candidates"] = "classification_label_issue_candidates"
    provider: Literal["cleanlab"] = "cleanlab"
    evaluated_row_count: int = Field(ge=1)
    eligible_row_count: int = Field(ge=1)
    candidate_issue_count: int = Field(ge=0)
    candidate_issue_rate: float = Field(ge=0.0, le=1.0)
    mean_label_quality: float = Field(ge=0.0, le=1.0)
    p10_label_quality: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> LabelIssueMeasurement:
        if self.evaluated_row_count > self.eligible_row_count:
            raise ValueError("evaluated_row_count cannot exceed eligible_row_count.")
        if self.candidate_issue_count > self.evaluated_row_count:
            raise ValueError("candidate_issue_count cannot exceed evaluated_row_count.")
        expected_rate = self.candidate_issue_count / self.evaluated_row_count
        if abs(self.candidate_issue_rate - expected_rate) > 1e-6:
            raise ValueError("candidate_issue_rate does not match the recorded counts.")
        return self


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
    fitted_pipeline_verified: bool = Field(
        default=False,
        description=(
            "Executor-owned result of checking that the selected pipeline is fitted; "
            "agent-authored output cannot set this field in the pipeline."
        ),
    )
    fit_scope: Literal["outer_train_only"] | None = Field(
        default=None,
        description="Executor-recorded data boundary used for final model fitting.",
    )
    outer_train_row_count: int | None = Field(default=None, ge=1)
    holdout_row_count: int | None = Field(default=None, ge=1)
    holdout_rows_used_for_fit: int | None = Field(default=None, ge=0)
    inner_fold_fit_count: int | None = Field(default=None, ge=1)
    label_issue_measurement: LabelIssueMeasurement | None = None

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
            and self.training_row_count + self.target_null_rows_dropped != self.input_row_count
        ):
            raise ValueError(
                "training_row_count + target_null_rows_dropped must equal input_row_count."
            )
        fit_evidence = (
            self.fit_scope,
            self.outer_train_row_count,
            self.holdout_row_count,
            self.holdout_rows_used_for_fit,
            self.inner_fold_fit_count,
        )
        if any(value is None for value in fit_evidence) and any(
            value is not None for value in fit_evidence
        ):
            raise ValueError("Training fit-scope evidence must be complete or entirely absent.")
        if self.fitted_pipeline_verified != all(value is not None for value in fit_evidence):
            raise ValueError(
                "A fitted-pipeline verification and complete fit-scope evidence "
                "must be recorded together."
            )
        if self.fit_scope == "outer_train_only" and self.holdout_rows_used_for_fit != 0:
            raise ValueError("outer_train_only cannot report holdout rows used for fit.")
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
            "fitted_pipeline_verified": self.fitted_pipeline_verified,
            "fit_scope": self.fit_scope,
            "outer_train_row_count": self.outer_train_row_count,
            "holdout_row_count": self.holdout_row_count,
            "holdout_rows_used_for_fit": self.holdout_rows_used_for_fit,
            "inner_fold_fit_count": self.inner_fold_fit_count,
            "label_issue_candidate_rate": (
                self.label_issue_measurement.candidate_issue_rate
                if self.label_issue_measurement
                else None
            ),
            "model_artifact_id": self.model_blob.artifact_id if self.model_blob else None,
        }


class EnhancedTrainingReport(TrainingReport):
    """The same comparison, refit on externally engineered features.

    A distinct artifact type rather than a second ``TRAINED_MODEL``: evaluation
    resolves its trained model by recency, so a second artifact of that type
    would quietly make this the model the run evaluates and reports on. As its
    own type it is invisible to the primary spine and visible only where it is
    asked for.

    It is trained on the same rows, the same split, and the same candidate menu
    as its base model, so the two holdout scores are directly comparable. That is
    the whole point of the artifact, and it is only true because the feature
    search never saw the holdout.
    """

    artifact_type: ClassVar[ArtifactType] = ArtifactType.RL_ENHANCED_MODEL
    schema_version: ClassVar[str] = "1"

    #: The ``TrainingReport`` this one is the enhanced counterpart of. Recorded
    #: rather than inferred from "same run" so the pair survives a run that
    #: trained more than once.
    base_model_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    rl_feature_report_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_feature_names: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            **super().summary(),
            "base_model_artifact_id": self.base_model_artifact_id,
            "rl_feature_report_id": self.rl_feature_report_id,
            "n_generated_features": len(self.generated_feature_names),
        }


__all__ = [
    "CandidateResult",
    "EnhancedTrainingReport",
    "LabelIssueMeasurement",
    "MetricEvaluation",
    "ModelBlobReference",
    "SklearnComponentRecipe",
    "TrainingReport",
]
