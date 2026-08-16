"""Deterministic feature transformers with fold-local fitted state."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.utils.validation import check_is_fitted

if TYPE_CHECKING:
    from numpy.typing import NDArray


_DATETIME_COMPONENTS = (
    "year",
    "month",
    "day_of_week",
    "day_of_month",
    "quarter",
    "is_weekend",
    "days_since_reference",
)


def _as_2d_array(values: Any) -> NDArray[Any]:
    if isinstance(values, pd.DataFrame):
        array = values.to_numpy(dtype=object)
    elif isinstance(values, pd.Series):
        array = values.to_numpy(dtype=object).reshape(-1, 1)
    else:
        array = np.asarray(values, dtype=object)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2:
        raise ValueError(f"Expected a two-dimensional table, got shape {array.shape}.")
    return array


def _input_names(estimator: Any, input_features: Sequence[str] | None) -> list[str]:
    check_is_fitted(estimator, "n_features_in_")
    if input_features is not None:
        names = [str(name) for name in input_features]
    elif hasattr(estimator, "feature_names_in_"):
        names = [str(name) for name in estimator.feature_names_in_]
    else:
        names = [f"x{i}" for i in range(estimator.n_features_in_)]
    if len(names) != estimator.n_features_in_:
        raise ValueError(
            f"Expected {estimator.n_features_in_} input feature names, got {len(names)}."
        )
    return names


def _parse_datetime(values: NDArray[Any]) -> pd.Series:
    # utc=True gives a single, comparable timeline even when source offsets differ.
    parsed = pd.to_datetime(pd.Series(values), errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_localize(None)


class DatetimeFeaturizer(TransformerMixin, BaseEstimator):
    """Expand datetimes without exposing holdout dates while choosing an anchor.

    ``reference_date_`` is the latest timestamp in the data passed to ``fit``.
    Recomputing that anchor during ``transform`` would let a temporal holdout
    change its own features, while using the wall clock would make reruns differ.
    Raw datetimes are never emitted because most estimators cannot consume them.
    """

    def fit(self, X: Any, y: Any = None) -> DatetimeFeaturizer:
        """Learn one fixed reference timestamp from training rows only."""
        del y
        array = _as_2d_array(X)
        self.n_features_in_ = array.shape[1]
        if isinstance(X, pd.DataFrame) and all(isinstance(name, str) for name in X.columns):
            self.feature_names_in_ = np.asarray(X.columns, dtype=object)

        maxima = [
            value
            for index in range(array.shape[1])
            if pd.notna(value := _parse_datetime(array[:, index]).max())
        ]
        if not maxima:
            raise ValueError(
                "DatetimeFeaturizer cannot learn a reference date because the fit data "
                "contains no parseable datetime values."
            )
        self.reference_date_ = max(maxima)
        return self

    def transform(self, X: Any) -> NDArray[np.float64]:
        """Derive calendar and elapsed features using the frozen fit-time anchor."""
        check_is_fitted(self, ("n_features_in_", "reference_date_"))
        array = _as_2d_array(X)
        if array.shape[1] != self.n_features_in_:
            raise ValueError(
                f"Expected {self.n_features_in_} datetime columns, got {array.shape[1]}."
            )

        blocks: list[NDArray[np.float64]] = []
        for index in range(array.shape[1]):
            parsed = _parse_datetime(array[:, index])
            elapsed = (self.reference_date_ - parsed).dt.total_seconds() / 86_400.0
            block = np.column_stack(
                (
                    parsed.dt.year,
                    parsed.dt.month,
                    parsed.dt.dayofweek,
                    parsed.dt.day,
                    parsed.dt.quarter,
                    parsed.dt.dayofweek.ge(5).where(parsed.notna()),
                    elapsed,
                )
            ).astype(float)
            blocks.append(block)
        return np.hstack(blocks)

    def get_feature_names_out(
        self, input_features: Sequence[str] | None = None
    ) -> NDArray[np.object_]:
        """Return stable names in the exact order produced by ``transform``."""
        names = _input_names(self, input_features)
        return np.asarray(
            [f"{name}_{component}" for name in names for component in _DATETIME_COMPONENTS],
            dtype=object,
        )


def get_feature_names(preprocessor: ColumnTransformer) -> list[str]:
    """Return names from a fitted preprocessor's actual transformed schema.

    Reconstructing names from the input card misses one-hot levels learned in a
    fold and can make leakage reports or explanations point at nonexistent
    columns, so names are delegated to the fitted scikit-learn graph.
    """
    check_is_fitted(preprocessor)
    return [str(name) for name in preprocessor.get_feature_names_out()]


__all__ = ["DatetimeFeaturizer", "get_feature_names"]
