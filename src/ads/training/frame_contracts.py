"""Pandera-backed contracts at dataframe copy and feature boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors


class FrameContractError(ValueError):
    """Structured failure raised before a malformed frame crosses a boundary."""

    def __init__(self, code: str, **parameters: Any) -> None:
        self.code = code
        self.parameters = parameters
        super().__init__(f"{code}: {parameters}")


def validate_frame_copy(
    frame: pd.DataFrame,
    *,
    expected_columns: Sequence[str] | None = None,
    expected_rows: int | None = None,
) -> pd.DataFrame:
    """Validate a frame's row-free structural contract without coercing it."""
    if not isinstance(frame, pd.DataFrame):
        raise FrameContractError("frame_type_invalid", actual_type=type(frame).__name__)
    columns = list(frame.columns if expected_columns is None else expected_columns)
    if not all(isinstance(column, str) for column in columns):
        raise FrameContractError("frame_column_name_invalid")
    schema = pa.DataFrameSchema(
        {column: pa.Column(nullable=True) for column in columns},
        strict=True,
        ordered=True,
        unique_column_names=True,
    )
    try:
        validated = schema.validate(frame, lazy=True)
    except SchemaErrors as exc:
        raise FrameContractError(
            "frame_schema_invalid",
            expected_columns=columns,
            failure_count=len(exc.failure_cases),
        ) from exc
    if expected_rows is not None and len(validated) != expected_rows:
        raise FrameContractError(
            "frame_row_count_changed",
            expected_rows=expected_rows,
            actual_rows=len(validated),
        )
    return validated


def validate_feature_partition(
    *,
    training: pd.DataFrame,
    validation_features: pd.DataFrame,
    target_column: str,
    row_id_column: str,
) -> None:
    """Enforce the sandbox feature experiment's pre/postconditions."""
    validate_frame_copy(training)
    validate_frame_copy(validation_features)
    if target_column not in training.columns:
        raise FrameContractError("feature_training_target_missing", target_column=target_column)
    if target_column in validation_features.columns:
        raise FrameContractError("feature_validation_target_exposed", target_column=target_column)
    expected_validation = [column for column in training.columns if column != target_column]
    validate_frame_copy(validation_features, expected_columns=expected_validation)
    for frame_name, frame in (
        ("training", training),
        ("validation", validation_features),
    ):
        if row_id_column not in frame.columns:
            raise FrameContractError("feature_row_id_missing", frame=frame_name)
        row_ids = frame[row_id_column]
        if row_ids.isna().any() or row_ids.astype(str).duplicated().any():
            raise FrameContractError("feature_row_id_invalid", frame=frame_name)
    if set(training[row_id_column].astype(str)) & set(
        validation_features[row_id_column].astype(str)
    ):
        raise FrameContractError("feature_partition_overlap")


__all__ = ["FrameContractError", "validate_feature_partition", "validate_frame_copy"]
