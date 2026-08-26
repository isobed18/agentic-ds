"""Deterministic key and relationship detection.

Set operations, not judgment. This module measures *what overlaps*; the
SchemaDiscoveryAgent decides *what it means* — whether a 94.2% overlap is a real
foreign key with a business-meaningful orphan rate, or a coincidence between two
unrelated integer columns.

Producing this evidence deterministically is what keeps the agent's job small
enough for a 27B local model: it interprets a short ranked list instead of
trying to infer relationships from raw data it cannot see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

import pandas as pd

from ads.contracts.datacard import DataCard, SemanticType
from ads.contracts.integration import (
    Cardinality,
    KeyCandidate,
    RelationshipCandidate,
    RowsPerParentStats,
)

# Semantic types that can plausibly participate in a join key.
_JOINABLE = frozenset(
    {
        SemanticType.IDENTIFIER,
        SemanticType.CATEGORICAL,
        SemanticType.NUMERIC_DISCRETE,
        SemanticType.BOOLEAN,
    }
)

_MIN_OVERLAP = 0.30
_MIN_DISTINCT = 2
_MAX_COMPOSITE_WIDTH = 2

# Tokens too generic to indicate a real name relationship between columns.
_GENERIC_TOKENS = frozenset({"id", "no", "code", "key", "ref", "num", "number", "nr"})


@dataclass(frozen=True)
class KeyDetectionOptions:
    min_overlap: float = _MIN_OVERLAP
    """Below this, a column pair is noise rather than a candidate relationship."""
    min_parent_coverage: float = 0.50
    """Child must reference at least this share of the parent's keys, unless names match."""
    min_name_affinity: float = 0.50
    """Name-token similarity that alone justifies a candidate despite low coverage."""
    detect_composite_keys: bool = True
    max_composite_width: int = _MAX_COMPOSITE_WIDTH
    max_candidates_per_pair: int = 5


def _normalize_for_join(series: pd.Series) -> pd.Series:
    """Coerce to a comparable form so int64 vs object keys still match.

    Enterprise exports routinely store the same id as ``1234`` in one table and
    ``"1234"`` in another; comparing raw dtypes would miss every such join.
    """
    non_null = series.dropna()
    if non_null.empty:
        return non_null.astype(str)
    if pd.api.types.is_numeric_dtype(non_null):
        as_float = pd.to_numeric(non_null, errors="coerce").dropna()
        if (as_float % 1 == 0).all():
            return as_float.astype("int64").astype(str)
        return as_float.astype(str)
    return non_null.astype(str).str.strip()


def detect_primary_keys(
    card: DataCard, frame: pd.DataFrame, options: KeyDetectionOptions | None = None
) -> list[KeyCandidate]:
    """Find single and (optionally) composite candidate primary keys."""
    options = options or KeyDetectionOptions()
    candidates: list[KeyCandidate] = []

    single_key_columns: set[str] = set()
    for col in card.columns:
        if col.name not in frame.columns:
            continue
        # Continuous measurements are excluded even when unique in this sample:
        # a distinct float is a coincidence, not an identifier. Admitting them
        # also suppresses composite-key search on tables with no real PK.
        if col.semantic_type in (
            SemanticType.EMPTY,
            SemanticType.CONSTANT,
            SemanticType.NUMERIC_CONTINUOUS,
        ):
            continue
        if col.is_unique and col.n_unique >= _MIN_DISTINCT:
            single_key_columns.add(col.name)
            candidates.append(
                KeyCandidate(
                    table=card.table_name,
                    columns=[col.name],
                    is_unique=True,
                    null_rate=col.null_rate,
                    n_distinct=col.n_unique,
                )
            )

    if not options.detect_composite_keys or len(frame) == 0:
        return candidates

    # Only consider composites when no clean single-column key exists — a table
    # with a real PK does not need a composite one, and the search is O(n^2).
    if single_key_columns:
        return candidates

    eligible = [
        c.name
        for c in card.columns
        if c.name in frame.columns
        and c.semantic_type in _JOINABLE
        and c.null_rate < 0.05
        and 1 < c.n_unique < len(frame)
    ]
    eligible = eligible[:12]  # bound the combinatorics on wide tables

    for width in range(2, options.max_composite_width + 1):
        for combo in combinations(eligible, width):
            subset = frame.loc[:, list(combo)].dropna()
            if subset.empty:
                continue
            n_distinct = int(len(subset.drop_duplicates()))
            if n_distinct == len(subset) and n_distinct >= _MIN_DISTINCT:
                candidates.append(
                    KeyCandidate(
                        table=card.table_name,
                        columns=list(combo),
                        is_unique=True,
                        null_rate=round(1 - len(subset) / max(len(frame), 1), 6),
                        n_distinct=n_distinct,
                    )
                )
        if len(candidates) > 0:
            break  # narrowest composite wins; wider ones are redundant

    return candidates


