"""Deterministic measurements that constrain validation strategy selection.

The split strategy is too consequential to trust to model judgment alone. This
module measures entity repetition, temporal coverage, target imbalance and
per-fold support directly from the ABT, then produces a minimum acceptable
strategy using the precedence in ``skills/validation/choosing_a_split.md``.

There are deliberately no LLM imports or calls here.
"""

from __future__ import annotations

import pandas as pd

from ads.contracts.datacard import DataCard, SemanticType
from ads.contracts.problem import TaskType
from ads.contracts.validation import (
    IdentifierContainmentSignal,
    RepeatedEntitySignal,
    SplitStrategy,
    TemporalSpanSignal,
    ValidationSignals,
)

#: More than one calendar day represents multiple time periods.
MIN_TEMPORAL_SPAN_DAYS = 2
#: Skill policy: below this minority rate, preserve class proportions.
IMBALANCE_STRATIFY_RATE = 0.20
#: Generic support floors for an n-fold estimate.
MIN_ROWS_PER_FOLD = 20
MIN_MINORITY_PER_FOLD = 5
MIN_ENTITIES_PER_FOLD = 5

_CLASSIFICATION_TASKS = frozenset(
    {TaskType.BINARY_CLASSIFICATION, TaskType.MULTICLASS_CLASSIFICATION}
)


def _repeated_entities(card: DataCard, frame: pd.DataFrame) -> list[RepeatedEntitySignal]:
    """Measure recurrence and which identifier can contain every recurrence.

    Recurrence is evaluated only for semantic identifiers. A unique-rate cutoff
    answers whether a column resembles a row key, not whether any real entity can
    cross a split boundary, so even sparse long-tail recurrence is retained.

    For each candidate group column G and repeating identifier B, containment
    counts values of B that span multiple values of G. A safe group column has
    zero spanning and zero missing-group values for every repeating domain.
    """
    recurrence: dict[str, dict[str, object]] = {}
    repeated_values: dict[str, pd.Index] = {}

    for profile in card.columns:
        if profile.semantic_type is not SemanticType.IDENTIFIER:
            continue
        if profile.name not in frame.columns:
            continue

        values = frame[profile.name].dropna()
        if values.empty:
            continue
        counts = values.value_counts()
        repeated_counts = counts[counts > 1]
        if repeated_counts.empty:
            continue

        n_entities = int(len(counts))
        repeated_value_count = int(len(repeated_counts))
        repeated_row_count = int(repeated_counts.sum())
        recurrence[profile.name] = {
            "n_entities": n_entities,
            "unique_rate": round(n_entities / len(values), 6),
            "rows_per_entity": round(len(values) / n_entities, 3),
            "repeated_value_count": repeated_value_count,
            "repeated_row_count": repeated_row_count,
            "repeated_row_rate": round(repeated_row_count / len(values), 6),
        }
        repeated_values[profile.name] = repeated_counts.index

    signals: list[RepeatedEntitySignal] = []
    for group_column, measured in recurrence.items():
        containment: list[IdentifierContainmentSignal] = []
        for repeated_column, domain_values in repeated_values.items():
            if group_column == repeated_column:
                # Identity is a measured zero, not a missing matrix cell: each
                # repeated value defines exactly one group of its own and the
                # repeated domain excludes nulls by construction.
                containment.append(
                    IdentifierContainmentSignal(
                        column=repeated_column,
                        repeated_value_count=int(len(domain_values)),
                        spanning_value_count=0,
                        missing_group_value_count=0,
                    )
                )
                continue

            subset = frame.loc[
                frame[repeated_column].isin(domain_values),
                [repeated_column, group_column],
            ]
            group_counts = subset.groupby(
                repeated_column, sort=False, dropna=False
            )[group_column].nunique(dropna=False)
            spanning_value_count = int((group_counts > 1).sum())
            missing_group_value_count = int(
                subset.loc[subset[group_column].isna(), repeated_column].nunique()
            )
            containment.append(
                IdentifierContainmentSignal(
                    column=repeated_column,
                    repeated_value_count=int(len(domain_values)),
                    spanning_value_count=spanning_value_count,
                    missing_group_value_count=missing_group_value_count,
                )
            )

        signals.append(
            RepeatedEntitySignal(
                column=group_column,
                containment=containment,
                **measured,
            )
        )
    return signals


