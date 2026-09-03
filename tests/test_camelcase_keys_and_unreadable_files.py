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
    edge = next(r for r in found if {r.from_table, r.to_table} == {"ratings", "movies"})
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


def test_a_prose_note_says_what_it_is_not_just_that_parsing_failed(
    tmp_path: Path,
) -> None:
    """A note beside the CSVs is context, and must read as context.

    Routing it to `needs_review` stopped it being dropped, but the only thing
    it then said was the delimited loader's own failure -- "Could not be read
    as tabular data: <pandas error>". On the e-commerce benchmark that is the
    entire account a person gets of `operations_note.txt`: a parser complaint
    about a file that was never a table, offering nothing to review and no
    reason the file is in the folder.

    Prose is measurable without reading values out of it. Saying how much
    prose there is, and that it is kept as context rather than trained on,
    is the difference between a dead end and a file a person can act on.
    """
    source = tmp_path / "data" / "shop"
    source.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}).to_csv(source / "orders.csv", index=False)
    (source / "operations_note.txt").write_text(
        "Operations note\n"
        "===============\n"
        "\n"
        "Refunds are recorded against the original order, not a new one.\n"
        "Tickets opened by the support team carry an internal prefix.\n",
        encoding="utf-8",
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profile = plane.source_profile("shop")

    note = next(item for item in profile["source_files"] if item["name"] == "operations_note.txt")
    # The route this takes is host-dependent -- whether content detection
    # speaks deterministically decides between `needs_review` and a detection-
    # supplied `documents`, and libmagic is not present everywhere. What must
    # not be host-dependent is that the file gets described.
    assert note["route"] != "structured"

    insight = note["insight"]
    for language in ("en", "tr"):
        assert insight[language], f"the note has no {language} insight"
        assert "Could not be read as tabular data" not in insight[language], (
            "a parser complaint is not an insight about the file"
        )
        assert "PDF" not in insight[language], (
            "a .txt routed to documents was described as a PDF that will be "
            "extracted; the engine handles PDFs only, so it never is"
        )
    # Measured, not guessed: the note has 4 non-empty lines (the blank one
    # between the heading and the body is not prose).
    assert "4" in insight["en"], f"line count missing from insight: {insight['en']!r}"
    assert insight["en"] != insight["tr"], "the insight must be translated"
    # It must read as context to keep, not as a failure.
    assert "context" in insight["en"].lower()
    assert note["schema_role"]["en"]


def test_a_note_quarantined_by_content_detection_is_described_too(tmp_path: Path) -> None:
    """The other road to `needs_review`, and the one real data takes.

    A file lands in `needs_review` either because the delimited loader raised
    on it, or because measured content disagreed with the extension. The
    e-commerce benchmark's `operations_note.txt` takes the second: it parses
    as a single column, and detection measures it as a document. Only the
    first road was described, so the benchmark file -- the one a person
    actually looks at -- still showed a detection complaint and nothing about
    itself.
    """
    source = tmp_path / "data" / "shop"
    source.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}).to_csv(source / "orders.csv", index=False)
    (source / "operations_note.txt").write_text(
        "OPERATIONS NOTE - Q4\n"
        "Refunds are recorded against the original order.\n"
        "Support tickets use an internal prefix.\n",
        encoding="utf-8",
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profile = plane.source_profile("shop")

    note = next(item for item in profile["source_files"] if item["name"] == "operations_note.txt")
    assert note["route"] != "structured"
    # Whichever road it took, it must describe the file.
    assert "prose" in note["insight"]["en"]
    assert "3" in note["insight"]["en"]
    # The detection finding is not lost -- it stays as the reason.
    assert note["reason"]["en"] and note["reason"]["tr"]