def _cardinality(from_unique: bool, to_unique: bool) -> Cardinality:
    if from_unique and to_unique:
        return Cardinality.ONE_TO_ONE
    if to_unique:
        return Cardinality.MANY_TO_ONE
    if from_unique:
        return Cardinality.ONE_TO_MANY
    return Cardinality.MANY_TO_MANY


def _name_tokens(name: str) -> set[str]:
    """Split a column name into meaningful tokens, dropping generic filler."""
    tokens = {t for t in re.split(r"[^0-9a-zA-Z]+", name.lower()) if len(t) > 1}
    return tokens - _GENERIC_TOKENS


def name_affinity(left: str, right: str) -> float:
    """Jaccard similarity of two column names' meaningful tokens.

    ``physician_id`` vs ``physician_id`` scores 1.0; ``provider_ref`` vs
    ``physician_id`` scores 0.0 — which is why affinity alone cannot gate a
    relationship, only rank one.
    """
    a, b = _name_tokens(left), _name_tokens(right)
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 6)


def measure_relationship(
    from_table: str,
    from_column: str,
    from_values: pd.Series,
    to_table: str,
    to_column: str,
    to_values: pd.Series,
) -> RelationshipCandidate:
    """Measure directional containment of ``from_column`` values into ``to_column``.

    Two ratios are computed because overlap alone produces false positives: any
    small integer column is fully "contained" in a large sequential id column.
    ``parent_coverage`` is what separates a genuine foreign key from that
    coincidence.
    """
    left = _normalize_for_join(from_values)
    right = _normalize_for_join(to_values)

    left_set = set(left.unique())
    right_set = set(right.unique())
    shared = left_set & right_set

    # Row-level overlap is the headline number — a few thousand distinct bogus
    # ids spread over a handful of rows must not read as a broken relationship.
    overlap_rate = float(left.isin(right_set).mean()) if len(left) else 0.0
    distinct_overlap_rate = len(shared) / len(left_set) if left_set else 0.0
    parent_coverage = len(shared) / len(right_set) if right_set else 0.0

    from_unique = len(left_set) == len(left) and len(left) > 0
    to_unique = len(right_set) == len(right) and len(right) > 0

    dtype_compatible = (
        pd.api.types.is_numeric_dtype(from_values) == pd.api.types.is_numeric_dtype(to_values)
    ) or overlap_rate > 0.0

    matched = left[left.isin(right_set)]
    rows_by_referenced_parent = matched.value_counts()
    referenced_counts = rows_by_referenced_parent.astype(float)
    zero_parent_count = max(len(right_set) - len(rows_by_referenced_parent), 0)
    all_parent_counts = pd.concat(
        [
            referenced_counts,
            pd.Series([0.0] * zero_parent_count, dtype="float64"),
        ],
        ignore_index=True,
    )
    rows_per_parent = RowsPerParentStats(
        child_non_null_rows=len(left),
        matched_child_rows=len(matched),
        referenced_parent_count=len(rows_by_referenced_parent),
        all_parent_count=len(right_set),
        mean_rows_per_referenced_parent=round(
            float(referenced_counts.mean()) if len(referenced_counts) else 0.0,
            6,
        ),
        mean_rows_per_all_parents=round(
            float(all_parent_counts.mean()) if len(all_parent_counts) else 0.0,
            6,
        ),
        median_rows_per_referenced_parent=round(
            float(referenced_counts.quantile(0.50)) if len(referenced_counts) else 0.0,
            6,
        ),
        p90_rows_per_referenced_parent=round(
            float(referenced_counts.quantile(0.90)) if len(referenced_counts) else 0.0,
            6,
        ),
        median_rows_per_all_parents=round(
            float(all_parent_counts.quantile(0.50)) if len(all_parent_counts) else 0.0,
            6,
        ),
        p90_rows_per_all_parents=round(
            float(all_parent_counts.quantile(0.90)) if len(all_parent_counts) else 0.0,
            6,
        ),
    )

    return RelationshipCandidate(
        from_table=from_table,
        from_columns=[from_column],
        to_table=to_table,
        to_columns=[to_column],
        overlap_rate=round(overlap_rate, 6),
        orphan_rate=round(1.0 - overlap_rate, 6),
        distinct_overlap_rate=round(distinct_overlap_rate, 6),
        parent_coverage=round(parent_coverage, 6),
        name_affinity=name_affinity(from_column, to_column),
        n_from_distinct=len(left_set),
        n_to_distinct=len(right_set),
        cardinality=_cardinality(from_unique, to_unique),
        dtype_compatible=bool(dtype_compatible),
        rows_per_parent=rows_per_parent,
    )


