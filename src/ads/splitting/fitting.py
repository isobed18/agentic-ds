"""Fold-owned estimator construction and fitting."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas import Index
from sklearn.exceptions import NotFittedError
from sklearn.utils.validation import check_is_fitted

from ads.contracts.validation import ValidationStrategy
from ads.splitting.executor import SplitError, _make_splitter, _split_holdout


@dataclass(frozen=True)
class FoldResult:
    """One fitted estimator and the exact rows it was allowed to observe."""

    fold: int
    train_index: Index
    validation_index: Index
    estimator: Any


@dataclass(frozen=True)
class FoldResults:
    """Audit trail for the untouched outer holdout and every inner fit."""

    outer_train_index: Index
    holdout_index: Index
    folds: tuple[FoldResult, ...]

    @property
    def estimators(self) -> tuple[Any, ...]:
        """Return fitted estimators in fold order."""
        return tuple(fold.estimator for fold in self.folds)

    def __iter__(self) -> Iterator[FoldResult]:
        return iter(self.folds)

    def __len__(self) -> int:
        return len(self.folds)


def _require_unfitted(estimator: Any) -> None:
    if not callable(getattr(estimator, "fit", None)):
        raise TypeError("estimator_factory must return an object with a fit method.")
    try:
        check_is_fitted(estimator)
    except NotFittedError:
        return
    except TypeError:
        # Non-scikit estimators do not participate in check_is_fitted. Reject
        # conventional fitted-state attributes rather than pretending they are safe.
        fitted_attributes = [
            name
            for name in vars(estimator)
            if name.endswith("_") and not name.startswith("__")
        ]
        if fitted_attributes:
            raise SplitError(
                "estimator_factory returned an object with fitted-state attributes: "
                f"{sorted(fitted_attributes)}"
            ) from None
        return
    raise SplitError(
        "estimator_factory returned an already fitted estimator. Return a fresh, "
        "unfitted object on every call."
    )


def fit_in_folds(
    estimator_factory: Callable[[], Any],
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    *,
    target_column: str,
) -> FoldResults:
    """Split once, then construct and fit one fresh estimator per training fold.

    The factory is intentionally zero-argument. Estimator construction occurs
    only after the outer holdout is removed, and fitted instances are never
    accepted as input. Each estimator receives feature columns from its fold's
    training rows and the corresponding target; validation and holdout rows are
    not passed to ``fit``.
    """
    if not callable(estimator_factory):
        raise TypeError("estimator_factory must be a zero-argument callable.")
    if target_column not in frame.columns:
        raise SplitError(f"Target column {target_column!r} is not present in the frame.")

    outer_train, holdout = _split_holdout(
        frame,
        strategy,
        target_column=target_column,
    )
    splitter = _make_splitter(
        strategy,
        outer_train,
        target_column=target_column,
    )

    folds: list[FoldResult] = []
    estimator_ids: set[int] = set()
    for fold_number, (train_index, validation_index) in enumerate(
        splitter.iter_folds(outer_train)
    ):
        estimator = estimator_factory()
        if id(estimator) in estimator_ids:
            raise SplitError(
                "estimator_factory returned the same object for multiple folds; "
                "it must construct a fresh estimator on every call."
            )
        estimator_ids.add(id(estimator))
        _require_unfitted(estimator)

        training_rows = outer_train.loc[train_index]
        X_train = training_rows.drop(columns=[target_column])
        y_train = training_rows[target_column]
        estimator.fit(X_train, y_train)
        folds.append(
            FoldResult(
                fold=fold_number,
                train_index=train_index.copy(),
                validation_index=validation_index.copy(),
                estimator=estimator,
            )
        )

    return FoldResults(
        outer_train_index=outer_train.index.copy(),
        holdout_index=holdout.index.copy(),
        folds=tuple(folds),
    )


__all__ = ["FoldResult", "FoldResults", "fit_in_folds"]
