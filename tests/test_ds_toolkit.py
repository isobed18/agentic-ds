"""Fold-safety and end-to-end tests for deterministic preprocessing primitives."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.exceptions import NotFittedError
from sklearn.pipeline import Pipeline

from ads.contracts.integration import IntegrationPlanProposal
from ads.ds_toolkit import (
    DatetimeFeaturizer,
    RareCategoryBucketer,
    build_categorical_pipeline,
    build_numeric_pipeline,
    build_preprocessor,
    get_feature_names,
)
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table
from ads.integration.executor import execute_plan


def _card(frame: pd.DataFrame, name: str = "table"):
    return profile_table(
        LoadedTable(
            name=name,
            frame=frame,
            source_uri=f"memory://{name}",
            source_format="memory",
        )
    )


def _sample_plan() -> IntegrationPlanProposal:
    return IntegrationPlanProposal.model_validate(
        {
            "base_table": "physicians__physician_master",
            "base_grain": ["physician_id"],
            "grain_description": "One row per physician.",
            "aggregations": [
                {
                    "source_table": "transactions",
                    "output_name": "txn_by_physician",
                    "group_by": ["physician_id"],
                    "aggregations": {
                        "total_txn_amount": "SUM(amount)",
                        "txn_count": "COUNT(*)",
                        "last_txn_date": "MAX(txn_date)",
                    },
                    "rationale": "Collapse transaction fan-out.",
                },
                {
                    "source_table": "ledger_2019_2024",
                    "output_name": "ledger_by_provider",
                    "group_by": ["provider_ref"],
                    "aggregations": {
                        "total_ledger_amount": "SUM(amount)",
                        "ledger_entry_count": "COUNT(*)",
                    },
                    "rationale": "Collapse ledger fan-out.",
                },
            ],
            "joins": [
                {
                    "left_table": "physicians__physician_master",
                    "right_table": "physicians__compensation",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "Attach the one-to-one target table.",
                },
                {
                    "left_table": "physicians__physician_master",
                    "right_table": "txn_by_physician",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "Attach fold-independent source aggregates.",
                },
                {
                    "left_table": "physicians__physician_master",
                    "right_table": "ledger_by_provider",
                    "left_columns": ["physician_id"],
                    "right_columns": ["provider_ref"],
                    "how": "left",
                    "rationale": "Attach provider-level ledger aggregates.",
                },
            ],
            "warnings": [],
        }
    )


class TestBuilders:
    def test_builders_return_unfitted_estimators(self) -> None:
        numeric = build_numeric_pipeline(scale=True)
        categorical = build_categorical_pipeline()

        assert isinstance(numeric, Pipeline)
        assert isinstance(categorical, Pipeline)
        assert not hasattr(numeric.named_steps["imputer"], "statistics_")
        assert not hasattr(categorical.named_steps["one_hot"], "categories_")

    def test_new_category_does_not_crash_fitted_pipeline(self) -> None:
        pipeline = build_categorical_pipeline(rare_threshold=0.25)
        pipeline.fit(pd.DataFrame({"city": ["ankara", "ankara", "izmir", "izmir"]}))

        transformed = pipeline.transform(pd.DataFrame({"city": ["bursa"]}))

        assert transformed.shape[0] == 1
        assert np.isfinite(transformed).all()


class TestRareCategoryBucketer:
    def test_frequencies_come_only_from_fit_subset(self) -> None:
        # In the fit subset "rare" is 20%; extra rows would make it 60% overall.
        fit_subset = pd.DataFrame({"kind": ["common"] * 8 + ["rare"] * 2})
        held_out = pd.DataFrame({"kind": ["rare"] * 10})
        bucketer = RareCategoryBucketer(threshold=0.30).fit(fit_subset)

        assert bucketer.transform(pd.DataFrame({"kind": ["common", "rare"]})).tolist() == [
            ["common"],
            ["__other__"],
        ]
        assert set(bucketer.transform(held_out).ravel()) == {"__other__"}

    def test_unseen_category_maps_to_other(self) -> None:
        bucketer = RareCategoryBucketer().fit(pd.DataFrame({"kind": ["a", "b"]}))
        assert bucketer.transform(pd.DataFrame({"kind": ["new"]}))[0, 0] == "__other__"


class TestDatetimeFeaturizer:
    def test_reference_date_comes_from_fit_not_transform_data(self) -> None:
        transformer = DatetimeFeaturizer().fit(
            pd.DataFrame({"event_at": ["2025-01-01", "2025-01-10"]})
        )

        transformed = transformer.transform(pd.DataFrame({"event_at": ["2025-02-01"]}))

        assert transformer.reference_date_ == pd.Timestamp("2025-01-10")
        assert transformed[0, -1] == -22.0

    def test_feature_names_match_calendar_outputs(self) -> None:
        transformer = DatetimeFeaturizer().fit(pd.DataFrame({"event_at": ["2025-01-01"]}))
        assert transformer.get_feature_names_out().tolist() == [
            "event_at_year",
            "event_at_month",
            "event_at_day_of_week",
            "event_at_day_of_month",
            "event_at_quarter",
            "event_at_is_weekend",
            "event_at_days_since_reference",
        ]


class TestPreprocessor:
    def test_drops_identifier_constant_target_and_explicit_exclusions(self) -> None:
        frame = pd.DataFrame(
            {
                "customer_id": range(100, 160),
                "constant": ["same"] * 60,
                "amount": np.arange(60, dtype=float),
                "segment": ["a", "b"] * 30,
                "event_at": pd.date_range("2025-01-01", periods=60),
                "post_outcome": np.arange(60, dtype=float) * 2,
                "target": np.arange(60, dtype=float) * 3,
            }
        )
        preprocessor = build_preprocessor(
            _card(frame),
            target_column="target",
            excluded_columns={"post_outcome"},
        )

        matrix = preprocessor.fit_transform(frame)
        names = get_feature_names(preprocessor)

        assert isinstance(preprocessor, ColumnTransformer)
        assert matrix.shape[0] == len(frame)
        assert len(names) == matrix.shape[1]
        assert all("customer_id" not in name for name in names)
        assert all("constant" not in name for name in names)
        assert all("post_outcome" not in name for name in names)
        assert all("target" not in name for name in names)

    def test_unknown_target_fails_instead_of_becoming_a_feature(self) -> None:
        frame = pd.DataFrame({"feature": [1.0, 2.0], "target": [3.0, 4.0]})
        with pytest.raises(ValueError, match="not present"):
            build_preprocessor(_card(frame), target_column="targte", excluded_columns=set())

    def test_feature_names_require_fitting(self) -> None:
        frame = pd.DataFrame({"feature": [1.0, 2.0], "target": [3.0, 4.0]})
        preprocessor = build_preprocessor(
            _card(frame), target_column="target", excluded_columns=set()
        )
        with pytest.raises(NotFittedError):
            get_feature_names(preprocessor)

    def test_real_sample_abt_becomes_finite_numeric_matrix(self, frames) -> None:
        abt = execute_plan(_sample_plan(), frames).frame
        preprocessor = build_preprocessor(
            _card(abt, "sample_abt"),
            target_column="annual_comp",
            excluded_columns={"total_comp_ytd"},
        )

        matrix = preprocessor.fit_transform(abt)

        assert matrix.shape[0] == 800
        assert matrix.dtype.kind in "fiu"
        assert np.isfinite(matrix).all()
        assert len(get_feature_names(preprocessor)) == matrix.shape[1]
