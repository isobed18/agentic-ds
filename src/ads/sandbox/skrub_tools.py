"""Sanctioned fold-local skrub transforms for exploratory sandbox code."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from skrub import DatetimeEncoder, StringEncoder, TableVectorizer


@dataclass(frozen=True)
class FoldTransform:
    """Train/validation features produced by one train-only fitted vectorizer."""

    training: pd.DataFrame
    validation: pd.DataFrame
    vectorizer: TableVectorizer


def fit_transform_feature_fold(
    training: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    target_column: str,
    row_id_column: str = "__ads_experiment_row_id",
    string_components: int = 12,
) -> FoldTransform:
    """Fit skrub on training features and replay it unchanged on validation.

    Inputs are copied before use. The target must exist only in training and is
    never passed to skrub. This helper is exploratory: its output cannot satisfy
    a gate until a host-owned recipe replays and measures it independently.
    """
    if target_column not in training.columns:
        raise ValueError("skrub_training_target_missing")
    if target_column in validation.columns:
        raise ValueError("skrub_validation_target_exposed")
    if row_id_column not in training.columns or row_id_column not in validation.columns:
        raise ValueError("skrub_row_id_missing")
    source_columns = [
        column
        for column in training.columns
        if column not in {target_column, row_id_column}
    ]
    if list(validation.columns) != [row_id_column, *source_columns]:
        raise ValueError("skrub_feature_schema_mismatch")
    if not 2 <= string_components <= 64:
        raise ValueError("skrub_string_components_invalid")

    train_ids = training[row_id_column].astype(str).reset_index(drop=True)
    validation_ids = validation[row_id_column].astype(str).reset_index(drop=True)
    vectorizer = TableVectorizer(
        high_cardinality=StringEncoder(
            n_components=string_components,
            random_state=17,
        ),
        datetime=DatetimeEncoder(
            resolution="day",
            add_weekday=True,
            add_total_seconds=False,
        ),
        n_jobs=1,
    )
    transformed_train = vectorizer.fit_transform(training[source_columns].copy(deep=True))
    transformed_validation = vectorizer.transform(validation[source_columns].copy(deep=True))
    train_frame = pd.DataFrame(transformed_train).reset_index(drop=True)
    validation_frame = pd.DataFrame(transformed_validation).reset_index(drop=True)
    if list(train_frame.columns) != list(validation_frame.columns):
        raise ValueError("skrub_transformed_schema_mismatch")
    if not np.isfinite(train_frame.to_numpy(dtype=float)).all() or not np.isfinite(
        validation_frame.to_numpy(dtype=float)
    ).all():
        raise ValueError("skrub_non_finite_output")
    train_frame.insert(0, row_id_column, train_ids)
    validation_frame.insert(0, row_id_column, validation_ids)
    return FoldTransform(
        training=train_frame,
        validation=validation_frame,
        vectorizer=vectorizer,
    )


__all__ = ["FoldTransform", "fit_transform_feature_fold"]
