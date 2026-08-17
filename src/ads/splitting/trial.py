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
            failure_reason=None if passed else "Realized split violated a measured invariant.",
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
