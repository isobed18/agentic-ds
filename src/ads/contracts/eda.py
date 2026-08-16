"""Typed contracts for deterministic exploratory data analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.datacard import NumericStats
from ads.contracts.problem import TaskType


class DistributionKind(StrEnum):
    """Shape used to represent the target distribution."""

    CLASS_COUNTS = "class_counts"
    NUMERIC_SUMMARY = "numeric_summary"


class DistributionValue(FrozenModel):
    """One observed target value and its frequency."""

    value: str
    count: int = Field(ge=1)
    rate: float = Field(ge=0.0, le=1.0)


class HistogramBin(FrozenModel):
    """One bucket of a numeric distribution.

    Counts per interval, never the values that fell in them. This is what makes
    a distribution drawable without any row reaching the browser or a model:
    the bin edges come from the measured range, and only the tally crosses the
    boundary.
    """

    lower: float
    upper: float
    count: int = Field(ge=0)


class TargetDistribution(FrozenModel):
    """Null-aware distribution of the configured target."""

    target_column: str
    kind: DistributionKind
    total_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    null_rate: float = Field(ge=0.0, le=1.0)
    n_unique: int = Field(ge=0)
    values: list[DistributionValue] = Field(default_factory=list)
    numeric: NumericStats | None = None
    histogram: list[HistogramBin] = Field(
        default_factory=list,
        description=(
            "Binned counts for a numeric target. Quantiles alone cannot show "
            "shape: min/p25/p50/p75/max are identical for a bimodal and a "
            "uniform column, and the difference changes what a person should "
            "do about it."
        ),
    )

    @model_validator(mode="after")
    def _consistent(self) -> TargetDistribution:
        if self.non_null_count + self.null_count != self.total_count:
            raise ValueError("Target distribution counts must add to total_count.")
        if self.kind is DistributionKind.CLASS_COUNTS:
            if self.numeric is not None:
                raise ValueError("Class-count target distributions cannot include numeric stats.")
            if sum(item.count for item in self.values) != self.non_null_count:
                raise ValueError("Class counts must add to non_null_count.")
        elif self.values:
            raise ValueError("Numeric target distributions cannot include class counts.")
        return self


class MissingnessSummary(FrozenModel):
    """Missing-value measurement for one ABT column."""

    column: str
    null_count: int = Field(ge=0)
    null_rate: float = Field(ge=0.0, le=1.0)


class CorrelationMatrix(FrozenModel):
    """Square Pearson correlation matrix in stable DataCard column order."""

    columns: list[str]
    values: list[list[float | None]]

    @model_validator(mode="after")
    def _square_and_bounded(self) -> CorrelationMatrix:
        size = len(self.columns)
        if len(set(self.columns)) != size:
            raise ValueError("Correlation-matrix columns must be unique.")
        if len(self.values) != size or any(len(row) != size for row in self.values):
            raise ValueError("Correlation-matrix values must be square.")
        for row in self.values:
            for value in row:
                if value is not None and not -1.0 <= value <= 1.0:
                    raise ValueError("Correlation values must be in [-1, 1].")
        return self


class TargetRelationship(FrozenModel):
    """Deterministic univariate relationship between one feature and the target."""

    column: str
    pearson_correlation: float | None = Field(default=None, ge=-1.0, le=1.0)
    adjusted_mutual_information: float = Field(ge=0.0, le=1.0)


class ClassBalance(FrozenModel):
    """Observed class counts and imbalance for a classification target."""

    classes: list[DistributionValue] = Field(min_length=2)
    minority_class: str
    minority_count: int = Field(ge=1)
    minority_rate: float = Field(gt=0.0, le=1.0)
    majority_class: str
    majority_count: int = Field(ge=1)
    majority_rate: float = Field(gt=0.0, le=1.0)
    majority_to_minority_ratio: float = Field(ge=1.0)


class OutlierSummary(FrozenModel):
    """IQR-fence outlier count for one numeric feature."""

    column: str
    lower_fence: float
    upper_fence: float
    evaluated_count: int = Field(ge=0)
    outlier_count: int = Field(ge=0)
    outlier_rate: float = Field(ge=0.0, le=1.0)
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    """The quartiles the fences were derived from.

    Carried because a fence is not a box. The fences alone say where outliers
    begin but nothing about where the mass sits, so a reader cannot tell a
    tight distribution with a few far points from a wide one — which is
    precisely the distinction that decides whether to clip, transform, or leave
    the column alone.
    """


class DistributionPeak(FrozenModel):
    """One local histogram maximum under the documented fixed-bin algorithm."""

    bin_index: int = Field(ge=0)
    lower: float
    upper: float
    count: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0)
    neighbour_prominence: float = Field(ge=0.0, le=1.0)


class RoundingConcentration(FrozenModel):
    """Exact share of finite values lying on a candidate numeric multiple."""

    step: float = Field(gt=0.0)
    matching_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    rate: float = Field(ge=0.0, le=1.0)


class NumericDistributionShape(FrozenModel):
    """Descriptors only: histogram peaks and multiples, never a shape conclusion."""

    column: str
    non_null_count: int = Field(ge=0)
    histogram: list[HistogramBin] = Field(default_factory=list)
    candidate_peaks: list[DistributionPeak] = Field(default_factory=list)
    candidate_peak_count: int = Field(ge=0)
    peak_separation_bins: list[int] = Field(default_factory=list)
    most_common_value_rate: float = Field(ge=0.0, le=1.0)
    rounding_concentrations: list[RoundingConcentration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape_counts(self) -> NumericDistributionShape:
        if self.histogram and sum(item.count for item in self.histogram) != self.non_null_count:
            raise ValueError("Histogram bins must add to non_null_count.")
        if self.candidate_peak_count != len(self.candidate_peaks):
            raise ValueError("candidate_peak_count must match candidate_peaks.")
        if len(self.peak_separation_bins) != max(len(self.candidate_peaks) - 1, 0):
            raise ValueError("Peak separations must connect adjacent candidate peaks.")
        if any(item < 1 for item in self.peak_separation_bins):
            raise ValueError("Peak separation must be at least one bin.")
        return self


class PeriodBucket(FrozenModel):
    period: str
    count: int = Field(ge=1)
    rate: float = Field(gt=0.0, le=1.0)


class DatetimePeriodDistribution(FrozenModel):
    """Calendar concentration without a conclusion such as migration or backfill."""

    column: str
    granularity: str = Field(pattern="^(year|month)$")
    parsed_count: int = Field(ge=0)
    buckets: list[PeriodBucket] = Field(default_factory=list)
    dominant_period: str | None = None
    dominant_period_rate: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _period_counts(self) -> DatetimePeriodDistribution:
        if sum(item.count for item in self.buckets) != self.parsed_count:
            raise ValueError("Period buckets must add to parsed_count.")
        if bool(self.buckets) != (self.dominant_period is not None):
            raise ValueError("Dominant period must exist exactly when buckets exist.")
        return self


class EDAReport(Artifact):
    """Numbers-only EDA artifact with mechanically checkable rubric coverage."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.EDA_REPORT
    schema_version: ClassVar[str] = "2"

    problem_title: str
    task_type: TaskType
    target_column: str | None = None
    row_count: int = Field(ge=0)
    feature_columns: list[str]
    model_eligible_columns: list[str]
    covered_columns: list[str]
    target_distribution: TargetDistribution | None = None
    missingness: list[MissingnessSummary]
    correlation_matrix: CorrelationMatrix
    target_relationships: list[TargetRelationship]
    class_balance: ClassBalance | None = None
    outliers: list[OutlierSummary]
    numeric_distributions: list[NumericDistributionShape] = Field(default_factory=list)
    datetime_distributions: list[DatetimePeriodDistribution] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> EDAReport:
        named_lists = {
            "feature_columns": self.feature_columns,
            "model_eligible_columns": self.model_eligible_columns,
            "covered_columns": self.covered_columns,
            "missingness": [item.column for item in self.missingness],
            "target_relationships": [item.column for item in self.target_relationships],
            "outliers": [item.column for item in self.outliers],
        }
        for label, names in named_lists.items():
            if len(names) != len(set(names)):
                raise ValueError(f"{label} must not contain duplicate columns.")
        if not set(self.model_eligible_columns).issubset(self.feature_columns):
            raise ValueError("model_eligible_columns must be a subset of feature_columns.")
        if self.target_column is None and self.target_distribution is not None:
            raise ValueError("A target distribution requires target_column.")
        if self.target_column is not None:
            if self.target_distribution is None:
                raise ValueError("A configured target requires a target distribution.")
            if self.target_distribution.target_column != self.target_column:
                raise ValueError("Target distribution column does not match target_column.")
        if self.task_type in {
            TaskType.BINARY_CLASSIFICATION,
            TaskType.MULTICLASS_CLASSIFICATION,
        }:
            if self.class_balance is None:
                raise ValueError("Classification EDA requires class_balance.")
        elif self.class_balance is not None:
            raise ValueError("class_balance is only valid for classification tasks.")
        return self

    @property
    def target_distribution_reported(self) -> bool:
        """Whether the target-distribution rubric criterion is satisfied."""
        return self.target_column is None or self.target_distribution is not None

    @property
    def all_features_covered(self) -> bool:
        """Whether every non-target ABT feature has an EDA entry."""
        return set(self.feature_columns).issubset(self.covered_columns)

    @property
    def missingness_quantified(self) -> bool:
        """Whether missingness is recorded for every covered column and target."""
        measured = {item.column for item in self.missingness}
        expected = set(self.covered_columns)
        if self.target_column is not None:
            expected.add(self.target_column)
        return expected.issubset(measured)

    def criterion_results(self) -> dict[str, bool]:
        """Return the exact gate-rubric keys and their deterministic outcomes."""
        return {
            "eda.target_distribution_reported": self.target_distribution_reported,
            "eda.all_features_covered": self.all_features_covered,
            "eda.missingness_quantified": self.missingness_quantified,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "problem_title": self.problem_title,
            "row_count": self.row_count,
            "target_column": self.target_column,
            "n_features": len(self.feature_columns),
            "criteria": self.criterion_results(),
        }


__all__ = [
    "ClassBalance",
    "CorrelationMatrix",
    "DatetimePeriodDistribution",
    "DistributionKind",
    "DistributionPeak",
    "DistributionValue",
    "EDAReport",
    "MissingnessSummary",
    "NumericDistributionShape",
    "OutlierSummary",
    "PeriodBucket",
    "RoundingConcentration",
    "TargetDistribution",
    "TargetRelationship",
]
