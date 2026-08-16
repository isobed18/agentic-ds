"""Controlled single-fault mutation operators for Assurance Corpus v0.1."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from generators.readmission import POST_OUTCOME_FEATURE


def include_post_outcome_feature(
    base_features: list[str],
    feature: str = POST_OUTCOME_FEATURE,
) -> list[str]:
    """Return a new feature list with a post-outcome field included once."""
    if feature in base_features:
        raise ValueError(f"{feature} is already present")
    return [*base_features, feature]


def contaminate_entity_split(
    rows: list[dict[str, Any]],
    *,
    entity_field: str,
    time_field: str,
    count: int = 12,
) -> list[dict[str, Any]]:
    """Reassign distinct holdout rows to development entities."""
    mutated = deepcopy(rows)
    development_entities = sorted(
        {row[entity_field] for row in rows if row[time_field] < "2023-01-01"}
    )[:count]
    holdout_rows = [
        row for row in mutated if row[time_field] >= "2023-01-01"
    ][:count]
    if len(development_entities) < count or len(holdout_rows) < count:
        raise AssertionError("not enough rows to contaminate entity split")
    for row, entity in zip(holdout_rows, development_entities, strict=True):
        row[entity_field] = entity
    _assert_entity_overlap(mutated, entity_field=entity_field, time_field=time_field)
    return mutated


def use_full_history(
    rows: list[dict[str, Any]],
    *,
    entity_field: str,
    history_field: str,
) -> list[dict[str, Any]]:
    """Replace a windowed prior count with a full-extract entity count."""
    totals = Counter(row[entity_field] for row in rows)
    mutated = deepcopy(rows)
    for row in mutated:
        row[history_field] = totals[row[entity_field]] - 1
    if mutated == rows:
        raise AssertionError("full-history mutation made no change")
    return mutated


def add_cross_fold_duplicates(
    rows: list[dict[str, Any]],
    *,
    entity_field: str,
    record_field: str,
    time_field: str,
    recorded_at_field: str,
    entity_prefix: str,
    record_prefix: str,
    count: int = 16,
) -> list[dict[str, Any]]:
    """Add future near-duplicates under new identifiers, preserving group disjointness."""
    mutated = deepcopy(rows)
    candidates = sorted(rows, key=lambda row: (row[time_field], row[record_field]))[:count]
    for index, source in enumerate(candidates):
        clone = dict(source)
        prediction_time = date(2023, 7, 1) + timedelta(days=index)
        clone[entity_field] = f"{entity_prefix}{index:05d}"
        clone[record_field] = f"{record_prefix}D{index:05d}"
        clone[time_field] = prediction_time.isoformat()
        clone[recorded_at_field] = (prediction_time + timedelta(days=91)).isoformat()
        mutated.append(clone)
    _assert_no_entity_overlap(mutated, entity_field=entity_field, time_field=time_field)
    return sorted(mutated, key=lambda row: (row[time_field], row[record_field]))


def make_missingness_outcome_dependent(
    rows: list[dict[str, Any]],
    *,
    feature_field: str,
    target_field: str,
) -> list[dict[str, Any]]:
    """Mask a pre-prediction feature whenever the eventual target is positive."""
    mutated = deepcopy(rows)
    changed = 0
    for row in mutated:
        if row[target_field] == 1 and row[feature_field] != "":
            row[feature_field] = ""
            changed += 1
    if not changed:
        raise AssertionError("outcome-dependent missingness mutation made no change")
    return mutated


def make_labels_immature(
    rows: list[dict[str, Any]],
    *,
    record_field: str,
    time_field: str,
    recorded_at_field: str,
    horizon_days: int,
    count: int = 24,
) -> list[dict[str, Any]]:
    """Move holdout predictions close to extraction while retaining future-derived labels."""
    mutated = deepcopy(rows)
    selected = sorted(mutated, key=lambda row: row[time_field])[-count:]
    for index, row in enumerate(selected):
        prediction_time = date(2024, 1, 5) + timedelta(days=index % 20)
        row[record_field] = f"{row[record_field]}I"
        row[time_field] = prediction_time.isoformat()
        row[recorded_at_field] = (
            prediction_time + timedelta(days=horizon_days + 1)
        ).isoformat()
    return sorted(mutated, key=lambda row: (row[time_field], row[record_field]))


def assert_artifact_isolation(
    *,
    variant: str,
    data_hash: str,
    notebook_hash: str,
    safe_data_hash: str,
    safe_notebook_hash: str,
    notebook_only: set[str],
    data_only: set[str],
    brief_only: set[str],
) -> None:
    """Counter-check which public artifact plane a mutation is allowed to change."""
    if variant in notebook_only and data_hash != safe_data_hash:
        raise AssertionError(f"{variant} unexpectedly changed dataset bytes")
    if variant in data_only and notebook_hash != safe_notebook_hash:
        raise AssertionError(f"{variant} unexpectedly changed notebook bytes")
    if variant in brief_only and (
        data_hash != safe_data_hash or notebook_hash != safe_notebook_hash
    ):
        raise AssertionError(f"{variant} unexpectedly changed data or notebook")


def _assert_entity_overlap(
    rows: list[dict[str, Any]],
    *,
    entity_field: str,
    time_field: str,
) -> None:
    before = {row[entity_field] for row in rows if row[time_field] < "2023-01-01"}
    after = {row[entity_field] for row in rows if row[time_field] >= "2023-01-01"}
    if not before & after:
        raise AssertionError("entity contamination did not create cross-fold overlap")


def _assert_no_entity_overlap(
    rows: list[dict[str, Any]],
    *,
    entity_field: str,
    time_field: str,
) -> None:
    before = {row[entity_field] for row in rows if row[time_field] < "2023-01-01"}
    after = {row[entity_field] for row in rows if row[time_field] >= "2023-01-01"}
    if before & after:
        raise AssertionError("duplicate mutation also introduced entity overlap")
