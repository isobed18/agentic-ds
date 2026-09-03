"""Deterministic, row-free data-science evidence tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pandas as pd

from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.contracts.problem import TaskType
from ads.discovery import correlation_strength, detect_validation_signals
from ads.intake import (
    ProfileOptions,
    detect_primary_keys,
    measure_composite_relationship,
    measure_relationship,
    profile_column,
)
from ads.tools.models import ToolPayload, ToolRuntime
from ads.tools.registry import ToolDefinition, ToolHandler, ToolRegistry


def _table(runtime: ToolRuntime, table: str) -> pd.DataFrame:
    try:
        return runtime.frames[table]
    except KeyError as exc:
        raise ValueError(f"Unknown table {table!r}; available: {sorted(runtime.frames)}") from exc


def _card(runtime: ToolRuntime, table: str) -> DataCard:
    try:
        return runtime.cards[table]
    except KeyError as exc:
        raise ValueError(f"No DataCard is available for table {table!r}.") from exc


def _series(runtime: ToolRuntime, table: str, column: str) -> pd.Series:
    frame = _table(runtime, table)
    if column not in frame.columns:
        raise ValueError(f"Unknown column {column!r} in table {table!r}.")
    return frame[column]


def _profile(runtime: ToolRuntime, table: str, column: str):
    series = _series(runtime, table, column)
    return profile_column(
        column,
        series,
        len(series),
        ProfileOptions(include_samples=False),
    )


def _summary(tool_id: str, data: Mapping[str, Any]) -> str:
    rendered = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return f"{tool_id} measured {rendered}"


def column_profile(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table, column = str(arguments["table"]), str(arguments["column"])
    profile = _profile(runtime, table, column)
    data = {
        "table": table,
        "column": column,
        "dtype": profile.dtype,
        "semantic_type": profile.semantic_type.value,
        "sensitivity": profile.sensitivity.value,
        "null_count": profile.null_count,
        "null_rate": profile.null_rate,
        "n_unique": profile.n_unique,
        "unique_rate": profile.unique_rate,
        "is_unique": profile.is_unique,
        "numeric": profile.numeric.model_dump(mode="json") if profile.numeric else None,
        "datetime": profile.datetime.model_dump(mode="json") if profile.datetime else None,
        "notes": profile.notes,
    }
    return ToolPayload(summary=_summary("column_profile", data), data=data)


def value_counts(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table, column = str(arguments["table"]), str(arguments["column"])
    profile = _profile(runtime, table, column)
    redacted = profile.sensitivity.value == "pii"
    data = {
        "table": table,
        "column": column,
        "redacted": redacted,
        "n_unique": profile.n_unique,
        "non_null_count": len(_series(runtime, table, column)) - profile.null_count,
        "top_values": (
            [] if redacted else [item.model_dump(mode="json") for item in profile.top_values]
        ),
    }
    if redacted:
        data["redaction_reason"] = "Column classified as PII; values are never returned."
    return ToolPayload(summary=_summary("value_counts", data), data=data)


def correlation(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table = str(arguments["table"])
    left, right = str(arguments["left_column"]), str(arguments["right_column"])
    strength = correlation_strength(
        _series(runtime, table, left),
        _series(runtime, table, right),
    )
    data = {
        "table": table,
        "left_column": left,
        "right_column": right,
        "max_absolute_pearson_spearman": (round(strength, 6) if strength is not None else None),
    }
    return ToolPayload(summary=_summary("correlation", data), data=data)


def cardinality(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table, column = str(arguments["table"]), str(arguments["column"])
    profile = _profile(runtime, table, column)
    data = {
        "table": table,
        "column": column,
        "n_unique": profile.n_unique,
        "unique_rate": profile.unique_rate,
        "is_unique": profile.is_unique,
        "semantic_type": profile.semantic_type.value,
    }
    return ToolPayload(summary=_summary("cardinality", data), data=data)


def null_rate(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table, column = str(arguments["table"]), str(arguments["column"])
    profile = _profile(runtime, table, column)
    data = {
        "table": table,
        "column": column,
        "null_count": profile.null_count,
        "null_rate": profile.null_rate,
        "row_count": len(_series(runtime, table, column)),
    }
    return ToolPayload(summary=_summary("null_rate", data), data=data)


def candidate_keys(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    table = str(arguments["table"])
    candidates = detect_primary_keys(_card(runtime, table), _table(runtime, table))
    data = {
        "table": table,
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }
    return ToolPayload(summary=_summary("candidate_keys", data), data=data)


def _join_columns(arguments: Mapping[str, Any], side: str) -> list[str]:
    """The columns on one side of a join, singular or plural (#381).

    This tool took only `from_column`/`to_column`, so an agent that suspected a
    composite join had no way to measure one -- it was told "no candidate
    relationships found above the overlap threshold" and handed a tool that
    could not check the thing it needed to check. Both spellings are accepted so
    every existing call keeps working.
    """
    plural = arguments.get(f"{side}_columns")
    if plural is not None:
        columns = (
            [str(column) for column in plural]
            if isinstance(plural, list | tuple)
            else [str(plural)]
        )
    else:
        columns = [str(arguments[f"{side}_column"])]
    if not columns:
        raise ValueError(f"{side}_columns must name at least one column.")
    return columns


def join_overlap(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    from_table = str(arguments["from_table"])
    to_table = str(arguments["to_table"])
    from_columns = _join_columns(arguments, "from")
    to_columns = _join_columns(arguments, "to")
    if len(from_columns) != len(to_columns):
        raise ValueError("A join must name the same number of columns on both sides.")
    for column in from_columns:
        _series(runtime, from_table, column)
    for column in to_columns:
        _series(runtime, to_table, column)
    if len(from_columns) == 1:
        result = measure_relationship(
            from_table,
            from_columns[0],
            _series(runtime, from_table, from_columns[0]),
            to_table,
            to_columns[0],
            _series(runtime, to_table, to_columns[0]),
        )
    else:
        result = measure_composite_relationship(
            from_table,
            from_columns,
            _table(runtime, from_table),
            to_table,
            to_columns,
            _table(runtime, to_table),
        )
    data = result.model_dump(mode="json")
    data["confidence"] = result.confidence
    return ToolPayload(summary=_summary("join_overlap", data), data=data)


def validation_signals(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    """Expose the existing split-safety measurements through the same broker."""
    table = str(arguments["table"])
    signals = detect_validation_signals(
        _card(runtime, table),
        _table(runtime, table),
        target_column=str(arguments["target_column"]),
        task_type=TaskType(str(arguments["task_type"])),
        n_folds=int(arguments.get("n_folds", 3)),
    )
    data = signals.model_dump(mode="json")
    return ToolPayload(summary=_summary("validation_signals", data), data=data)


_TABLE_COLUMN = {"table": "table name", "column": "column name in that table"}

# The description used to be generated from the tool id — "Deterministically
# measure column profile." — which told an agent nothing it could not read off
# the name, and named no arguments at all. Each entry now states what comes back
# and exactly what to pass.
_DS_TOOLS: tuple[tuple[str, PermissionTier, ToolHandler, str, dict[str, str]], ...] = (
    (
        "column_profile",
        PermissionTier.READ_DATA,
        column_profile,
        "Full profile of one column: dtype, semantic type, null rate, uniqueness, distribution.",
        _TABLE_COLUMN,
    ),
    (
        "value_counts",
        PermissionTier.READ_DATA,
        value_counts,
        "Most frequent values of one column with their counts.",
        _TABLE_COLUMN,
    ),
    (
        "correlation",
        PermissionTier.READ_DATA,
        correlation,
        "Correlation between two numeric columns of one table.",
        {
            "table": "table name",
            "left_column": "first numeric column",
            "right_column": "second numeric column",
        },
    ),
    (
        "cardinality",
        PermissionTier.READ_DATA,
        cardinality,
        "Distinct-value count and uniqueness rate of one column.",
        _TABLE_COLUMN,
    ),
    (
        "null_rate",
        PermissionTier.READ_DATA,
        null_rate,
        "Missing-value count and rate of one column.",
        _TABLE_COLUMN,
    ),
    (
        "candidate_keys",
        PermissionTier.READ_DATA,
        candidate_keys,
        "Columns that uniquely identify a row of one table.",
        {"table": "table name"},
    ),
    (
        "join_overlap",
        PermissionTier.READ_DATA,
        join_overlap,
        "Measure whether two tables actually join on a column or a set of columns: "
        "row overlap, distinct overlap and parent coverage.",
        {
            "from_table": "left table name",
            "from_column": "left join column (one column)",
            "from_columns": (
                "left join columns, for a composite key (list; use instead of from_column)"
            ),
            "to_table": "right table name",
            "to_column": "right join column (one column)",
            "to_columns": "right join columns, same length as from_columns",
        },
    ),
    (
        "validation_signals",
        PermissionTier.READ_DATA,
        validation_signals,
        "Split-safety measurements: repeated entities, temporal spans, containment.",
        {
            "table": "table name",
            "target_column": "target column",
            "task_type": "regression | binary_classification | multiclass_classification",
            "n_folds": "optional integer, default 3",
        },
    ),
)


def register_ds_tools(registry: ToolRegistry) -> ToolRegistry:
    for tool_id, tier, handler, description, arguments in _DS_TOOLS:
        registry.register(
            ToolDefinition(
                tool_id=tool_id,
                tier=tier,
                description=description,
                handler=handler,
                arguments=arguments,
            )
        )
    return registry


__all__ = [
    "candidate_keys",
    "cardinality",
    "column_profile",
    "correlation",
    "join_overlap",
    "null_rate",
    "register_ds_tools",
    "validation_signals",
    "value_counts",
]