def full_coverage_group_columns(signals: ValidationSignals) -> list[str]:
    """Identifiers measured to keep every repeating identifier domain together."""
    repeated_columns = {item.column for item in signals.repeated_entity_keys}
    safe: list[str] = []
    for candidate in signals.repeated_entity_keys:
        containment = {item.column: item for item in candidate.containment}
        if set(containment) != repeated_columns:
            continue
        if all(
            item.spanning_value_count == 0
            and item.missing_group_value_count == 0
            for item in containment.values()
        ):
            safe.append(candidate.column)
    return safe


def _temporal_spans(card: DataCard, frame: pd.DataFrame) -> list[TemporalSpanSignal]:
    signals: list[TemporalSpanSignal] = []
    for profile in card.columns:
        if profile.semantic_type is not SemanticType.DATETIME:
            continue
        if profile.name not in frame.columns:
            continue

        parsed = pd.to_datetime(frame[profile.name], errors="coerce").dropna()
        if parsed.empty:
            continue
        minimum = parsed.min()
        maximum = parsed.max()
        span_days = int(max((maximum - minimum).days, 0))
        signals.append(
            TemporalSpanSignal(
                column=profile.name,
                min_date=str(minimum),
                max_date=str(maximum),
                span_days=span_days,
            )
        )
    return signals


def _class_support(
    frame: pd.DataFrame,
    target_column: str | None,
    task_type: TaskType,
) -> tuple[int, int | None, float | None]:
    if target_column is None or target_column not in frame.columns:
        return len(frame), None, None

    target = frame[target_column].dropna()
    n_usable_rows = len(target)
    if task_type not in _CLASSIFICATION_TASKS or target.empty:
        return n_usable_rows, None, None

    counts = target.astype(str).value_counts()
    minority_count = int(counts.min())
    minority_rate = round(minority_count / n_usable_rows, 6)
    return n_usable_rows, minority_count, minority_rate


def _small_sample_warnings(
    *,
    n_usable_rows: int,
    n_folds: int,
    minority_class_count: int | None,
    repeated_entities: list[RepeatedEntitySignal],
) -> list[str]:
    warnings: list[str] = []
    rows_per_fold = n_usable_rows / n_folds
    if rows_per_fold < MIN_ROWS_PER_FOLD:
        warnings.append(
            f"few_rows_per_fold: {rows_per_fold:.1f} usable rows per fold with "
            f"n_folds={n_folds}; need at least {MIN_ROWS_PER_FOLD}."
        )

    if minority_class_count is not None:
        minority_per_fold = minority_class_count / n_folds
        if minority_per_fold < MIN_MINORITY_PER_FOLD:
            warnings.append(
                f"few_minority_examples_per_fold: {minority_per_fold:.1f} per fold with "
                f"n_folds={n_folds}; need at least {MIN_MINORITY_PER_FOLD}."
            )

    for entity in repeated_entities:
        entities_per_fold = entity.n_entities / n_folds
        if entities_per_fold < MIN_ENTITIES_PER_FOLD:
            warnings.append(
                f"few_entities_per_fold: {entity.column!r} has {entities_per_fold:.1f} "
                f"entities per fold with n_folds={n_folds}; need at least "
                f"{MIN_ENTITIES_PER_FOLD}."
            )
    return warnings


def detect_validation_signals(
    card: DataCard,
    frame: pd.DataFrame,
    *,
    target_column: str | None,
    task_type: TaskType,
    n_folds: int = 5,
) -> ValidationSignals:
    """Measure the ABT facts that determine an honest validation split."""
    if not 2 <= n_folds <= 20:
        raise ValueError("n_folds must be between 2 and 20")

    repeated = _repeated_entities(card, frame)
    temporal = _temporal_spans(card, frame)
    n_usable_rows, minority_count, minority_rate = _class_support(
        frame, target_column, task_type
    )
    warnings = _small_sample_warnings(
        n_usable_rows=n_usable_rows,
        n_folds=n_folds,
        minority_class_count=minority_count,
        repeated_entities=repeated,
    )
    return ValidationSignals(
        n_rows=len(frame),
        n_usable_rows=n_usable_rows,
        n_folds=n_folds,
        target_column=target_column,
        repeated_entity_keys=repeated,
        temporal_spans=temporal,
        minority_class_count=minority_count,
        minority_class_rate=minority_rate,
        small_sample_warnings=warnings,
    )


