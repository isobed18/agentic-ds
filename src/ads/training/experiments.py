"""Host-owned preparation and scoring for agent-authored model experiments."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.metrics import f1_score, mean_squared_error, roc_auc_score

from ads.contracts.problem import Metric, TaskType
from ads.contracts.validation import ValidationStrategy
from ads.splitting import make_splitter, split_holdout
from ads.training.frame_contracts import validate_feature_partition, validate_frame_copy

ROW_ID_COLUMN = "__ads_experiment_row_id"


@dataclass(frozen=True)
class ExperimentPartition:
    """Development rows exposed to code plus validation labels retained by host."""

    training: pd.DataFrame
    validation_features: pd.DataFrame
    validation_targets: pd.Series
    target_column: str


def prepare_experiment_partition(
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    *,
    target_column: str,
    excluded_columns: list[str] | tuple[str, ...] = (),
) -> ExperimentPartition:
    """Build one inner development split without exposing the final holdout.

    Excluded columns are removed before either copy is materialized. Validation
    labels remain process-local; only feature rows and opaque row ids enter the
    execution backend.
    """
    if ROW_ID_COLUMN in frame.columns:
        raise ValueError(f"Reserved experiment column {ROW_ID_COLUMN!r} already exists.")
    validate_frame_copy(frame)
    if target_column not in frame.columns:
        raise ValueError(f"Target column {target_column!r} is missing.")
    labeled = frame.loc[frame[target_column].notna()].copy()
    excluded = set(excluded_columns) - {target_column}
    modeled = labeled.drop(columns=sorted(excluded & set(labeled.columns)))
    outer_train, _final_holdout = split_holdout(
        modeled,
        strategy,
        target_column=target_column,
    )
    splitter = make_splitter(
        strategy,
        outer_train,
        target_column=target_column,
    )
    development_train_index, development_validation_index = next(splitter.iter_folds(outer_train))
    training = outer_train.loc[development_train_index].copy()
    validation = outer_train.loc[development_validation_index].copy()
    training.insert(0, ROW_ID_COLUMN, [f"train_{index:08d}" for index in range(len(training))])
    validation_ids = [f"validation_{index:08d}" for index in range(len(validation))]
    validation_targets = pd.Series(
        validation[target_column].to_numpy(copy=True),
        index=validation_ids,
        name=target_column,
    )
    validation_features = validation.drop(columns=[target_column])
    validation_features.insert(0, ROW_ID_COLUMN, validation_ids)
    training = training.reset_index(drop=True)
    validation_features = validation_features.reset_index(drop=True)
    validate_feature_partition(
        training=training,
        validation_features=validation_features,
        target_column=target_column,
        row_id_column=ROW_ID_COLUMN,
    )
    return ExperimentPartition(
        training=training,
        validation_features=validation_features,
        validation_targets=validation_targets,
        target_column=target_column,
    )


def _prediction_series(
    predictions: pd.DataFrame,
    partition: ExperimentPartition,
) -> pd.Series:
    if list(predictions.columns) != [ROW_ID_COLUMN, "prediction"]:
        raise ValueError(f"Predictions must contain exactly [{ROW_ID_COLUMN!r}, 'prediction'].")
    if predictions[ROW_ID_COLUMN].duplicated().any():
        raise ValueError("Prediction row ids must be unique.")
    supplied = set(predictions[ROW_ID_COLUMN].astype(str))
    expected = set(partition.validation_targets.index.astype(str))
    if supplied != expected or len(predictions) != len(expected):
        raise ValueError("Predictions must cover every validation row exactly once.")
    indexed = predictions.assign(
        **{ROW_ID_COLUMN: predictions[ROW_ID_COLUMN].astype(str)}
    ).set_index(ROW_ID_COLUMN)["prediction"]
    return indexed.loc[partition.validation_targets.index]


def measure_experiment_predictions(
    predictions: pd.DataFrame,
    partition: ExperimentPartition,
    *,
    task_type: TaskType,
    metric: Metric,
) -> tuple[float, float]:
    """Measure agent predictions and a naive baseline on the hidden labels."""
    predicted = _prediction_series(predictions, partition)
    truth = partition.validation_targets
    train_truth = partition.training[partition.target_column]

    if task_type is TaskType.REGRESSION and metric is Metric.RMSE:
        numeric = pd.to_numeric(predicted, errors="coerce")
        if numeric.isna().any() or not np.isfinite(numeric).all():
            raise ValueError("Regression predictions must be finite numbers.")
        baseline = DummyRegressor(strategy="mean").fit(np.zeros((len(train_truth), 1)), train_truth)
        baseline_predictions = baseline.predict(np.zeros((len(truth), 1)))
        return (
            float(np.sqrt(mean_squared_error(truth, numeric))),
            float(np.sqrt(mean_squared_error(truth, baseline_predictions))),
        )

    if task_type is TaskType.BINARY_CLASSIFICATION and metric is Metric.ROC_AUC:
        probabilities = pd.to_numeric(predicted, errors="coerce")
        if probabilities.isna().any() or not probabilities.between(0.0, 1.0).all():
            raise ValueError("Binary predictions must be probabilities in [0, 1].")
        baseline = DummyClassifier(strategy="prior").fit(
            np.zeros((len(train_truth), 1)), train_truth
        )
        classes = list(baseline.classes_)
        if len(classes) != 2:
            raise ValueError("Binary experiment training rows must contain two classes.")
        baseline_probabilities = baseline.predict_proba(np.zeros((len(truth), 1)))[:, 1]
        return (
            float(roc_auc_score(truth, probabilities)),
            float(roc_auc_score(truth, baseline_probabilities)),
        )

    if task_type is TaskType.MULTICLASS_CLASSIFICATION and metric is Metric.F1:
        predicted_labels = predicted.astype(str)
        truth_labels = truth.astype(str)
        baseline = DummyClassifier(strategy="most_frequent").fit(
            np.zeros((len(train_truth), 1)), train_truth.astype(str)
        )
        baseline_labels = baseline.predict(np.zeros((len(truth), 1)))
        return (
            float(f1_score(truth_labels, predicted_labels, average="macro")),
            float(f1_score(truth_labels, baseline_labels, average="macro")),
        )

    raise ValueError(f"Authored experiments do not support {task_type.value}/{metric.value}.")


__all__ = [
    "ExperimentPartition",
    "ROW_ID_COLUMN",
    "measure_experiment_predictions",
    "prepare_experiment_partition",
]
