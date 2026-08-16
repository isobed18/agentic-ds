"""Fold-safe builders for deterministic tabular preprocessing."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.utils.validation import check_is_fitted

from ads.contracts.datacard import DataCard, SemanticType
from ads.ds_toolkit.features import DatetimeFeaturizer

if TYPE_CHECKING:
    from numpy.typing import NDArray


_OTHER = "__other__"
_MISSING = "__missing__"


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


def _category_token(value: Any) -> str:
    try:
        if pd.isna(value):
            return _MISSING
    except (TypeError, ValueError):
        pass
    return str(value)


class RareCategoryBucketer(TransformerMixin, BaseEstimator):
    """Learn fold-local frequent categories and replace every other value.

    Frequencies are learned only in ``fit`` so a category common in a holdout
    cannot influence the training schema. Unseen values take the same
    ``__other__`` path as rare training values instead of raising at inference.
    """

    def __init__(self, threshold: float = 0.05) -> None:
        self.threshold = threshold

    def fit(self, X: Any, y: Any = None) -> RareCategoryBucketer:
        """Learn categories whose training-fold frequency meets the threshold."""
        del y
        if not isinstance(self.threshold, (int, float)) or isinstance(self.threshold, bool):
            raise TypeError("threshold must be a number between 0 and 1.")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1 inclusive.")

        array = _as_2d_array(X)
        if array.shape[0] == 0:
            raise ValueError("RareCategoryBucketer cannot fit an empty table.")
        self.n_features_in_ = array.shape[1]
        if isinstance(X, pd.DataFrame) and all(isinstance(name, str) for name in X.columns):
            self.feature_names_in_ = np.asarray(X.columns, dtype=object)

        frequent: list[frozenset[str]] = []
        for index in range(array.shape[1]):
            tokens = [_category_token(value) for value in array[:, index]]
            counts = Counter(tokens)
            frequent.append(
                frozenset(
                    category
                    for category, count in counts.items()
                    if count / len(tokens) >= self.threshold
                )
            )
        self.frequent_categories_ = tuple(frequent)
        return self

    def transform(self, X: Any) -> NDArray[np.object_]:
        """Map rare and unseen values to the stable ``__other__`` sentinel."""
        check_is_fitted(self, ("n_features_in_", "frequent_categories_"))
        array = _as_2d_array(X)
        if array.shape[1] != self.n_features_in_:
            raise ValueError(
                f"Expected {self.n_features_in_} categorical columns, got {array.shape[1]}."
            )

        transformed = np.empty(array.shape, dtype=object)
        for index, frequent in enumerate(self.frequent_categories_):
            transformed[:, index] = [
                token if token in frequent else _OTHER
                for token in (_category_token(value) for value in array[:, index])
            ]
        return transformed

    def get_feature_names_out(
        self, input_features: Sequence[str] | None = None
    ) -> NDArray[np.object_]:
        """Preserve input names because bucketing does not change column count."""
        check_is_fitted(self, "n_features_in_")
        if input_features is not None:
            names = [str(name) for name in input_features]
        elif hasattr(self, "feature_names_in_"):
            names = [str(name) for name in self.feature_names_in_]
        else:
            names = [f"x{i}" for i in range(self.n_features_in_)]
        if len(names) != self.n_features_in_:
            raise ValueError(
                f"Expected {self.n_features_in_} input feature names, got {len(names)}."
            )
        return np.asarray(names, dtype=object)


def build_numeric_pipeline(strategy: str = "median", *, scale: bool = False) -> Pipeline:
    """Build an unfitted numeric imputation pipeline with optional scaling.

    Returning the estimator graph, rather than precomputed values, ensures the
    imputation median and scaling moments are learned inside each training fold.
    """
    steps: list[tuple[str, Any]] = [
        ("imputer", SimpleImputer(strategy=strategy, keep_empty_features=True))
    ]
    if scale:
        steps.append(("scaler", StandardScaler()))
    return Pipeline(steps)


def build_categorical_pipeline(
    *, imputation_strategy: str = "most_frequent", rare_threshold: float = 0.05
) -> Pipeline:
    """Build an unfitted impute, rare-bucket, and one-hot pipeline.

    All learned categories stay inside the fitted pipeline. Precomputing either
    frequencies or one-hot levels over the full table would leak holdout
    prevalence into training; ``handle_unknown`` also keeps inference robust.
    """
    imputer_options: dict[str, Any] = {
        "strategy": imputation_strategy,
        "keep_empty_features": True,
    }
    if imputation_strategy == "constant":
        imputer_options["fill_value"] = _MISSING
    return Pipeline(
        [
            ("imputer", SimpleImputer(**imputer_options)),
            ("rare_categories", RareCategoryBucketer(threshold=rare_threshold)),
            (
                "one_hot",
                OneHotEncoder(
                    handle_unknown="ignore",
                    sparse_output=False,
                    dtype=np.float64,
                ),
            ),
        ]
    )


def build_preprocessor(
    card: DataCard,
    *,
    target_column: str,
    excluded_columns: Collection[str],
) -> ColumnTransformer:
    """Build an unfitted semantic-type router from a :class:`DataCard`.

    The target and explicit exclusions are rejected when misspelled: silently
    accepting a typo could route a target, identifier, or post-outcome field
    into the feature matrix. Identifier, constant, empty, text, and unknown
    columns are left to ``remainder='drop'``.
    """
    if target_column not in card.column_names:
        raise ValueError(f"Target column {target_column!r} is not present in the DataCard.")
    if isinstance(excluded_columns, str):
        raise TypeError("excluded_columns must be a collection of column names, not a string.")
    excluded = set(excluded_columns)
    unknown_exclusions = excluded.difference(card.column_names)
    if unknown_exclusions:
        raise ValueError(
            f"Excluded columns are not present in the DataCard: {sorted(unknown_exclusions)}"
        )
    omitted = excluded | {target_column}

    numeric_types = {SemanticType.NUMERIC_CONTINUOUS, SemanticType.NUMERIC_DISCRETE}
    categorical_types = {SemanticType.CATEGORICAL, SemanticType.BOOLEAN}
    numeric_columns = [
        column.name
        for column in card.columns
        if column.name not in omitted and column.semantic_type in numeric_types
    ]
    categorical_columns = [
        column.name
        for column in card.columns
        if column.name not in omitted and column.semantic_type in categorical_types
    ]
    datetime_columns = [
        column.name
        for column in card.columns
        if column.name not in omitted and column.semantic_type is SemanticType.DATETIME
    ]

    datetime_pipeline = Pipeline(
        [
            ("features", DatetimeFeaturizer()),
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", build_numeric_pipeline(), numeric_columns),
            ("categorical", build_categorical_pipeline(), categorical_columns),
            ("datetime", datetime_pipeline, datetime_columns),
        ],
        remainder="drop",
        sparse_threshold=0.0,
        verbose_feature_names_out=True,
    )


__all__ = [
    "RareCategoryBucketer",
    "build_categorical_pipeline",
    "build_numeric_pipeline",
    "build_preprocessor",
]
