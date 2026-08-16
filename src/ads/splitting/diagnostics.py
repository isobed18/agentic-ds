"""Measure whether a built split is actually usable.

Added by Claude alongside Codex's executor, not as a change to it: the executor
correctly enforces the guarantees a strategy promises, but correctness and
usability are different properties, and nothing was measuring the second.

Measured on the real sample data, `grouped_temporal` on 15,000 transactions
keeps 20.6% of rows and produces 5-row validation folds, because physicians
transact continuously across the cutoff so entity isolation and strict time
ordering purge nearly every straddling entity. The split is exactly what was
asked for. The metrics computed from it would be noise.

This module turns that into numbers the Gate Evaluator can act on, so a
degenerate split escalates to a human instead of quietly producing a confident
model trained on 571 rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ads.contracts.gates import QualitySignals
from ads.contracts.validation import ValidationStrategy
from ads.splitting.executor import make_splitter, split_holdout


@dataclass(frozen=True)
class SplitDiagnostics:
    """Post-split measurements. Facts only — the gate decides what they mean."""

    n_input_rows: int
    n_train_rows: int
    n_holdout_rows: int
    fold_sizes: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    """(train_size, validation_size) per fold."""

    @property
    def n_retained_rows(self) -> int:
        return self.n_train_rows + self.n_holdout_rows

    @property
    def retained_rate(self) -> float:
        if self.n_input_rows <= 0:
            return 0.0
        return round(self.n_retained_rows / self.n_input_rows, 6)

    @property
    def n_purged_rows(self) -> int:
        return max(self.n_input_rows - self.n_retained_rows, 0)

    @property
    def min_validation_fold_size(self) -> int | None:
        if not self.fold_sizes:
            return None
        return min(validation for _, validation in self.fold_sizes)

    def to_quality_signals(self) -> QualitySignals:
        """Project into the signal shape the Gate Evaluator consumes."""
        return QualitySignals(
            n_rows=self.n_train_rows,
            split_retained_rate=self.retained_rate,
            min_validation_fold_size=self.min_validation_fold_size,
        )

    def digest(self) -> str:
        lines = [
            f"SPLIT: {self.n_train_rows:,} train / {self.n_holdout_rows:,} holdout "
            f"= {self.n_retained_rows:,} of {self.n_input_rows:,} rows "
            f"({self.retained_rate:.1%} retained)"
        ]
        if self.n_purged_rows:
            lines.append(
                f"  purged {self.n_purged_rows:,} row(s) to keep the strategy's guarantees"
            )
        if self.fold_sizes:
            sizes = ", ".join(f"{v:,}" for _, v in self.fold_sizes)
            lines.append(f"  validation fold sizes: [{sizes}]")
        return "\n".join(lines)


def describe_split(frame: pd.DataFrame, strategy: ValidationStrategy) -> SplitDiagnostics:
    """Build the split and measure what it leaves behind."""
    train, holdout = split_holdout(frame, strategy)

    fold_sizes: list[tuple[int, int]] = []
    if len(train):
        for train_index, validation_index in make_splitter(strategy, train).iter_folds(train):
            fold_sizes.append((len(train_index), len(validation_index)))

    return SplitDiagnostics(
        n_input_rows=int(len(frame)),
        n_train_rows=int(len(train)),
        n_holdout_rows=int(len(holdout)),
        fold_sizes=tuple(fold_sizes),
    )


__all__ = ["SplitDiagnostics", "describe_split"]
