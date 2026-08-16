"""Deterministic exploratory data analysis and Markdown rendering."""

from ads.contracts.eda import (
    ClassBalance,
    CorrelationMatrix,
    DatetimePeriodDistribution,
    DistributionKind,
    DistributionPeak,
    DistributionValue,
    EDAReport,
    MissingnessSummary,
    NumericDistributionShape,
    OutlierSummary,
    PeriodBucket,
    RoundingConcentration,
    TargetDistribution,
    TargetRelationship,
)
from ads.eda.markdown import render_markdown
from ads.eda.profiler import EDAError, profile_for_eda
from ads.store import register_artifact_type

register_artifact_type(EDAReport)

__all__ = [
    "ClassBalance",
    "CorrelationMatrix",
    "DatetimePeriodDistribution",
    "DistributionKind",
    "DistributionPeak",
    "DistributionValue",
    "EDAError",
    "EDAReport",
    "MissingnessSummary",
    "NumericDistributionShape",
    "OutlierSummary",
    "PeriodBucket",
    "RoundingConcentration",
    "TargetDistribution",
    "TargetRelationship",
    "profile_for_eda",
    "render_markdown",
]
