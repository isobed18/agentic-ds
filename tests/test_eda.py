"""Deterministic EDA artifact, rubric coverage, and rendering tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ads.contracts import IntegrationPlanProposal, Metric, ProblemDefinition, TaskType
from ads.eda import EDAError, profile_for_eda, render_markdown
from ads.intake import LoadedTable, profile_table
from ads.integration import execute_plan

SAMPLE_PLAN = IntegrationPlanProposal.model_validate(
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
                },
                "rationale": "Prevent transaction fan-out.",
            }
        ],
        "joins": [
            {
                "left_table": "physicians__physician_master",
                "right_table": "physicians__compensation",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": "left",
                "rationale": "One compensation row per physician.",
            },
            {
                "left_table": "physicians__physician_master",
                "right_table": "txn_by_physician",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": "left",
                "rationale": "Join physician-level transaction aggregates.",
            },
        ],
        "warnings": [],
    }
)


@pytest.fixture(scope="module")
def real_abt(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return execute_plan(SAMPLE_PLAN, frames).frame


@pytest.fixture(scope="module")
def real_card(real_abt: pd.DataFrame):
    return profile_table(
        LoadedTable(
            name="physician_abt",
            frame=real_abt,
            source_uri="derived://sample",
            source_format="duckdb",
        )
    )


@pytest.fixture(scope="module")
def regression_problem() -> ProblemDefinition:
    return ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="annual_comp",
        primary_metric=Metric.RMSE,
        title="Predict annual physician compensation",
        description="Estimate compensation from information available before prediction.",
        excluded_columns=["total_comp_ytd"],
        confirmed_by="human",
    )


@pytest.fixture(scope="module")
def real_report(real_card, real_abt, regression_problem):
    return profile_for_eda(real_card, real_abt, regression_problem)


def test_every_real_abt_feature_is_covered(real_card, regression_problem, real_report) -> None:
    expected = set(real_card.column_names) - {regression_problem.target_column}
    assert expected <= set(real_report.covered_columns)
    assert real_report.all_features_covered


def test_rubric_criteria_are_mechanically_checkable(real_report) -> None:
    assert real_report.criterion_results() == {
        "eda.target_distribution_reported": True,
        "eda.all_features_covered": True,
        "eda.missingness_quantified": True,
    }
    assert all(real_report.summary()["criteria"].values())


def test_target_distribution_and_missingness_use_full_frame(
    real_abt, real_card, real_report
) -> None:
    distribution = real_report.target_distribution
    assert distribution is not None
    assert distribution.total_count == len(real_abt)
    assert distribution.non_null_count == int(real_abt["annual_comp"].notna().sum())
    assert distribution.null_count == int(real_abt["annual_comp"].isna().sum())
    assert distribution.numeric is not None

    measured = {item.column: item for item in real_report.missingness}
    assert set(measured) == set(real_card.column_names)
    assert measured["annual_comp"].null_count == int(real_abt["annual_comp"].isna().sum())


def test_relationships_correlations_and_outliers_cover_their_domains(real_report) -> None:
    assert {item.column for item in real_report.target_relationships} == set(
        real_report.feature_columns
    )
    matrix = real_report.correlation_matrix
    assert len(matrix.values) == len(matrix.columns)
    assert all(len(row) == len(matrix.columns) for row in matrix.values)
    assert "annual_comp" in matrix.columns
    assert "total_comp_ytd" in matrix.columns
    assert "total_comp_ytd" not in real_report.model_eligible_columns
    assert {item.column for item in real_report.outliers} <= set(real_report.feature_columns)


def test_profile_is_deterministic(real_card, real_abt, regression_problem, real_report) -> None:
    repeated = profile_for_eda(real_card, real_abt, regression_problem)
    assert repeated.model_dump(exclude={"created_at"}) == real_report.model_dump(
        exclude={"created_at"}
    )


def test_class_balance_is_explicit() -> None:
    frame = pd.DataFrame(
        {
            "amount": np.arange(100, dtype=float),
            "segment": ["a", "b"] * 50,
            "churned": [0] * 80 + [1] * 20,
        }
    )
    card = profile_table(
        LoadedTable(name="classification", frame=frame, source_uri="mem", source_format="csv")
    )
    problem = ProblemDefinition(
        task_type=TaskType.BINARY_CLASSIFICATION,
        target_column="churned",
        primary_metric=Metric.ROC_AUC,
        title="Predict churn",
        description="Estimate whether an account will churn.",
    )

    report = profile_for_eda(card, frame, problem)

    assert report.class_balance is not None
    assert report.class_balance.minority_class == "1"
    assert report.class_balance.minority_count == 20
    assert report.class_balance.minority_rate == pytest.approx(0.2)
    assert report.class_balance.majority_to_minority_ratio == pytest.approx(4.0)
    assert [item.count for item in report.target_distribution.values] == [80, 20]


def test_shape_descriptors_report_peaks_multiples_and_period_concentration() -> None:
    frame = pd.DataFrame(
        {
            "mixture": [-10.0] + [-5.0] * 49 + [5.0] * 49 + [10.0],
            "event_date": ["2023-06-01"] * 20 + ["2024-06-01"] * 80,
            "target": np.linspace(0.0, 1.0, 100),
        }
    )
    card = profile_table(
        LoadedTable(name="shape", frame=frame, source_uri="mem", source_format="csv")
    )
    problem = ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="target",
        primary_metric=Metric.RMSE,
        title="Measure shapes",
        description="Exercise row-free distribution descriptors.",
    )

    report = profile_for_eda(card, frame, problem)

    shape = next(item for item in report.numeric_distributions if item.column == "mixture")
    assert sum(item.count for item in shape.histogram) == 100
    assert shape.candidate_peak_count == 2
    assert len(shape.candidate_peaks) == 2
    assert shape.peak_separation_bins == [12]
    multiple_of_five = next(item for item in shape.rounding_concentrations if item.step == 5.0)
    assert multiple_of_five.rate == 1.0

    yearly = next(
        item
        for item in report.datetime_distributions
        if item.column == "event_date" and item.granularity == "year"
    )
    assert yearly.parsed_count == 100
    assert yearly.dominant_period == "2024"
    assert yearly.dominant_period_rate == pytest.approx(0.8)


def test_markdown_contains_numeric_tables_and_rubric_results(real_report) -> None:
    markdown = render_markdown(real_report)
    assert "# EDA report:" in markdown
    assert "`eda.all_features_covered`: pass" in markdown
    assert "## Missingness" in markdown
    assert "## Pearson correlation matrix" in markdown
    assert "## Feature relationships with target" in markdown
    assert "## IQR outlier counts" in markdown


def test_stale_datacard_is_rejected(real_card, real_abt, regression_problem) -> None:
    with pytest.raises(EDAError, match="DataCard records"):
        profile_for_eda(real_card, real_abt.iloc[:-1], regression_problem)