def recommend_strategy(signals: ValidationSignals) -> SplitStrategy:
    """Return the minimum safe strategy under the documented precedence."""
    has_repeated_entity = bool(signals.repeated_entity_keys)
    has_multiple_periods = any(
        span.span_days >= MIN_TEMPORAL_SPAN_DAYS for span in signals.temporal_spans
    )
    is_imbalanced = (
        signals.minority_class_rate is not None
        and signals.minority_class_rate < IMBALANCE_STRATIFY_RATE
    )

    if has_repeated_entity and has_multiple_periods:
        return SplitStrategy.GROUPED_TEMPORAL
    if has_repeated_entity:
        return SplitStrategy.GROUPED
    if has_multiple_periods:
        return SplitStrategy.TEMPORAL
    if is_imbalanced:
        return SplitStrategy.STRATIFIED
    return SplitStrategy.RANDOM


def validation_signals_digest(signals: ValidationSignals) -> str:
    """Render measured signals for the agent without exposing raw rows."""
    lines = [
        f"Rows: {signals.n_rows:,} total, {signals.n_usable_rows:,} usable for target",
        f"Planned folds: {signals.n_folds}",
    ]
    if signals.repeated_entity_keys:
        lines.append(
            "REPEATING IDENTIFIER CANDIDATES "
            "(measured; recurrence is not proof of entity semantics):"
        )
        for item in signals.repeated_entity_keys:
            recurrence = (
                f", repeated_values={item.repeated_value_count:,}, "
                f"repeated_rows={item.repeated_row_count:,} "
                f"({item.repeated_row_rate:.2%})"
                if item.repeated_value_count is not None
                and item.repeated_row_count is not None
                and item.repeated_row_rate is not None
                else ", repeat counts not measured in this artifact version"
            )
            lines.append(
                f"  - {item.column}: {item.n_entities:,} distinct values, "
                f"unique_rate={item.unique_rate:.3f}, "
                f"rows_per_value={item.rows_per_entity:.3f}{recurrence}"
            )

        safe_groups = full_coverage_group_columns(signals)
        if safe_groups:
            lines.append(
                "FULL-COVERAGE GROUP CANDIDATES: " + ", ".join(sorted(safe_groups))
            )
        else:
            lines.append("NO FULL-COVERAGE GROUP COLUMN:")
            for candidate in signals.repeated_entity_keys:
                containment = {item.column: item for item in candidate.containment}
                if not containment:
                    lines.append(
                        f"  - grouping by {candidate.column}: containment not measured"
                    )
                    continue
                for repeated_column in sorted(
                    item.column for item in signals.repeated_entity_keys
                ):
                    cell = containment.get(repeated_column)
                    if cell is None:
                        lines.append(
                            f"  - grouping by {candidate.column}: "
                            f"{repeated_column} was not measured"
                        )
                    elif (
                        cell.spanning_value_count > 0
                        or cell.missing_group_value_count > 0
                    ):
                        detail = (
                            f"grouping by {candidate.column} leaves "
                            f"{cell.spanning_value_count} of "
                            f"{cell.repeated_value_count} repeated values of "
                            f"{repeated_column} spanning groups"
                        )
                        if cell.missing_group_value_count:
                            detail += (
                                f" and {cell.missing_group_value_count} with a "
                                "missing group value"
                            )
                        lines.append(f"  - {detail}")
    else:
        lines.append("Repeating identifier candidates: none detected")

    if signals.temporal_spans:
        lines.append("DATETIME SPANS (measured):")
        lines.extend(
            f"  - {item.column}: {item.min_date[:10]}..{item.max_date[:10]} "
            f"({item.span_days:,} days)"
            for item in signals.temporal_spans
        )
    else:
        lines.append("Datetime spans: none detected")

    if signals.minority_class_rate is not None:
        lines.append(
            "Minority class: "
            f"{signals.minority_class_count:,} rows "
            f"({signals.minority_class_rate:.2%})"
        )
    if signals.small_sample_warnings:
        lines.append("SMALL-SAMPLE WARNINGS:")
        lines.extend(f"  - {warning}" for warning in signals.small_sample_warnings)
    lines.append(f"Deterministic minimum strategy: {recommend_strategy(signals).value}")
    return "\n".join(lines)


__all__ = [
    "IMBALANCE_STRATIFY_RATE",
    "MIN_ENTITIES_PER_FOLD",
    "MIN_MINORITY_PER_FOLD",
    "MIN_ROWS_PER_FOLD",
    "MIN_TEMPORAL_SPAN_DAYS",
    "RepeatedEntitySignal",
    "TemporalSpanSignal",
    "ValidationSignals",
    "detect_validation_signals",
    "full_coverage_group_columns",
    "recommend_strategy",
    "validation_signals_digest",
]