def _is_plausible_relationship(rel: RelationshipCandidate, options: KeyDetectionOptions) -> bool:
    """Reject numerically-coincidental overlaps.

    A candidate must clear the overlap floor *and* show one of two independent
    signs of a real reference: the child names itself after the parent, or the
    child actually exercises a meaningful share of the parent's key space.
    """
    if rel.overlap_rate < options.min_overlap:
        return False
    if rel.n_from_distinct < _MIN_DISTINCT or rel.n_to_distinct < _MIN_DISTINCT:
        return False
    return (
        rel.name_affinity >= options.min_name_affinity
        or rel.parent_coverage >= options.min_parent_coverage
    )


def detect_relationships(
    cards: list[DataCard],
    frames: dict[str, pd.DataFrame],
    options: KeyDetectionOptions | None = None,
) -> list[RelationshipCandidate]:
    """Measure candidate foreign-key relationships across every table pair.

    Directional: a child column pointing into a parent key is what we look for,
    so both directions of each pair are measured and only those clearing
    ``min_overlap`` are kept.
    """
    options = options or KeyDetectionOptions()
    by_name = {c.table_name: c for c in cards}
    results: list[RelationshipCandidate] = []

    for left_name, right_name in combinations(sorted(by_name), 2):
        left_card, right_card = by_name[left_name], by_name[right_name]
        left_frame, right_frame = frames.get(left_name), frames.get(right_name)
        if left_frame is None or right_frame is None:
            continue

        pair_results: list[RelationshipCandidate] = []
        for left_col in left_card.columns:
            if left_col.semantic_type not in _JOINABLE or left_col.name not in left_frame:
                continue
            for right_col in right_card.columns:
                if right_col.semantic_type not in _JOINABLE or right_col.name not in right_frame:
                    continue

                # Skip pairs that cannot be a key relationship in either direction.
                if not (left_col.is_unique or right_col.is_unique):
                    continue

                if right_col.is_unique:
                    rel = measure_relationship(
                        left_name,
                        left_col.name,
                        left_frame[left_col.name],
                        right_name,
                        right_col.name,
                        right_frame[right_col.name],
                    )
                    if _is_plausible_relationship(rel, options):
                        pair_results.append(rel)

                if left_col.is_unique:
                    rel = measure_relationship(
                        right_name,
                        right_col.name,
                        right_frame[right_col.name],
                        left_name,
                        left_col.name,
                        left_frame[left_col.name],
                    )
                    if _is_plausible_relationship(rel, options):
                        pair_results.append(rel)

        pair_results.sort(key=lambda r: r.confidence, reverse=True)
        results.extend(pair_results[: options.max_candidates_per_pair])

    results = _drop_mirrored(results)
    results.sort(key=lambda r: r.confidence, reverse=True)
    return results


def _drop_mirrored(relationships: list[RelationshipCandidate]) -> list[RelationshipCandidate]:
    """Collapse 1:1 pairs reported in both directions to a single entry.

    A true one-to-one relationship measures identically each way; listing both
    doubles the review burden for the human at the gate without adding evidence.
    """
    kept: list[RelationshipCandidate] = []
    seen: set[tuple[str, ...]] = set()
    for rel in sorted(relationships, key=lambda r: r.confidence, reverse=True):
        endpoints = sorted(
            [
                f"{rel.from_table}.{'+'.join(rel.from_columns)}",
                f"{rel.to_table}.{'+'.join(rel.to_columns)}",
            ]
        )
        signature = (*endpoints, rel.cardinality.value)
        if rel.cardinality is Cardinality.ONE_TO_ONE and signature in seen:
            continue
        seen.add(signature)
        kept.append(rel)
    return kept


def relationships_digest(relationships: list[RelationshipCandidate]) -> str:
    """Render measured relationships as compact text for LLM context."""
    if not relationships:
        return "No candidate relationships found above the overlap threshold."

    lines = ["CANDIDATE RELATIONSHIPS (measured, ranked by confidence):"]
    for rel in relationships:
        lines.append(
            f"  {rel.from_table}.{'+'.join(rel.from_columns)} -> "
            f"{rel.to_table}.{'+'.join(rel.to_columns)}  "
            f"rows_matched={rel.overlap_rate:.1%} orphan_rows={rel.orphan_rate:.1%} "
            f"distinct_matched={rel.distinct_overlap_rate:.1%} "
            f"parent_coverage={rel.parent_coverage:.1%} "
            f"card={rel.cardinality.value} "
            f"({rel.n_from_distinct:,} -> {rel.n_to_distinct:,} distinct)"
        )
    return "\n".join(lines)


__all__ = [
    "KeyDetectionOptions",
    "detect_primary_keys",
    "detect_relationships",
    "measure_relationship",
    "relationships_digest",
]
