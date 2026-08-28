"""The table budget on pairwise relationship detection.

The pass compares every table with every other, so its cost grows with the
square of the table count. 700 one-kilobyte CSVs -- a sharded export, not 700
datasets anyone wants cross-referenced -- is roughly 245,000 pairs, and the
request died at Cloudflare's 100s edge timeout having said nothing (#86).

What matters here is not the number. It is that going past it *refuses* rather
than returning an empty list: "measured, found none" and "did not measure" look
identical in a list of relationships, and only one of them should let somebody
conclude their tables are unrelated.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.intake import TooManyTablesForPairwiseDetection, detect_relationships
from ads.intake.keys import KeyDetectionOptions
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_tables


def _tables(count: int) -> tuple[list, dict[str, pd.DataFrame]]:
    loaded = []
    for index in range(count):
        frame = pd.DataFrame({"id": [1, 2, 3], "value": [index, index + 1, index + 2]})
        loaded.append(
            LoadedTable(
                name=f"t{index}",
                frame=frame,
                source_uri=f"mem://t{index}",
                source_format="csv",
            )
        )
    cards = profile_tables(loaded)
    return cards, {table.name: table.frame for table in loaded}


def test_a_normal_number_of_tables_is_measured() -> None:
    cards, frames = _tables(4)
    assert isinstance(detect_relationships(cards, frames), list)


def test_going_past_the_budget_refuses_instead_of_hanging() -> None:
    cards, frames = _tables(6)
    options = KeyDetectionOptions(max_pairwise_tables=5)
    with pytest.raises(TooManyTablesForPairwiseDetection):
        detect_relationships(cards, frames, options)


def test_exactly_at_the_budget_still_runs() -> None:
    """Off-by-one here would refuse a source that was always fine."""
    cards, frames = _tables(5)
    options = KeyDetectionOptions(max_pairwise_tables=5)
    assert isinstance(detect_relationships(cards, frames, options), list)


def test_the_refusal_says_both_numbers() -> None:
    """A limit nobody can see is a limit nobody can act on."""
    cards, frames = _tables(6)
    options = KeyDetectionOptions(max_pairwise_tables=5)
    with pytest.raises(TooManyTablesForPairwiseDetection) as caught:
        detect_relationships(cards, frames, options)
    assert caught.value.table_count == 6
    assert caught.value.limit == 5
    assert "6" in str(caught.value) and "5" in str(caught.value)


def test_the_refusal_is_not_an_empty_result() -> None:
    """The distinction the whole change exists for: a caller must not be able to
    mistake a skipped pass for a measured one that found nothing."""
    cards, frames = _tables(6)
    options = KeyDetectionOptions(max_pairwise_tables=5)
    try:
        result = detect_relationships(cards, frames, options)
    except TooManyTablesForPairwiseDetection:
        return
    pytest.fail(f"returned {result!r} instead of refusing")


def test_the_default_budget_admits_an_ordinary_project() -> None:
    """A dozen tables is a normal project and must never trip this."""
    cards, frames = _tables(12)
    assert isinstance(detect_relationships(cards, frames), list)
