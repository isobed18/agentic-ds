"""Deterministic outer-holdout and inner-cross-validation splitting.

There are two distinct split layers, and their controls are intentionally not
interchangeable:

* ``test_size`` controls the single outer holdout for random, stratified, and
  grouped strategies. For temporal strategies, ``holdout_cutoff`` controls that
  holdout when supplied; otherwise ``test_size`` chooses the most recent rows.
* ``n_folds`` controls cross-validation *within the outer training portion*.

Call :func:`split_holdout` exactly once before constructing or fitting anything,
then pass only its training result to :func:`make_splitter`. The fitting API in
``ads.splitting.fitting`` owns that ordering so holdout state cannot enter a
fold-fitted estimator.

Grouped-temporal splitting is purged. A validation entity never appears in its
training fold, validation rows are strictly later than training rows, and any
earlier history belonging to a validation entity is omitted from that fold.
The same rule applies at the outer cutoff. Purging is necessary because keeping
all rows for an entity that spans a boundary cannot provide both entity
isolation and strict time ordering.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import (
    GroupKFold,
    GroupShuffleSplit,
    KFold,
    StratifiedKFold,
    TimeSeriesSplit,
    train_test_split,
)

from ads.contracts.validation import SplitStrategy, ValidationStrategy

_RANDOM_SEED = 20260812


class SplitError(ValueError):
    """Raised when a requested honest split cannot be formed from the frame."""


class Splitter(ABC):
    """A concrete inner-CV fold generator returning original frame indices."""

    @abstractmethod
    def iter_folds(self, frame: pd.DataFrame) -> Iterator[tuple[pd.Index, pd.Index]]:
        """Yield ``(train_index, validation_index)`` pairs for ``frame``."""


def _validate_index(frame: pd.DataFrame) -> None:
    if not frame.index.is_unique:
        raise SplitError(
            "Split frames must have a unique index so fold indices identify rows unambiguously."
        )


def _require_column(frame: pd.DataFrame, column: str | None, role: str) -> str:
    if not column:
        raise SplitError(f"The validation strategy does not define a {role} column.")
    if column not in frame.columns:
        raise SplitError(f"The {role} column {column!r} is not present in the frame.")
    return column


def _resolve_target(
    strategy: ValidationStrategy, frame: pd.DataFrame, override: str | None = None
) -> str:
    measured = strategy.detected_signals.target_column
    if override is not None and measured is not None and override != measured:
        raise SplitError(
            f"target_column={override!r} disagrees with the strategy's measured target "
            f"{measured!r}."
        )
    target = override or measured
    return _require_column(frame, target, "target")


def _datetimes(frame: pd.DataFrame, column: str) -> pd.Series:
    parsed = pd.to_datetime(frame[column], errors="coerce", format="mixed", utc=True)
    parsed = parsed.dt.tz_localize(None)
    invalid = parsed.isna()
    if invalid.any():
        raise SplitError(
            f"Temporal splitting requires every {column!r} value to be parseable and non-null; "
            f"found {int(invalid.sum())} invalid row(s)."
        )
    return parsed


def _cutoff_timestamp(value: str) -> pd.Timestamp:
    try:
        cutoff = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise SplitError(f"holdout_cutoff={value!r} is not a valid timestamp.") from exc
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_convert("UTC").tz_localize(None)
    return cutoff


def _temporal_boundary(
    dates: pd.Series, strategy: ValidationStrategy
) -> pd.Timestamp:
    if strategy.holdout_cutoff:
        return _cutoff_timestamp(strategy.holdout_cutoff)

    ordered = dates.sort_values(kind="mergesort")
    n_holdout = max(1, int(np.ceil(len(ordered) * strategy.test_size)))
    return pd.Timestamp(ordered.iloc[-n_holdout])


def _nonempty_split(
    train: pd.DataFrame, holdout: pd.DataFrame, strategy: ValidationStrategy
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if train.empty or holdout.empty:
        raise SplitError(
            f"strategy={strategy.strategy.value!r} produced an empty "
            f"{'training' if train.empty else 'holdout'} portion. Adjust test_size/cutoff "
            "or choose a strategy supported by the data."
        )
    return train.copy(), holdout.copy()


@dataclass(frozen=True)
class _SklearnSplitter(Splitter):
    strategy: ValidationStrategy
    fold_generator: KFold | StratifiedKFold | GroupKFold
    target_column: str | None = None

    def iter_folds(self, frame: pd.DataFrame) -> Iterator[tuple[pd.Index, pd.Index]]:
        _validate_index(frame)
        kind = self.strategy.strategy
        try:
            if kind is SplitStrategy.STRATIFIED:
                target = _resolve_target(self.strategy, frame, self.target_column)
                raw_folds = self.fold_generator.split(frame, frame[target])
            elif kind is SplitStrategy.GROUPED:
                group = _require_column(
                    frame, self.strategy.group_column, "group"
                )
                if frame[group].isna().any():
                    raise SplitError(f"Grouped splitting does not permit nulls in {group!r}.")
                raw_folds = self.fold_generator.split(frame, groups=frame[group])
            else:
                raw_folds = self.fold_generator.split(frame)

            for train_positions, validation_positions in raw_folds:
                yield (
                    frame.index.take(train_positions),
                    frame.index.take(validation_positions),
                )
        except ValueError as exc:
            if isinstance(exc, SplitError):
                raise
            raise SplitError(f"Could not construct {kind.value} folds: {exc}") from exc


@dataclass(frozen=True)
class _TemporalSplitter(Splitter):
    strategy: ValidationStrategy
    fold_generator: TimeSeriesSplit

    def iter_folds(self, frame: pd.DataFrame) -> Iterator[tuple[pd.Index, pd.Index]]:
        _validate_index(frame)
        time_column = _require_column(frame, self.strategy.time_column, "time")
        dates = _datetimes(frame, time_column)
        eligible_dates = dates
        if self.strategy.holdout_cutoff:
            eligible_dates = dates[
                dates < _cutoff_timestamp(self.strategy.holdout_cutoff)
            ]

        time_blocks = pd.Index(eligible_dates.drop_duplicates().sort_values())
        try:
            raw_folds = self.fold_generator.split(time_blocks)
            for train_blocks, validation_blocks in raw_folds:
                train_times = time_blocks.take(train_blocks)
                validation_times = time_blocks.take(validation_blocks)
                train_index = frame.index[dates.isin(train_times)]
                validation_index = frame.index[dates.isin(validation_times)]
                yield train_index, validation_index
        except ValueError as exc:
            raise SplitError(f"Could not construct temporal folds: {exc}") from exc


@dataclass(frozen=True)
class _GroupedTemporalSplitter(Splitter):
    strategy: ValidationStrategy
    fold_generator: TimeSeriesSplit

    def iter_folds(self, frame: pd.DataFrame) -> Iterator[tuple[pd.Index, pd.Index]]:
        _validate_index(frame)
        group_column = _require_column(frame, self.strategy.group_column, "group")
        time_column = _require_column(frame, self.strategy.time_column, "time")
        if frame[group_column].isna().any():
            raise SplitError(
                f"Grouped-temporal splitting does not permit nulls in {group_column!r}."
            )
        dates = _datetimes(frame, time_column)
        eligible = pd.Series(True, index=frame.index)
        if self.strategy.holdout_cutoff:
            eligible &= dates < _cutoff_timestamp(self.strategy.holdout_cutoff)

        groups = frame[group_column]
        eligible_group_times = (
            pd.DataFrame({"group": groups[eligible], "date": dates[eligible]})
            .groupby("group", sort=False)["date"]
            .max()
        )
        completion_blocks = pd.Index(
            eligible_group_times.drop_duplicates().sort_values()
        )
        try:
            raw_folds = self.fold_generator.split(completion_blocks)
            for train_blocks, validation_blocks in raw_folds:
                train_times = completion_blocks.take(train_blocks)
                validation_times = completion_blocks.take(validation_blocks)
                train_groups = eligible_group_times.index[
                    eligible_group_times.isin(train_times)
                ]
                validation_groups = eligible_group_times.index[
                    eligible_group_times.isin(validation_times)
                ]
                boundary = pd.Timestamp(train_times.max())

                train_mask = eligible & groups.isin(train_groups)
                validation_mask = (
                    eligible & groups.isin(validation_groups) & dates.gt(boundary)
                )
                train_index = frame.index[train_mask]
                validation_index = frame.index[validation_mask]
                if train_index.empty or validation_index.empty:
                    raise SplitError(
                        "Purged grouped-temporal cross-validation produced an empty fold. "
                        "Use fewer folds or provide a longer temporal history."
                    )
                yield train_index, validation_index
        except ValueError as exc:
            if isinstance(exc, SplitError):
                raise
            raise SplitError(
                f"Could not construct grouped-temporal folds: {exc}"
            ) from exc


def _make_splitter(
    strategy: ValidationStrategy,
    frame: pd.DataFrame,
    *,
    target_column: str | None = None,
) -> Splitter:
    _validate_index(frame)
    kind = strategy.strategy
    if kind is SplitStrategy.RANDOM:
        return _SklearnSplitter(
            strategy,
            KFold(
                n_splits=strategy.n_folds,
                shuffle=True,
                random_state=_RANDOM_SEED,
            ),
        )
    if kind is SplitStrategy.STRATIFIED:
        _resolve_target(strategy, frame, target_column)
        return _SklearnSplitter(
            strategy,
            StratifiedKFold(
                n_splits=strategy.n_folds,
                shuffle=True,
                random_state=_RANDOM_SEED,
            ),
            target_column,
        )
    if kind is SplitStrategy.GROUPED:
        _require_column(frame, strategy.group_column, "group")
        return _SklearnSplitter(
            strategy,
            GroupKFold(n_splits=strategy.n_folds),
        )
    if kind is SplitStrategy.TEMPORAL:
        _require_column(frame, strategy.time_column, "time")
        return _TemporalSplitter(
            strategy,
            TimeSeriesSplit(n_splits=strategy.n_folds),
        )
    if kind is SplitStrategy.GROUPED_TEMPORAL:
        _require_column(frame, strategy.group_column, "group")
        _require_column(frame, strategy.time_column, "time")
        return _GroupedTemporalSplitter(
            strategy,
            TimeSeriesSplit(n_splits=strategy.n_folds),
        )
    raise SplitError(f"Unsupported split strategy: {kind!r}")


def make_splitter(
    strategy: ValidationStrategy,
    frame: pd.DataFrame,
    *,
    target_column: str | None = None,
) -> Splitter:
    """Build the concrete inner-CV splitter for an outer-training frame."""
    return _make_splitter(strategy, frame, target_column=target_column)


def _split_holdout(
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    *,
    target_column: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        raise SplitError("Cannot split an empty frame.")
    _validate_index(frame)
    positions = np.arange(len(frame))
    kind = strategy.strategy

    if kind in {SplitStrategy.RANDOM, SplitStrategy.STRATIFIED}:
        stratify = None
        if kind is SplitStrategy.STRATIFIED:
            target = _resolve_target(strategy, frame, target_column)
            stratify = frame[target]
        try:
            train_positions, holdout_positions = train_test_split(
                positions,
                test_size=strategy.test_size,
                random_state=_RANDOM_SEED,
                shuffle=True,
                stratify=stratify,
            )
        except ValueError as exc:
            raise SplitError(f"Could not construct {kind.value} holdout: {exc}") from exc
        return _nonempty_split(
            frame.iloc[train_positions], frame.iloc[holdout_positions], strategy
        )

    if kind is SplitStrategy.GROUPED:
        group_column = _require_column(frame, strategy.group_column, "group")
        if frame[group_column].isna().any():
            raise SplitError(f"Grouped splitting does not permit nulls in {group_column!r}.")
        generator = GroupShuffleSplit(
            n_splits=1,
            test_size=strategy.test_size,
            random_state=_RANDOM_SEED,
        )
        try:
            train_positions, holdout_positions = next(
                generator.split(frame, groups=frame[group_column])
            )
        except ValueError as exc:
            raise SplitError(f"Could not construct grouped holdout: {exc}") from exc
        return _nonempty_split(
            frame.iloc[train_positions], frame.iloc[holdout_positions], strategy
        )

    time_column = _require_column(frame, strategy.time_column, "time")
    dates = _datetimes(frame, time_column)
    boundary = _temporal_boundary(dates, strategy)
    holdout_mask = dates.ge(boundary)
    train_mask = dates.lt(boundary)

    if kind is SplitStrategy.GROUPED_TEMPORAL:
        group_column = _require_column(frame, strategy.group_column, "group")
        if frame[group_column].isna().any():
            raise SplitError(
                f"Grouped-temporal splitting does not permit nulls in {group_column!r}."
            )
        holdout_groups = frame.loc[holdout_mask, group_column]
        train_mask &= ~frame[group_column].isin(holdout_groups)

    return _nonempty_split(frame.loc[train_mask], frame.loc[holdout_mask], strategy)


def split_holdout(
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    *,
    target_column: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create the one-time outer train/holdout split before estimator construction."""
    return _split_holdout(frame, strategy, target_column=target_column)


__all__ = ["SplitError", "Splitter", "make_splitter", "split_holdout"]
