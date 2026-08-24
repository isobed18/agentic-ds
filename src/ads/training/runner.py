"""Leakage-safe candidate training over inner CV and one outer holdout."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import is_classifier, is_regressor
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from ads.contracts.problem import Metric, TaskType
from ads.contracts.training import (
    CandidateResult,
    MetricEvaluation,
    ModelBlobReference,
    TrainingReport,
)
from ads.contracts.validation import ValidationStrategy
from ads.splitting import SplitError, fit_in_folds
from ads.store import ArtifactStore
from ads.training.candidates import RANDOM_SEED, CandidateSpec
from ads.training.data_issues import measure_classification_label_issues
from ads.training.persistence import (
    attach_training_provenance,
    hash_training_frame,
    model_artifact_id,
    save_model,
)
from ads.training.recipes import component_recipe


class TrainingError(ValueError):
    """Raised when candidates cannot be trained or honestly evaluated."""


_METRICS_BY_TASK: dict[TaskType, tuple[Metric, ...]] = {
    TaskType.REGRESSION: (Metric.RMSE, Metric.MAE, Metric.R2),
    TaskType.BINARY_CLASSIFICATION: (
        Metric.ROC_AUC,
        Metric.AVERAGE_PRECISION,
        Metric.F1,
        Metric.ACCURACY,
        Metric.BALANCED_ACCURACY,
    ),
    TaskType.MULTICLASS_CLASSIFICATION: (
        Metric.F1,
        Metric.ACCURACY,
        Metric.BALANCED_ACCURACY,
    ),
}
_LOWER_IS_BETTER = frozenset({Metric.RMSE, Metric.MAE})


def _seed_random_states(estimator: Any) -> Any:
    """Fill every unset sklearn ``random_state`` parameter deterministically."""
    get_params = getattr(estimator, "get_params", None)
    set_params = getattr(estimator, "set_params", None)
    if not callable(get_params) or not callable(set_params):
        return estimator
    params = get_params(deep=True)
    seeds = {
        name: RANDOM_SEED
        for name, value in params.items()
        if name.split("__")[-1] == "random_state" and value is None
    }
    if seeds:
        estimator.set_params(**seeds)
    return estimator


def _validate_candidates(candidates: Sequence[CandidateSpec], task_type: TaskType) -> None:
    if not candidates:
        raise TrainingError("At least the mandatory baseline candidate is required.")
    candidate_ids = [candidate.id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise TrainingError("Candidate ids must be unique.")

    prototypes: list[Any] = []
    for candidate in candidates:
        try:
            estimator = candidate.estimator_factory()
        except TypeError as exc:
            raise TrainingError(
                f"Candidate {candidate.id!r} factory must be callable with zero arguments."
            ) from exc
        if not callable(getattr(estimator, "fit", None)):
            raise TrainingError(
                f"Candidate {candidate.id!r} factory did not return a trainable estimator."
            )
        prototypes.append(estimator)

    expected_baseline = DummyRegressor if task_type is TaskType.REGRESSION else DummyClassifier
    if not isinstance(prototypes[0], expected_baseline):
        raise TrainingError(f"The first candidate must be a {expected_baseline.__name__} baseline.")
    expected_kind = is_regressor if task_type is TaskType.REGRESSION else is_classifier
    mismatched = [
        candidate.id
        for candidate, estimator in zip(candidates, prototypes, strict=True)
        if not expected_kind(estimator)
    ]
    if mismatched:
        raise TrainingError(f"Candidates do not match task_type={task_type.value!r}: {mismatched}")


def _binary_score(estimator: Any, X: pd.DataFrame) -> tuple[np.ndarray, Any]:
    classes = np.asarray(estimator.classes_)
    if len(classes) != 2:
        raise TrainingError(
            "Binary probability metrics require exactly two classes in every training fold."
        )
    if callable(getattr(estimator, "predict_proba", None)):
        scores = np.asarray(estimator.predict_proba(X))[:, 1]
    elif callable(getattr(estimator, "decision_function", None)):
        scores = np.asarray(estimator.decision_function(X))
    else:
        raise TrainingError(
            f"Estimator {type(estimator).__name__} exposes neither predict_proba nor "
            "decision_function for binary ranking metrics."
        )
    return scores, classes[1]


def _evaluate_metrics(
    estimator: Any,
    X: pd.DataFrame,
    y: pd.Series,
    task_type: TaskType,
) -> dict[Metric, float]:
    predictions = estimator.predict(X)
    if task_type is TaskType.REGRESSION:
        values = {
            Metric.RMSE: float(np.sqrt(mean_squared_error(y, predictions))),
            Metric.MAE: float(mean_absolute_error(y, predictions)),
            Metric.R2: float(r2_score(y, predictions)),
        }
    else:
        average = "binary" if task_type is TaskType.BINARY_CLASSIFICATION else "macro"
        f1_options: dict[str, Any] = {"average": average, "zero_division": 0}
        if task_type is TaskType.BINARY_CLASSIFICATION:
            classes = np.asarray(estimator.classes_)
            if len(classes) != 2:
                raise TrainingError(
                    "Binary metrics require exactly two classes in every training fold."
                )
            f1_options["pos_label"] = classes[1]
        values = {
            Metric.F1: float(f1_score(y, predictions, **f1_options)),
            Metric.ACCURACY: float(accuracy_score(y, predictions)),
            Metric.BALANCED_ACCURACY: float(balanced_accuracy_score(y, predictions)),
        }
        if task_type is TaskType.BINARY_CLASSIFICATION:
            ranking_scores, positive_class = _binary_score(estimator, X)
            binary_target = np.asarray(y == positive_class, dtype=int)
            try:
                values[Metric.ROC_AUC] = float(roc_auc_score(binary_target, ranking_scores))
                values[Metric.AVERAGE_PRECISION] = float(
                    average_precision_score(binary_target, ranking_scores)
                )
            except ValueError as exc:
                raise TrainingError(
                    "Binary ranking metrics require both classes in every evaluation portion; "
                    "use a supported stratified strategy or more data."
                ) from exc

    non_finite = [metric.value for metric, value in values.items() if not np.isfinite(value)]
    if non_finite:
        raise TrainingError(f"Non-finite metric values were produced for {non_finite}.")
    return values


def _ranking_score(metric: Metric, value: float) -> float:
    return -value if metric in _LOWER_IS_BETTER else value


def _pipeline_factory(
    preprocessor_factory: Callable[[], Any], candidate: CandidateSpec
) -> Callable[[], Pipeline]:
    """Compose fresh preprocessors and estimators while rejecting reused instances."""
    preprocessor_ids: set[int] = set()
    estimator_ids: set[int] = set()

    def factory() -> Pipeline:
        preprocessor = preprocessor_factory()
        estimator = candidate.estimator_factory()
        if id(preprocessor) in preprocessor_ids:
            raise SplitError(
                "preprocessor_factory returned the same object more than once; it must "
                "construct a fresh preprocessor for every fit."
            )
        if id(estimator) in estimator_ids:
            raise SplitError(
                f"Candidate {candidate.id!r} returned the same estimator more than once; "
                "its factory must construct a fresh estimator for every fit."
            )
        preprocessor_ids.add(id(preprocessor))
        estimator_ids.add(id(estimator))
        return Pipeline(
            [
                ("preprocessor", _seed_random_states(preprocessor)),
                ("estimator", _seed_random_states(estimator)),
            ]
        )

    return factory


def train_candidates(
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    preprocessor_factory: Callable[[], Any],
    candidates: Sequence[CandidateSpec],
    *,
    target_column: str,
    task_type: TaskType,
    primary_metric: Metric | None = None,
    store: ArtifactStore | None = None,
    run_id: str | None = None,
) -> TrainingReport:
    """Fit all candidates fold-locally, select by CV, then evaluate the holdout once.

    The first metric listed for each task is the fixed selection metric: RMSE for
    regression, ROC AUC for binary classification, and macro F1 for multiclass.
    Every other supported contract metric is reported alongside it.
    """
    task_type = TaskType(task_type)
    if task_type not in _METRICS_BY_TASK:
        raise TrainingError(f"Training is unsupported for task_type={task_type.value!r}.")
    if not callable(preprocessor_factory):
        raise TypeError("preprocessor_factory must be a zero-argument callable.")
    if target_column not in frame.columns:
        raise TrainingError(f"Target column {target_column!r} is not present in the frame.")
    if (store is None) != (run_id is None):
        raise TrainingError("store and run_id must either both be provided or both be absent.")
    if run_id is not None and not run_id.strip():
        raise TrainingError("run_id must not be empty.")
    _validate_candidates(candidates, task_type)

    input_row_count = len(frame)
    target_null_rows_dropped = int(frame[target_column].isna().sum())
    training_frame = frame.loc[frame[target_column].notna()].copy()
    if training_frame.empty:
        raise TrainingError(
            f"Target column {target_column!r} has no labeled rows after dropping "
            f"{target_null_rows_dropped} null target value(s)."
        )

    # Every metric for the task is evaluated regardless, so honouring the
    # problem's choice costs nothing. Ignoring it did cost something: the
    # evaluation stage asserts that TrainingReport.primary_metric equals
    # ProblemDefinition.primary_metric, so any run choosing anything other than
    # the first metric for its task crashed after training had already
    # succeeded. It went unnoticed because the fixture problems all happened to
    # want the first metric.
    metric_order = _METRICS_BY_TASK[task_type]
    if primary_metric is None:
        primary_metric = metric_order[0]
    elif primary_metric not in metric_order:
        raise TrainingError(
            f"Metric {primary_metric.value!r} is not defined for task "
            f"{task_type.value!r}. Available: {[m.value for m in metric_order]}."
        )
    results: list[CandidateResult] = []
    final_estimators: dict[str, Pipeline] = {}
    outer_training_frames: dict[str, pd.DataFrame] = {}
    holdout_row_counts: dict[str, int] = {}
    inner_fold_counts: dict[str, int] = {}
    candidate_fold_results: dict[str, Any] = {}

    for candidate_number, candidate in enumerate(candidates):
        pipeline_factory = _pipeline_factory(preprocessor_factory, candidate)

        fold_results = fit_in_folds(
            pipeline_factory,
            training_frame,
            strategy,
            target_column=target_column,
        )
        fold_metrics: dict[Metric, list[float]] = {metric: [] for metric in metric_order}
        for fold in fold_results:
            validation_rows = training_frame.loc[fold.validation_index]
            measured = _evaluate_metrics(
                fold.estimator,
                validation_rows.drop(columns=[target_column]),
                validation_rows[target_column],
                task_type,
            )
            for metric in metric_order:
                fold_metrics[metric].append(measured[metric])

        final_estimator = pipeline_factory()
        outer_train = training_frame.loc[fold_results.outer_train_index]
        final_estimator.fit(
            outer_train.drop(columns=[target_column]),
            outer_train[target_column],
        )
        holdout = training_frame.loc[fold_results.holdout_index]
        holdout_metrics = _evaluate_metrics(
            final_estimator,
            holdout.drop(columns=[target_column]),
            holdout[target_column],
            task_type,
        )
        final_estimators[candidate.id] = final_estimator
        outer_training_frames[candidate.id] = outer_train
        holdout_row_counts[candidate.id] = len(holdout)
        inner_fold_counts[candidate.id] = len(fold_results)
        candidate_fold_results[candidate.id] = fold_results

        evaluations = [
            MetricEvaluation(
                metric=metric,
                fold_scores=fold_metrics[metric],
                cv_mean=float(np.mean(fold_metrics[metric])),
                cv_std=float(np.std(fold_metrics[metric], ddof=0)),
                holdout_score=holdout_metrics[metric],
            )
            for metric in metric_order
        ]
        results.append(
            CandidateResult(
                candidate_id=candidate.id,
                display_name=candidate.display_name,
                estimator_class=type(final_estimator.named_steps["estimator"]).__name__,
                hyperparameters=dict(candidate.hyperparameters),
                estimator_recipe=component_recipe(final_estimator.named_steps["estimator"]),
                is_baseline=candidate_number == 0,
                metrics=evaluations,
            )
        )

    winner = max(
        results,
        key=lambda result: _ranking_score(
            primary_metric, result.evaluation_for(primary_metric).cv_mean
        ),
    )
    winning_pipeline = final_estimators[winner.candidate_id]
    check_is_fitted(winning_pipeline)
    preprocessor_recipe = component_recipe(winning_pipeline.named_steps["preprocessor"])
    label_issue_measurement = None
    if task_type in {
        TaskType.BINARY_CLASSIFICATION,
        TaskType.MULTICLASS_CLASSIFICATION,
    }:
        fold_probabilities: list[np.ndarray] = []
        fold_labels: list[np.ndarray] = []
        class_order: np.ndarray | None = None
        try:
            winning_folds = candidate_fold_results[winner.candidate_id]
            for fold in winning_folds:
                validation = training_frame.loc[fold.validation_index]
                current_classes = np.asarray(fold.estimator.classes_)
                if class_order is None:
                    class_order = current_classes
                elif not np.array_equal(class_order, current_classes):
                    raise TrainingError("OOF classifier class order changed between folds.")
                probabilities = np.asarray(
                    fold.estimator.predict_proba(validation.drop(columns=[target_column])),
                    dtype=float,
                )
                class_to_index = {
                    value: index for index, value in enumerate(current_classes.tolist())
                }
                encoded = validation[target_column].map(class_to_index)
                if encoded.isna().any():
                    raise TrainingError("OOF labels include a class absent from the fitted fold.")
                fold_probabilities.append(probabilities)
                fold_labels.append(encoded.to_numpy(dtype=int))
            label_issue_measurement = measure_classification_label_issues(
                labels=np.concatenate(fold_labels),
                pred_probs=np.concatenate(fold_probabilities),
                eligible_row_count=len(winning_folds.outer_train_index),
            )
        except (AttributeError, TrainingError, ValueError):
            # Diagnostics are advisory. A classifier without calibrated
            # probabilities must not turn an otherwise valid training run red.
            label_issue_measurement = None
    report = TrainingReport(
        task_type=task_type,
        primary_metric=primary_metric,
        results=results,
        winner_id=winner.candidate_id,
        input_row_count=input_row_count,
        target_null_rows_dropped=target_null_rows_dropped,
        training_row_count=len(training_frame),
        preprocessor_recipe=preprocessor_recipe,
        fitted_pipeline_verified=True,
        fit_scope="outer_train_only",
        outer_train_row_count=len(outer_training_frames[winner.candidate_id]),
        holdout_row_count=holdout_row_counts[winner.candidate_id],
        holdout_rows_used_for_fit=0,
        inner_fold_fit_count=inner_fold_counts[winner.candidate_id],
        label_issue_measurement=label_issue_measurement,
    )
    if store is None or run_id is None:
        return report

    training_frame = outer_training_frames[winner.candidate_id]
    attach_training_provenance(winning_pipeline, training_frame, strategy)
    winner_recipe = winner.estimator_recipe
    if winner_recipe is None:  # pragma: no cover - runner always records it above
        raise TrainingError("The winning estimator recipe was not recorded.")
    artifact_id = model_artifact_id(
        training_frame_hash=hash_training_frame(training_frame),
        strategy=strategy,
        target_column=target_column,
        task_type=task_type.value,
        winner_recipe=winner_recipe.model_dump(mode="json"),
        preprocessor_recipe=preprocessor_recipe.model_dump(mode="json"),
    )
    save_model(
        winning_pipeline,
        store,
        run_id=run_id,
        artifact_id=artifact_id,
    )
    return report.model_copy(update={"model_blob": ModelBlobReference(artifact_id=artifact_id)})


__all__ = ["TrainingError", "train_candidates"]
