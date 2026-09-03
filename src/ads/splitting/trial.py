"""Executor-owned trial of an exact validation strategy proposal."""

from __future__ import annotations

import pandas as pd

from ads.contracts.validation import (
    SplitStrategy,
    ValidationSignals,
    ValidationStrategy,
    ValidationStrategyProposal,
    ValidationTrial,
    validation_strategy_fingerprint,
)
from ads.splitting.executor import SplitError, make_splitter, split_holdout


def _invariant_failure_reason(
    strategy: ValidationStrategy,
    *,
    n_input_rows: int,
    n_train_rows: int,
    n_holdout_rows: int,
    fold_sizes: list[tuple[int, int]],
    group_overlap: int,
    temporal_violations: int,
) -> str:
    """Name the invariants a realized split broke, and by how much.

    #440: the other branch of `execute_validation_trial` writes the full
    `SplitError` text into `failure_reason`, so surfacing that field in
    `splitting_stage` explains those failures exactly. This branch used to
    hardcode "Realized split violated a measured invariant." -- a sentence with
    no content, on the path where the split *did* execute and then failed a
    measurement. Half of the failures would still have reported nothing.

    Every number needed is already measured on the same object, so the reason
    is assembled from what the trial recorded rather than from a fresh guess.
    """
    failures: list[str] = []
    if not n_train_rows:
        failures.append(f"the training partition is empty (0 of {n_input_rows} input rows)")
    if not n_holdout_rows:
        failures.append(f"the holdout partition is empty (0 of {n_input_rows} input rows)")
    if not fold_sizes:
        failures.append("the inner splitter produced no folds")
    else:
        empty = [
            f"fold {index + 1} splits {train}/{validation}"
            for index, (train, validation) in enumerate(fold_sizes)
            if not train or not validation
        ]
        if empty:
            # Five is enough to show the shape of it; a 50-fold list would be
            # truncated mid-word by the 1000-character contract limit anyway.
            listed = ", ".join(empty[:5])
            more = "" if len(empty) <= 5 else f", and {len(empty) - 5} more"
            failures.append(
                f"{len(empty)} of {len(fold_sizes)} folds have an empty side ({listed}{more})"
            )
    if group_overlap:
        failures.append(
            f"{group_overlap} group values in {strategy.group_column!r} appear on both sides "
            "of a split boundary"
        )
    if temporal_violations:
        failures.append(
            f"{temporal_violations} of {len(fold_sizes) + 1} boundaries are not ordered in "
            f"time by {strategy.time_column!r}"
        )
    if not failures:
        # `passed` is the conjunction of exactly the checks above, so this is
        # unreachable -- but a failed trial without a reason fails contract
        # validation, which would replace a bad message with no artifact.
        return "Realized split violated a measured invariant."
    return f"Realized split violated a measured invariant: {'; '.join(failures)}."[:1000]


def execute_validation_trial(
    frame: pd.DataFrame,
    proposal: ValidationStrategyProposal,
    signals: ValidationSignals,
) -> ValidationTrial:
    """Realize outer and inner splits and measure their safety boundaries."""
    strategy = ValidationStrategy.from_proposal(proposal, signals)
    fingerprint = validation_strategy_fingerprint(proposal)
    try:
        train, holdout = split_holdout(
            frame,
            strategy,
            target_column=signals.target_column,
        )
        folds = list(
            make_splitter(
                strategy,
                train,
                target_column=signals.target_column,
            ).iter_folds(train)
        )
        boundaries = [(train.index, holdout.index), *folds]
        group_overlap = 0
        if strategy.strategy in {SplitStrategy.GROUPED, SplitStrategy.GROUPED_TEMPORAL}:
            assert strategy.group_column is not None
            for left, right in boundaries:
                group_overlap += len(
                    set(frame.loc[left, strategy.group_column].dropna())
                    & set(frame.loc[right, strategy.group_column].dropna())
                )
        temporal_violations = 0
        if strategy.strategy in {SplitStrategy.TEMPORAL, SplitStrategy.GROUPED_TEMPORAL}:
            assert strategy.time_column is not None
            dates = pd.to_datetime(
                frame[strategy.time_column], errors="coerce", format="mixed", utc=True
            )
            for left, right in boundaries:
                if dates.loc[left].max() >= dates.loc[right].min():
                    temporal_violations += 1
        fold_sizes = [(len(left), len(right)) for left, right in folds]
        passed = bool(
            len(train)
            and len(holdout)
            and fold_sizes
            and all(left and right for left, right in fold_sizes)
            and group_overlap == 0
            and temporal_violations == 0
        )
        return ValidationTrial(
            proposal_fingerprint=fingerprint,
            strategy=proposal.strategy,
            n_input_rows=len(frame),
            n_train_rows=len(train),
            n_holdout_rows=len(holdout),
            fold_sizes=fold_sizes,
            group_overlap_count=group_overlap,
            temporal_order_violation_count=temporal_violations,
            passed=passed,
            failure_reason=None
            if passed
            else _invariant_failure_reason(
                strategy,
                n_input_rows=len(frame),
                n_train_rows=len(train),
                n_holdout_rows=len(holdout),
                fold_sizes=fold_sizes,
                group_overlap=group_overlap,
                temporal_violations=temporal_violations,
            ),
        )
    except (SplitError, KeyError, ValueError, TypeError) as exc:
        return ValidationTrial(
            proposal_fingerprint=fingerprint,
            strategy=proposal.strategy,
            n_input_rows=len(frame),
            n_train_rows=0,
            n_holdout_rows=0,
            passed=False,
            failure_reason=f"{type(exc).__name__}: {exc}"[:1000],
        )


__all__ = ["execute_validation_trial"]
