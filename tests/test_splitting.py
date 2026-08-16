"""Tests for the split-first, fold-owned fitting boundary."""

from __future__ import annotations

import pandas as pd
import pytest
from sklearn.model_selection import GroupKFold, KFold, StratifiedKFold, TimeSeriesSplit

from ads.contracts.integration import IntegrationPlanProposal
from ads.contracts.validation import (
    SplitStrategy,
    ValidationSignals,
    ValidationStrategy,
)
from ads.ds_toolkit import build_preprocessor
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table
from ads.integration.executor import execute_plan
from ads.splitting import SplitError, fit_in_folds, make_splitter, split_holdout


def _strategy(kind: SplitStrategy, **overrides: object) -> ValidationStrategy:
    payload: dict[str, object] = {
        "strategy": kind,
        "n_folds": 3,
        "test_size": 0.2,
        "rationale": "Exercise deterministic split execution.",
    }
    payload.update(overrides)
    return ValidationStrategy.model_validate(payload)


def _card(frame: pd.DataFrame):
    return profile_table(
        LoadedTable(
            name="leakage_probe",
            frame=frame,
            source_uri="memory://leakage_probe",
            source_format="memory",
        )
    )


class TestConcreteSplitters:
    def test_five_strategies_map_to_expected_fold_generators(self) -> None:
        frame = pd.DataFrame(
            {
                "group": [f"g{i // 4}" for i in range(48)],
                "when": pd.date_range("2025-01-01", periods=48),
                "target": [0, 1] * 24,
            }
        )
        signals = ValidationSignals(
            n_rows=len(frame),
            n_usable_rows=len(frame),
            n_folds=3,
            target_column="target",
        )

        random = make_splitter(_strategy(SplitStrategy.RANDOM), frame)
        stratified = make_splitter(
            _strategy(SplitStrategy.STRATIFIED, detected_signals=signals), frame
        )
        grouped = make_splitter(
            _strategy(SplitStrategy.GROUPED, group_column="group"), frame
        )
        temporal = make_splitter(
            _strategy(SplitStrategy.TEMPORAL, time_column="when"), frame
        )
        grouped_temporal = make_splitter(
            _strategy(
                SplitStrategy.GROUPED_TEMPORAL,
                group_column="group",
                time_column="when",
            ),
            frame,
        )

        assert isinstance(random.fold_generator, KFold)
        assert isinstance(stratified.fold_generator, StratifiedKFold)
        assert isinstance(grouped.fold_generator, GroupKFold)
        assert isinstance(temporal.fold_generator, TimeSeriesSplit)
        assert isinstance(grouped_temporal.fold_generator, TimeSeriesSplit)
        assert all(len(list(splitter.iter_folds(frame))) == 3 for splitter in (
            random,
            stratified,
            grouped,
            temporal,
            grouped_temporal,
        ))

    def test_outer_group_holdout_is_deterministic_and_isolated(self) -> None:
        frame = pd.DataFrame(
            {"group": [f"g{i // 3}" for i in range(60)], "value": range(60)}
        )
        strategy = _strategy(SplitStrategy.GROUPED, group_column="group")

        train_a, holdout_a = split_holdout(frame, strategy)
        train_b, holdout_b = split_holdout(frame, strategy)

        assert train_a.index.equals(train_b.index)
        assert holdout_a.index.equals(holdout_b.index)
        assert set(train_a["group"]).isdisjoint(holdout_a["group"])

    def test_temporal_holdout_uses_cutoff_not_n_folds(self) -> None:
        frame = pd.DataFrame(
            {"when": pd.date_range("2025-01-01", periods=20), "target": range(20)}
        )
        strategy = _strategy(
            SplitStrategy.TEMPORAL,
            n_folds=4,
            test_size=0.8,
            time_column="when",
            holdout_cutoff="2025-01-16",
        )

        train, holdout = split_holdout(frame, strategy)

        assert len(train) == 15
        assert len(holdout) == 5
        assert train["when"].max() < holdout["when"].min()
        assert len(list(make_splitter(strategy, train).iter_folds(train))) == 4


class TestFoldOwnedFitting:
    def test_holdout_categories_and_dates_never_enter_fitted_state(self) -> None:
        cutoff = pd.Timestamp("2025-03-01")
        frame = pd.DataFrame(
            {
                "segment": ["training_only"] * 59 + ["holdout_only"] * 21,
                "event_at": pd.date_range("2025-01-01", periods=80),
                "amount": [float(i) for i in range(80)],
                "target": [i % 2 for i in range(80)],
            }
        )
        card = _card(frame)
        strategy = _strategy(
            SplitStrategy.TEMPORAL,
            time_column="event_at",
            holdout_cutoff=cutoff.isoformat(),
        )

        def factory():
            return build_preprocessor(
                card,
                target_column="target",
                excluded_columns=set(),
            )

        results = fit_in_folds(factory, frame, strategy, target_column="target")

        assert set(frame.loc[results.holdout_index, "segment"]) == {"holdout_only"}
        assert frame.loc[results.holdout_index, "event_at"].min() >= cutoff
        for fitted in results.estimators:
            bucketer = fitted.named_transformers_["categorical"].named_steps[
                "rare_categories"
            ]
            datetime = fitted.named_transformers_["datetime"].named_steps["features"]
            assert "holdout_only" not in bucketer.frequent_categories_[0]
            assert datetime.reference_date_ < cutoff

        # The negative control proves these assertions detect actual leakage.
        leaked = factory().fit(frame.drop(columns=["target"]), frame["target"])
        leaked_bucketer = leaked.named_transformers_["categorical"].named_steps[
            "rare_categories"
        ]
        leaked_datetime = leaked.named_transformers_["datetime"].named_steps["features"]
        assert "holdout_only" in leaked_bucketer.frequent_categories_[0]
        assert leaked_datetime.reference_date_ >= cutoff

    def test_factory_cannot_return_a_prefitted_estimator(self) -> None:
        frame = pd.DataFrame(
            {
                "feature": range(20),
                "target": [0, 1] * 10,
            }
        )
        card = _card(frame)
        fitted = build_preprocessor(
            card,
            target_column="target",
            excluded_columns=set(),
        ).fit(frame.drop(columns=["target"]), frame["target"])

        with pytest.raises(SplitError, match="already fitted"):
            fit_in_folds(
                lambda: fitted,
                frame,
                _strategy(SplitStrategy.RANDOM),
                target_column="target",
            )


class TestSampleData:
    def test_real_transaction_abt_has_isolated_grouped_temporal_folds(
        self, frames: dict[str, pd.DataFrame]
    ) -> None:
        plan = IntegrationPlanProposal(
            base_table="transactions",
            base_grain=["txn_id"],
            grain_description="One row per transaction.",
        )
        abt = execute_plan(plan, frames).frame
        strategy = _strategy(
            SplitStrategy.GROUPED_TEMPORAL,
            group_column="physician_id",
            time_column="txn_date",
            holdout_cutoff="2024-01-01",
        )

        train, holdout = split_holdout(abt, strategy)

        assert set(train["physician_id"]).isdisjoint(holdout["physician_id"])
        assert train["txn_date"].max() < holdout["txn_date"].min()
        folds = list(make_splitter(strategy, train).iter_folds(train))
        assert len(folds) == strategy.n_folds
        for train_index, validation_index in folds:
            fold_train = train.loc[train_index]
            fold_validation = train.loc[validation_index]
            assert set(fold_train["physician_id"]).isdisjoint(
                fold_validation["physician_id"]
            )
            assert fold_train["txn_date"].max() < fold_validation["txn_date"].min()
