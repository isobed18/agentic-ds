"""Deterministic EDA measurements over an analytical base table."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ads.contracts.datacard import ColumnProfile, DataCard
from ads.contracts.eda import (
    ClassBalance,
    CorrelationMatrix,
    DatetimePeriodDistribution,
    DistributionKind,
    DistributionPeak,
    DistributionValue,
    EDAReport,
    HistogramBin,
    MissingnessSummary,
    NumericDistributionShape,
    OutlierSummary,
    PeriodBucket,
    RoundingConcentration,
    TargetDistribution,
    TargetRelationship,
)
from ads.contracts.problem import SUPERVISED_TASKS, ProblemDefinition, TaskType
from ads.discovery import normalised_mutual_information
from ads.discovery.support import usable_feature_columns
from ads.intake.profiler import ProfileOptions, profile_column


class EDAError(ValueError):
    """Raised when an ABT and confirmed problem cannot produce honest EDA."""


_CLASSIFICATION_TASKS = frozenset(
    {TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION}
)
_ROUND_DIGITS = 6


def _validate_inputs(card: DataCard, frame: pd.DataFrame, problem: ProblemDefinition) -> None:
    if not frame.columns.is_unique:
        raise EDAError("EDA requires unique frame column names.")
    frame_columns = [str(column) for column in frame.columns]
    if frame_columns != card.column_names:
        missing = sorted(set(card.column_names) - set(frame_columns))
        extra = sorted(set(frame_columns) - set(card.column_names))
        raise EDAError(
            "The ABT DataCard must describe the frame in the same column order; "
            f"missing={missing}, extra={extra}."
        )
    if card.n_rows != len(frame):
        raise EDAError(
            f"The ABT DataCard records {card.n_rows} rows but the frame has {len(frame)}."
        )
    if problem.task_type in SUPERVISED_TASKS:
        if problem.target_column is None:
            raise EDAError(f"{problem.task_type.value} requires a target column for EDA.")
        if problem.target_column not in frame.columns:
            raise EDAError(
                f"Target column {problem.target_column!r} is not present in the ABT."
            )


def _full_profiles(card: DataCard, frame: pd.DataFrame) -> dict[str, ColumnProfile]:
    """Reuse intake profiling on the full EDA frame without retaining samples."""
    options = ProfileOptions(include_samples=False, max_profile_rows=max(len(frame), 1))
    return {
        name: profile_column(name, frame[name], len(frame), options)
        for name in card.column_names
    }


def _distribution_values(series: pd.Series) -> list[DistributionValue]:
    counts = series.dropna().astype(str).value_counts(sort=False)
    ordered = sorted(
        ((str(value), int(count)) for value, count in counts.items()),
        key=lambda item: (-item[1], item[0]),
    )
    total = int(sum(count for _, count in ordered))
    return [
        DistributionValue(
            value=value,
            count=count,
            rate=round(count / total, _ROUND_DIGITS),
        )
        for value, count in ordered
    ]


def _target_distribution(
    problem: ProblemDefinition,
    frame: pd.DataFrame,
    profiles: dict[str, ColumnProfile],
) -> TargetDistribution | None:
    target_column = problem.target_column
    if target_column is None:
        return None

    target = frame[target_column]
    profile = profiles[target_column]
    non_null_count = int(target.notna().sum())
    if non_null_count == 0:
        raise EDAError(f"Target column {target_column!r} has no observed values.")

    common = {
        "target_column": target_column,
        "total_count": len(target),
        "non_null_count": non_null_count,
        "null_count": profile.null_count,
        "null_rate": profile.null_rate,
        "n_unique": profile.n_unique,
    }
    if problem.task_type in _CLASSIFICATION_TASKS:
        return TargetDistribution(
            **common,
            kind=DistributionKind.CLASS_COUNTS,
            values=_distribution_values(target),
        )
    if profile.numeric is None:
        raise EDAError(
            f"Target column {target_column!r} needs numeric statistics for "
            f"task_type={problem.task_type.value!r}."
        )
    return TargetDistribution(
        **common,
        kind=DistributionKind.NUMERIC_SUMMARY,
        numeric=profile.numeric,
        histogram=_histogram(target),
    )


#: Enough buckets to show skew, a second mode or a spike at zero, few enough
#: that each bar still holds a meaningful count on a small table.
HISTOGRAM_BINS = 24
_PEAK_MIN_SHARE = 0.03
_PEAK_MIN_NEIGHBOUR_PROMINENCE = 0.005
_ROUNDING_STEPS = (0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 100.0, 1000.0)


def _histogram(series: pd.Series, bins: int = HISTOGRAM_BINS) -> list[HistogramBin]:
    """Bin a numeric column into counts.

    Returns nothing rather than a degenerate chart when the column is constant:
    a single bar spanning zero width is not a distribution, and drawing one
    would imply a shape that was never measured.
    """
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return []
    low, high = float(values.min()), float(values.max())
    if not math.isfinite(low) or not math.isfinite(high) or low == high:
        return []
    counts, edges = np.histogram(values.to_numpy(), bins=bins, range=(low, high))
    return [
        HistogramBin(lower=float(edges[i]), upper=float(edges[i + 1]), count=int(count))
        for i, count in enumerate(counts)
    ]


def _numeric_distribution_shape(
    column: str,
    series: pd.Series,
) -> NumericDistributionShape | None:
    """Measure fixed-bin local peaks and exact multiple concentrations.

    Twenty equal-width bins span the finite minimum and maximum. Counts are
    smoothed with the fixed [0.25, 0.5, 0.25] kernel. An interior bin is
    retained as a candidate peak when its raw share is at least 3% and
    its smoothed height exceeds both immediate neighbours with neighbour
    prominence of at least 0.5% of observations. This reports candidates, not
    a conclusion that the distribution is multimodal or heaped. Exact-multiple
    rates are reported for 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 100, and 1000.
    """
    values = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    values = values[np.isfinite(values)]
    if values.empty:
        return None
    histogram = _histogram(values)
    peaks: list[DistributionPeak] = []
    if len(histogram) >= 3:
        counts = np.asarray([item.count for item in histogram], dtype=float)
        smoothed = np.convolve(counts, np.asarray([0.25, 0.5, 0.25]), mode="same")
        total = len(values)
        for index in range(1, len(histogram) - 1):
            is_local_maximum = (
                smoothed[index] >= smoothed[index - 1]
                and smoothed[index] >= smoothed[index + 1]
                and (
                    smoothed[index] > smoothed[index - 1]
                    or smoothed[index] > smoothed[index + 1]
                )
            )
            prominence = max(
                (smoothed[index] - max(smoothed[index - 1], smoothed[index + 1]))
                / total,
                0.0,
            )
            share = histogram[index].count / total
            if (
                is_local_maximum
                and share >= _PEAK_MIN_SHARE
                and prominence >= _PEAK_MIN_NEIGHBOUR_PROMINENCE
            ):
                item = histogram[index]
                peaks.append(
                    DistributionPeak(
                        bin_index=index,
                        lower=item.lower,
                        upper=item.upper,
                        count=item.count,
                        share=round(share, _ROUND_DIGITS),
                        neighbour_prominence=round(prominence, _ROUND_DIGITS),
                    )
                )
    peak_separations = [
        right.bin_index - left.bin_index
        for left, right in zip(peaks, peaks[1:], strict=False)
    ]
    most_common_count = int(values.value_counts().iloc[0])
    rounding: list[RoundingConcentration] = []
    array = values.to_numpy()
    for step in _ROUNDING_STEPS:
        matching_count = int(
            np.isclose(array / step, np.round(array / step), rtol=0.0, atol=1e-9).sum()
        )
        rounding.append(
            RoundingConcentration(
                step=step,
                matching_count=matching_count,
                evaluated_count=len(values),
                rate=round(matching_count / len(values), _ROUND_DIGITS),
            )
        )
    return NumericDistributionShape(
        column=column,
        non_null_count=len(values),
        histogram=histogram,
        candidate_peaks=peaks,
        candidate_peak_count=len(peaks),
        peak_separation_bins=peak_separations,
        most_common_value_rate=round(most_common_count / len(values), _ROUND_DIGITS),
        rounding_concentrations=rounding,
    )


def _period_distribution(
    column: str,
    series: pd.Series,
    granularity: str,
) -> DatetimePeriodDistribution | None:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=True).dropna()
    if parsed.empty:
        return None
    naive = parsed.dt.tz_localize(None)
    frequency = "Y" if granularity == "year" else "M"
    labels = naive.dt.to_period(frequency).astype(str)
    counts = labels.value_counts().sort_index()
    total = int(counts.sum())
    buckets = [
        PeriodBucket(
            period=str(period),
            count=int(count),
            rate=round(int(count) / total, _ROUND_DIGITS),
        )
        for period, count in counts.items()
    ]
    dominant = max(buckets, key=lambda item: (item.count, item.period))
    return DatetimePeriodDistribution(
        column=column,
        granularity=granularity,
        parsed_count=total,
        buckets=buckets,
        dominant_period=dominant.period,
        dominant_period_rate=dominant.rate,
    )


def _class_balance(distribution: TargetDistribution | None) -> ClassBalance | None:
    if distribution is None or distribution.kind is not DistributionKind.CLASS_COUNTS:
        return None
    if len(distribution.values) < 2:
        raise EDAError("Classification EDA requires at least two observed target classes.")

    minority = min(distribution.values, key=lambda item: (item.count, item.value))
    majority = max(distribution.values, key=lambda item: (item.count, item.value))
    return ClassBalance(
        classes=distribution.values,
        minority_class=minority.value,
        minority_count=minority.count,
        minority_rate=minority.rate,
        majority_class=majority.value,
        majority_count=majority.count,
        majority_rate=majority.rate,
        majority_to_minority_ratio=round(
            majority.count / minority.count, _ROUND_DIGITS
        ),
    )


def _correlation_matrix(
    card: DataCard,
    frame: pd.DataFrame,
    profiles: dict[str, ColumnProfile],
) -> CorrelationMatrix:
    numeric_columns = [
        name for name in card.column_names if profiles[name].numeric is not None
    ]
    if not numeric_columns:
        return CorrelationMatrix(columns=[], values=[])

    measured = frame[numeric_columns].corr(method="pearson", min_periods=2)
    values: list[list[float | None]] = []
    for row_name in numeric_columns:
        row: list[float | None] = []
        for column_name in numeric_columns:
            value = measured.loc[row_name, column_name]
            row.append(
                None
                if pd.isna(value)
                else round(float(np.clip(value, -1.0, 1.0)), _ROUND_DIGITS)
            )
        values.append(row)
    return CorrelationMatrix(columns=numeric_columns, values=values)


def _pearson_from_matrix(
    matrix: CorrelationMatrix, feature: str, target: str
) -> float | None:
    if feature not in matrix.columns or target not in matrix.columns:
        return None
    feature_index = matrix.columns.index(feature)
    target_index = matrix.columns.index(target)
    return matrix.values[feature_index][target_index]


def _target_relationships(
    frame: pd.DataFrame,
    feature_columns: list[str],
    target_column: str | None,
    correlations: CorrelationMatrix,
) -> list[TargetRelationship]:
    if target_column is None:
        return []
    target = frame[target_column]
    return [
        TargetRelationship(
            column=column,
            pearson_correlation=_pearson_from_matrix(
                correlations, column, target_column
            ),
            adjusted_mutual_information=round(
                normalised_mutual_information(frame[column], target), _ROUND_DIGITS
            ),
        )
        for column in feature_columns
    ]


def _outliers(
    frame: pd.DataFrame,
    feature_columns: list[str],
    profiles: dict[str, ColumnProfile],
) -> list[OutlierSummary]:
    summaries: list[OutlierSummary] = []
    for column in feature_columns:
        numeric = profiles[column].numeric
        if numeric is None:
            continue
        iqr = numeric.p75 - numeric.p25
        lower_fence = numeric.p25 - 1.5 * iqr
        upper_fence = numeric.p75 + 1.5 * iqr
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        outlier_count = int(((values < lower_fence) | (values > upper_fence)).sum())
        evaluated_count = len(values)
        summaries.append(
            OutlierSummary(
                column=column,
                lower_fence=round(float(lower_fence), _ROUND_DIGITS),
                upper_fence=round(float(upper_fence), _ROUND_DIGITS),
                p25=round(float(numeric.p25), _ROUND_DIGITS),
                p50=round(float(numeric.p50), _ROUND_DIGITS),
                p75=round(float(numeric.p75), _ROUND_DIGITS),
                evaluated_count=evaluated_count,
                outlier_count=outlier_count,
                outlier_rate=round(
                    outlier_count / evaluated_count if evaluated_count else 0.0,
                    _ROUND_DIGITS,
                ),
            )
        )
    return summaries


def profile_for_eda(
    abt_card: DataCard,
    frame: pd.DataFrame,
    problem: ProblemDefinition,
) -> EDAReport:
    """Build a deterministic, numbers-only EDA report for a confirmed problem."""
    _validate_inputs(abt_card, frame, problem)
    profiles = _full_profiles(abt_card, frame)
    feature_columns = [
        name for name in abt_card.column_names if name != problem.target_column
    ]
    model_eligible = [
        profile.name
        for profile in usable_feature_columns(
            abt_card,
            problem.target_column,
            frozenset(problem.excluded_columns),
        )
    ]
    target_distribution = _target_distribution(problem, frame, profiles)
    correlations = _correlation_matrix(abt_card, frame, profiles)

    return EDAReport(
        problem_title=problem.title,
        task_type=problem.task_type,
        target_column=problem.target_column,
        row_count=len(frame),
        feature_columns=feature_columns,
        model_eligible_columns=model_eligible,
        covered_columns=feature_columns,
        target_distribution=target_distribution,
        missingness=[
            MissingnessSummary(
                column=name,
                null_count=profiles[name].null_count,
                null_rate=profiles[name].null_rate,
            )
            for name in abt_card.column_names
        ],
        correlation_matrix=correlations,
        target_relationships=_target_relationships(
            frame,
            feature_columns,
            problem.target_column,
            correlations,
        ),
        class_balance=_class_balance(target_distribution),
        outliers=_outliers(frame, feature_columns, profiles),
        numeric_distributions=[
            measured
            for name in abt_card.column_names
            if profiles[name].numeric is not None
            and (measured := _numeric_distribution_shape(name, frame[name])) is not None
        ],
        datetime_distributions=[
            measured
            for name in abt_card.column_names
            if profiles[name].datetime is not None
            for granularity in ("year", "month")
            if (measured := _period_distribution(name, frame[name], granularity)) is not None
        ],
    )


__all__ = ["EDAError", "profile_for_eda"]
