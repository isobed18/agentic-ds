"""Deterministic feasibility measurement for proposed ML problems.

This module is the reason the Problem Discovery gate can be trusted. The agent
proposes framings; this measures whether the data can actually support them, and
returns **blocking reasons** that are facts rather than opinions.

The motivating case: on the sample data, "fraud detection" on ``flagged`` reads
as an excellent idea and is one a stakeholder will ask for. It has 39 positives
at a 0.26% rate. No amount of modelling fixes that, and no LLM should be trusted
to notice it reliably. A count does.

Thresholds are conservative and explicit. They are policy, so they live here as
named constants rather than being buried in conditionals.
"""

from __future__ import annotations

import pandas as pd

from ads.contracts.datacard import (
    ColumnProfile,
    DataCard,
    SemanticType,
    Sensitivity,
)
from ads.contracts.problem import SUPERVISED_TASKS, ProblemSupport, TaskType

#: Below this many usable rows, nothing is learnable.
MIN_ROWS = 100
#: Below this many minority-class examples, a classifier cannot generalise.
MIN_MINORITY_COUNT = 50
#: Below this, the target is too sparsely populated to model.
MAX_TARGET_NULL_RATE = 0.60
#: Fewer rows than this per feature invites overfitting.
MIN_ROWS_PER_FEATURE = 5.0
#: Warn (not block) below this positive rate.
IMBALANCE_WARN_RATE = 0.05
#: A multiclass target with more levels than this is really something else.
MAX_CLASSES = 50

#: Semantic types that can never serve as a model feature.
_UNUSABLE_FEATURE_TYPES = frozenset(
    {SemanticType.IDENTIFIER, SemanticType.CONSTANT, SemanticType.EMPTY, SemanticType.UNKNOWN}
)


def usable_feature_columns(
    card: DataCard, target_column: str | None, excluded: frozenset[str] = frozenset()
) -> list[ColumnProfile]:
    """Columns that could serve as model features.

    PII is excluded by its explicit sensitivity classification, independently of
    semantic type. Identifiers are excluded because a model that keys off an id
    memorises rows; constants and empty columns because they carry no signal.
    """
    return [
        col
        for col in card.columns
        if col.name != target_column
        and col.name not in excluded
        and col.sensitivity is not Sensitivity.PII
        and col.semantic_type not in _UNUSABLE_FEATURE_TYPES
    ]


def _class_counts(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str).value_counts()


def _check_task_target_compatibility(
    task_type: TaskType, profile: ColumnProfile, n_classes: int | None
) -> list[str]:
    """Reject framings the target's own shape contradicts."""
    reasons: list[str] = []

    if profile.semantic_type is SemanticType.IDENTIFIER:
        reasons.append(
            f"target_is_identifier: {profile.name!r} is an identifier; predicting it is "
            "meaningless."
        )
    if profile.semantic_type is SemanticType.CONSTANT:
        reasons.append(f"target_is_constant: {profile.name!r} has a single value.")
    if profile.semantic_type is SemanticType.EMPTY:
        reasons.append(f"target_is_empty: {profile.name!r} is entirely null.")

    if task_type is TaskType.BINARY_CLASSIFICATION and n_classes not in (None, 2):
        reasons.append(
            f"task_target_mismatch: binary classification needs exactly 2 classes, "
            f"{profile.name!r} has {n_classes}."
        )
    if task_type is TaskType.MULTICLASS_CLASSIFICATION:
        if n_classes is not None and n_classes < 3:
            reasons.append(
                f"task_target_mismatch: multiclass needs 3+ classes, "
                f"{profile.name!r} has {n_classes}."
            )
        if n_classes is not None and n_classes > MAX_CLASSES:
            reasons.append(
                f"too_many_classes: {profile.name!r} has {n_classes} levels "
                f"(limit {MAX_CLASSES}); this is not a classification target."
            )
    if task_type is TaskType.REGRESSION and profile.semantic_type not in (
        SemanticType.NUMERIC_CONTINUOUS,
        SemanticType.NUMERIC_DISCRETE,
    ):
        reasons.append(
            f"task_target_mismatch: regression needs a numeric target, "
            f"{profile.name!r} is {profile.semantic_type.value}."
        )

    return reasons


