"""Two defects the understanding benchmark surfaced on its first real run.

Both were invisible to the existing suite, and both were invisible for the same
reason: every fixture in this repository was written by someone who already knew
the schema, so it used snake_case keys and contained no file that wasn't meant
to be parsed. Real data supplied both immediately.

**camelCase keys were not keys.** `_slugify` folded `movieId` to `movieid`,
erasing the word boundary. The identifier heuristic looks for an `id` token,
found a single blob, and classified an integer primary key as a continuous
measurement -- which excluded it from `_JOINABLE`, so `detect_relationships`
returned *nothing at all* for a four-table dataset whose joins were 100%
overlapping. camelCase is the norm in anything exported from a JS, Java or Mongo
schema, so this silently applied to a large share of real datasets.

**One unreadable file killed the whole source.** `.txt` is routed to the
delimited-text loader, so a prose README next to four clean CSVs raised out of
`load_directory` and the API turned it into a 400. Uploading a folder that
happens to contain a README made the product unusable, with no indication which
file was at fault.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ads.api.service import ControlPlane
from ads.intake import detect_relationships, load_directory_with_failures, profile_tables
from ads.intake.loaders import load_directory
from ads.intake.profiler import _looks_like_identifier_name
from ads.store.artifacts import ArtifactStore


@pytest.fixture
def camel_corpus(tmp_path: Path) -> Path:
    """Integer keys in camelCase, the shape MovieLens actually ships."""
    source = tmp_path / "data" / "camel"
    source.mkdir(parents=True)
    pd.DataFrame({"movieId": range(1, 61), "title": [f"t{i}" for i in range(1, 61)]}).to_csv(
        source / "movies.csv", index=False
    )
    pd.DataFrame(
        {
            "userId": [1, 1, 2, 2, 3] * 20,
            "movieId": list(range(1, 61)) + list(range(1, 41)),
            "rating": [4.0] * 100,
        }
    ).to_csv(source / "ratings.csv", index=False)
    return tmp_path


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("movieId", True),
        ("userId", True),
        ("movie_id", True),
        ("id", True),
        ("customerNo", True),
        # The substring form read `paid_amount` as a key because it contains
        # `id_`. Tokens do not.
        ("paid_amount", False),
        ("paidAmount", False),
        ("amount", False),
        ("code_review", False),
    ],
)
def test_identifier_names_are_matched_on_tokens_not_substrings(name: str, expected: bool) -> None:
    assert _looks_like_identifier_name(name) is expected


def test_camelcase_headers_become_snake_case(camel_corpus: Path) -> None:
    """The boundary has to survive normalisation, or every later heuristic that
    reads names as tokens is looking at a blob."""
    tables = load_directory(str(camel_corpus / "data" / "camel"))
    columns = {column for table in tables for column in table.frame.columns}
    assert "movie_id" in columns
    assert "movieid" not in columns, "folding the case erases the word boundary"


def test_an_integer_camelcase_key_is_classified_as_an_identifier(camel_corpus: Path) -> None:
    cards = profile_tables(load_directory(str(camel_corpus / "data" / "camel")))
    kinds = {
        (card.table_name, column.name): column.semantic_type.value
        for card in cards
        for column in card.columns
    }
    assert kinds[("movies", "movie_id")] == "identifier"
    assert kinds[("ratings", "movie_id")] == "identifier", (
        "a foreign key repeats heavily and is still a key"
    )


def test_relationships_are_found_between_integer_keys(camel_corpus: Path) -> None:
    """The headline failure. Before the fix this returned an empty list."""
    tables = load_directory(str(camel_corpus / "data" / "camel"))
    cards = profile_tables(tables)
    found = detect_relationships(cards, {table.name: table.frame for table in tables})

    assert found, "an integer join at 100% overlap must be measurable"
    edge = next(
        r for r in found if {r.from_table, r.to_table} == {"ratings", "movies"}
    )
    assert edge.from_columns == ("movie_id",) or list(edge.from_columns) == ["movie_id"]
    assert edge.overlap_rate == pytest.approx(1.0)


def test_an_unreadable_file_is_reported_and_the_rest_still_load(tmp_path: Path) -> None:
    """Prose in a .txt is the common case: the delimited loader is obliged to
    try, and it must be allowed to fail without taking the CSVs with it."""
    source = tmp_path / "mixed"
    source.mkdir()
    pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}).to_csv(source / "clean.csv", index=False)
    # Shaped like the README that exposed this: several lines carrying no
    # delimiter, then one with five fields. That is what makes the C parser give
    # up rather than quietly produce a one-column table of prose.
    (source / "notes.txt").write_text(
        "Summary\n=======\n\nThis dataset is described below.\n"
        "alpha, beta, gamma, delta, epsilon\n",
        encoding="utf-8",
    )

    tables, failures = load_directory_with_failures(source)

    assert [t.name for t in tables] == ["clean"], "the readable file still loads"
    assert "notes.txt" in failures
    assert failures["notes.txt"], "the reason must be kept, not discarded"


def test_the_whole_source_no_longer_fails_because_of_one_file(tmp_path: Path) -> None:
    """End to end: this raised out of `source_profile` and became a 400, so a
    folder containing a README could not be used at all."""
    source = tmp_path / "data" / "mixed"
    source.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}).to_csv(source / "clean.csv", index=False)
    (source / "readme.txt").write_text(
        "README\n\nUsage\n-----\n"
        "Cite as: Harper, F, and Konstan, J, 2015, The MovieLens Datasets, ACM\n",
        encoding="utf-8",
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profile = plane.source_profile("mixed")

    routes = {f["name"]: f["route"] for f in profile["source_files"]}
    assert routes["clean.csv"] == "structured"
    assert routes["readme.txt"] == "needs_review", "shown, not silently dropped"

    reason = next(f["reason"] for f in profile["source_files"] if f["name"] == "readme.txt")
    assert reason, "a file that needs review has to say why"
    assert any(t["name"] == "clean" for t in profile["tables"])