def compute_support(
    card: DataCard,
    frame: pd.DataFrame,
    *,
    target_column: str | None,
    task_type: TaskType,
    excluded_columns: frozenset[str] = frozenset(),
) -> ProblemSupport:
    """Measure whether the data can support a proposed problem."""
    features = usable_feature_columns(card, target_column, excluded_columns)
    n_features = len(features)

    blocking: list[str] = []
    warnings: list[str] = []

    if task_type not in SUPERVISED_TASKS:
        # Unsupervised: no target, so support is about rows and features only.
        n_rows = int(len(frame))
        if n_rows < MIN_ROWS:
            blocking.append(f"insufficient_rows: {n_rows} rows, need at least {MIN_ROWS}.")
        if n_features == 0:
            blocking.append("no_usable_features: every column is an identifier or constant.")
        return ProblemSupport(
            n_rows=n_rows,
            target_null_rate=0.0,
            n_usable_features=n_features,
            rows_per_feature=round(n_rows / n_features, 2) if n_features else 0.0,
            blocking_reasons=blocking,
            warnings=warnings,
        )

    profile = card.column(target_column) if target_column else None
    if target_column is None or profile is None:
        return ProblemSupport(
            n_rows=0,
            target_null_rate=1.0,
            n_usable_features=n_features,
            rows_per_feature=0.0,
            blocking_reasons=[
                f"unknown_target: column {target_column!r} is not present in the ABT."
            ],
        )

    series = frame[target_column] if target_column in frame.columns else pd.Series(dtype="object")
    labelled = series.dropna()
    n_rows = int(len(labelled))

    n_classes: int | None = None
    minority_count: int | None = None
    minority_rate: float | None = None

    is_classification = task_type in (
        TaskType.BINARY_CLASSIFICATION,
        TaskType.MULTICLASS_CLASSIFICATION,
    )
    if is_classification and n_rows:
        counts = _class_counts(labelled)
        n_classes = int(len(counts))
        minority_count = int(counts.min())
        minority_rate = round(minority_count / n_rows, 6)

    blocking.extend(_check_task_target_compatibility(task_type, profile, n_classes))

    if n_rows < MIN_ROWS:
        blocking.append(
            f"insufficient_rows: only {n_rows} labelled rows, need at least {MIN_ROWS}."
        )
    if profile.null_rate > MAX_TARGET_NULL_RATE:
        blocking.append(
            f"target_too_sparse: {profile.null_rate:.1%} of {target_column!r} is null "
            f"(limit {MAX_TARGET_NULL_RATE:.0%})."
        )
    if n_features == 0:
        blocking.append("no_usable_features: every other column is an identifier or constant.")

    if is_classification and minority_count is not None:
        if minority_count < MIN_MINORITY_COUNT:
            blocking.append(
                f"minority_class_below_floor: smallest class has {minority_count} examples "
                f"({minority_rate:.2%}), need at least {MIN_MINORITY_COUNT}. "
                "The data cannot support this framing regardless of model choice."
            )
        elif minority_rate is not None and minority_rate < IMBALANCE_WARN_RATE:
            warnings.append(
                f"severe_imbalance: minority class at {minority_rate:.2%}. "
                "Use average_precision rather than accuracy, and expect to need "
                "resampling or class weights."
            )

    rows_per_feature = round(n_rows / n_features, 2) if n_features else 0.0
    if n_features and rows_per_feature < MIN_ROWS_PER_FEATURE:
        warnings.append(
            f"few_rows_per_feature: {rows_per_feature} rows per feature "
            f"(recommended at least {MIN_ROWS_PER_FEATURE}). High overfitting risk."
        )
    if 0 < profile.null_rate <= MAX_TARGET_NULL_RATE:
        warnings.append(
            f"target_missingness: {profile.null_rate:.1%} of rows have no target and "
            "will be dropped from training."
        )

    return ProblemSupport(
        n_rows=n_rows,
        target_null_rate=profile.null_rate,
        n_classes=n_classes,
        minority_class_count=minority_count,
        minority_class_rate=minority_rate,
        n_usable_features=n_features,
        rows_per_feature=rows_per_feature,
        blocking_reasons=blocking,
        warnings=warnings,
    )


def support_digest(card: DataCard) -> str:
    """Render candidate-target facts for the agent's context.

    Gives the model the shape of every plausible target up front, so it proposes
    framings that fit the data rather than framings it then has to defend.
    """
    lines = ["CANDIDATE TARGET COLUMNS (measured):"]
    for col in card.candidate_targets():
        parts = [f"  - {col.name}: {col.semantic_type.value}", f"non_null={1 - col.null_rate:.1%}"]
        parts.append(f"distinct={col.n_unique:,}")
        if col.top_values:
            top = ", ".join(f"{v.value}={v.count:,}" for v in col.top_values[:4])
            parts.append(f"counts=[{top}]")
        if col.numeric:
            parts.append(f"range=[{col.numeric.min:.4g}..{col.numeric.max:.4g}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


__all__ = [
    "IMBALANCE_WARN_RATE",
    "MAX_TARGET_NULL_RATE",
    "MIN_MINORITY_COUNT",
    "MIN_ROWS",
    "MIN_ROWS_PER_FEATURE",
    "compute_support",
    "support_digest",
    "usable_feature_columns",
]
