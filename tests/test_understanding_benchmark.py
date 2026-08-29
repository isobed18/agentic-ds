"""The understanding benchmark's scorer, and the synthetic corpus behind its traps.

A benchmark that cannot fail a bad run measures nothing, so most of this file is
about making the scorer reject answers it should reject. The cardinality case is
the one that matters: `links` and `ratings` both join `movies` on the same
column with 100% overlap, and only distinctness of the child keys separates
1:1 from N:1. A scorer that waves that through would rate a system that never
measured cardinality as perfect.

These tests never touch the network. `fetch.py` downloads the real corpus;
`measure()` is exercised against a small frame written here, because asserting
against live MovieLens would make the suite fail whenever GroupLens is down.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks" / "understanding_v0"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"bench_{name}", BENCHMARK / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score_module = _load("score")
fetch_module = _load("fetch")


def _truth(relationships: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"relationships": relationships, **extra}


EDGE_RATINGS = {
    "from": "ratings.csv",
    "from_columns": ["movieId"],
    "to": "movies.csv",
    "to_columns": ["movieId"],
    "cardinality": "N:1",
    "overlap_rate": 1.0,
}
EDGE_LINKS = {
    "from": "links.csv",
    "from_columns": ["movieId"],
    "to": "movies.csv",
    "to_columns": ["movieId"],
    "cardinality": "1:1",
    "overlap_rate": 1.0,
}
EDGE_TAGS = {
    "from": "tags.csv",
    "from_columns": ["movieId"],
    "to": "movies.csv",
    "to_columns": ["movieId"],
    "cardinality": "N:1",
    "overlap_rate": 1.0,
}

WHOLE_TRUTH = _truth(
    [EDGE_RATINGS, EDGE_LINKS],
    files={
        "movies.csv": {"route": "structured"},
        "README.txt": {"route": "documents", "currently_misrouted_as_structured": True},
    },
    primary_keys={
        "movies.csv": ["movieId"],
        "ratings.csv": ["userId", "movieId"],
        "tags.csv": [],
    },
    implicit_entities=[
        {
            "key": "userId",
            "appears_in": ["ratings.csv", "tags.csv"],
            "has_parent_table": False,
        }
    ],
    quality_issues=[
        {
            "table": "links.csv",
            "column": "tmdbId",
            "issue": "missing values",
            "missing_rows": 8,
        },
        {
            "table": "movies.csv",
            "column": "genres",
            "issue": "pipe-delimited multi-value column, not categorical",
        },
    ],
)

WHOLE_RUN = {
    "relationships": [dict(EDGE_RATINGS), dict(EDGE_LINKS)],
    "files": {
        "movies.csv": {"route": "structured"},
        "README.txt": {"route": "documents"},
    },
    "primary_keys": {
        "movies": ["movie_id"],
        "ratings": ["user_id", "movie_id"],
        "tags": [],
    },
    "implicit_entities": [
        {
            "key": "user_id",
            "appears_in": ["ratings", "tags"],
            "has_parent_table": False,
        }
    ],
    "quality_issues": [
        {
            "table": "links",
            "column": "tmdb_id",
            "issue": "missing_values",
            "missing_rows": 8,
        },
        {
            "table": "movies",
            "column": "genres",
            "issue": "multi_value_delimited",
        },
    ],
}


def test_a_whole_understanding_answer_reports_each_category_score() -> None:
    report = score_module.score(WHOLE_TRUTH, WHOLE_RUN)

    assert report["routing"]["accuracy"] == 1.0
    assert report["primary_keys"]["accuracy"] == 1.0
    assert report["implicit_entities"]["recall"] == 1.0
    assert report["implicit_entities"]["parent_table_accuracy"] == 1.0
    assert report["quality"]["recall"] == 1.0
    assert report["quality"]["precision"] == 1.0
    assert report["quality"]["detail_accuracy"] == 1.0


def test_a_stage_response_scores_routes_and_candidate_keys_from_its_profile() -> None:
    truth = _truth(
        [],
        files={"movies.csv": {"route": "structured"}},
        primary_keys={"movies.csv": ["movieId"], "tags.csv": []},
    )
    stage_response = {
        "run_id": "run-1",
        "profile": {
            "source_files": [{"name": "movies.csv", "route": "structured"}],
            "tables": [
                {"name": "movies", "candidate_keys": [["movie_id"]]},
                {"name": "tags", "candidate_keys": []},
            ],
            "relationships": [],
        },
    }

    report = score_module.score(truth, stage_response)

    assert report["routing"]["accuracy"] == 1.0
    assert report["primary_keys"]["accuracy"] == 1.0


def test_wrong_routes_move_the_routing_score() -> None:
    bad = {**WHOLE_RUN, "files": {**WHOLE_RUN["files"], "README.txt": {"route": "structured"}}}

    routing = score_module.score(WHOLE_TRUTH, bad)["routing"]

    assert routing["accuracy"] == 0.5
    assert routing["wrong"][0]["file"] == "readme.txt"
    assert routing["wrong"][0]["known_current_failure"] is True


def test_wrong_composite_and_invented_absent_keys_move_the_key_score() -> None:
    bad = {
        **WHOLE_RUN,
        "primary_keys": {
            "movies": ["movie_id"],
            "ratings": ["movie_id"],
            "tags": ["tag"],
        },
    }

    keys = score_module.score(WHOLE_TRUTH, bad)["primary_keys"]

    assert keys["accuracy"] == pytest.approx(1 / 3, abs=0.0001)
    assert keys["absence_accuracy"] == 0.0
    assert {item["table"] for item in keys["wrong"]} == {"ratings", "tags"}


def test_missing_an_entity_without_a_parent_moves_the_entity_score() -> None:
    bad = {**WHOLE_RUN, "implicit_entities": []}

    entities = score_module.score(WHOLE_TRUTH, bad)["implicit_entities"]

    assert entities["recall"] == 0.0
    assert entities["missed"]


def test_inventing_a_parent_table_moves_the_entity_attribute_score() -> None:
    bad_entity = {**WHOLE_RUN["implicit_entities"][0], "has_parent_table": True}
    bad = {**WHOLE_RUN, "implicit_entities": [bad_entity]}

    entities = score_module.score(WHOLE_TRUTH, bad)["implicit_entities"]

    assert entities["recall"] == 1.0, "the shared user entity itself was noticed"
    assert entities["parent_table_accuracy"] == 0.0
    assert entities["wrong_parent_table"]


def test_missed_and_invented_quality_findings_move_both_quality_scores() -> None:
    bad = {
        **WHOLE_RUN,
        "quality_issues": [
            WHOLE_RUN["quality_issues"][0],
            {"table": "ratings", "column": "rating", "issue": "missing_values"},
        ],
    }

    quality = score_module.score(WHOLE_TRUTH, bad)["quality"]

    assert quality["recall"] == 0.5
    assert quality["precision"] == 0.5
    assert quality["missed"] and quality["unexpected"]


def test_a_wrong_measured_quality_count_moves_detail_accuracy() -> None:
    wrong_count = {**WHOLE_RUN["quality_issues"][0], "missing_rows": 7}
    bad = {**WHOLE_RUN, "quality_issues": [wrong_count, WHOLE_RUN["quality_issues"][1]]}

    quality = score_module.score(WHOLE_TRUTH, bad)["quality"]

    assert quality["recall"] == 1.0, "the finding identity is still right"
    assert quality["detail_accuracy"] == 0.0
    assert quality["wrong_details"][0]["expected"] == 8


def test_a_perfect_answer_scores_perfectly() -> None:
    truth = _truth([EDGE_RATINGS, EDGE_LINKS])
    report = score_module.score(truth, [dict(EDGE_RATINGS), dict(EDGE_LINKS)])
    assert report["edges"]["recall"] == 1.0
    assert report["edges"]["precision"] == 1.0
    assert report["cardinality"]["accuracy"] == 1.0


def test_the_recorded_relationship_baseline_remains_exact() -> None:
    redundant = [
        {
            "from": child,
            "from_columns": ["movieId"],
            "to": "links.csv",
            "to_columns": ["movieId"],
        }
        for child in ("ratings.csv", "tags.csv")
    ]
    truth = _truth(
        [EDGE_RATINGS, EDGE_TAGS, EDGE_LINKS],
        redundant_relationships=redundant,
    )
    measured = [dict(EDGE_RATINGS), dict(EDGE_TAGS), dict(EDGE_LINKS), *redundant]

    report = score_module.score(truth, measured)

    assert report["edges"]["recall"] == 1.0
    assert report["edges"]["precision"] == 1.0
    assert report["cardinality"]["accuracy"] == 1.0
    assert report["overlap"]["within_tolerance"] == 3
    assert report["overlap"]["scored_over"] == 3


def test_confusing_one_to_one_with_many_to_one_is_caught() -> None:
    """The sharpest signal in the benchmark. Both edges have 100% overlap; only
    child-key distinctness tells them apart, so a system that never measured it
    must not score as though it had."""
    truth = _truth([EDGE_RATINGS, EDGE_LINKS])
    sloppy = [dict(EDGE_RATINGS), {**EDGE_LINKS, "cardinality": "N:1"}]

    report = score_module.score(truth, sloppy)
    assert report["edges"]["recall"] == 1.0, "the edge itself was found"
    assert report["cardinality"]["accuracy"] == 0.5
    wrong = report["cardinality"]["wrong"][0]
    assert wrong["expected"] == "1:1" and wrong["reported"] == "N:1"


def test_inventing_edges_costs_precision_not_recall() -> None:
    """Recall alone rewards proposing every column pair."""
    truth = _truth([EDGE_RATINGS])
    noisy = [
        dict(EDGE_RATINGS),
        {
            "from": "tags.csv",
            "from_columns": ["userId"],
            "to": "movies.csv",
            "to_columns": ["movieId"],
            "cardinality": "N:1",
        },
    ]
    report = score_module.score(truth, noisy)
    assert report["edges"]["recall"] == 1.0
    assert report["edges"]["precision"] == 0.5


def test_a_documented_false_friend_is_reported_separately() -> None:
    """Proposing a coincidence the corpus documents as one is a worse error than
    proposing an unlisted but plausible join, so it is not buried in `unexpected`."""
    false_friend = {
        "from": "support_tickets.csv",
        "from_columns": ["agent_id"],
        "to": "products.csv",
        "to_columns": ["sku"],
    }
    truth = _truth([EDGE_RATINGS], false_relationships=[false_friend])
    report = score_module.score(truth, [dict(EDGE_RATINGS), dict(false_friend)])
    assert report["edges"]["known_false_friends_proposed"], "must be named, not just counted"


def test_direction_is_not_scored_as_a_miss() -> None:
    """`ratings joins movies` and `movies is joined by ratings` are the same
    finding phrased two ways. Cardinality is where direction actually matters."""
    truth = _truth([EDGE_RATINGS])
    flipped = [
        {
            "from": "movies.csv",
            "from_columns": ["movieId"],
            "to": "ratings.csv",
            "to_columns": ["movieId"],
            "cardinality": "N:1",
        }
    ]
    assert score_module.score(truth, flipped)["edges"]["recall"] == 1.0


def test_an_approximate_overlap_is_not_close_enough() -> None:
    """Overlap is a count, not an estimate."""
    truth = _truth([EDGE_RATINGS])
    report = score_module.score(truth, [{**EDGE_RATINGS, "overlap_rate": 0.91}])
    assert report["overlap"]["within_tolerance"] == 0
    assert report["overlap"]["outside_tolerance"][0]["reported"] == 0.91


def test_missing_every_edge_scores_zero_rather_than_erroring() -> None:
    report = score_module.score(_truth([EDGE_RATINGS, EDGE_LINKS]), [])
    assert report["edges"]["recall"] == 0.0
    assert report["edges"]["precision"] is None, "precision over nothing is undefined, not 1.0"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1:1", "1:1"), ("one-to-one", "1:1"), ("N-1", "N:1"), ("many_to_one", "N:1")],
)
def test_cardinality_spellings_are_normalised(raw: str, expected: str) -> None:
    """Backends spell these differently; a formatting difference is not a defect."""
    assert score_module._normalise_cardinality(raw) == expected


def test_ground_truth_is_measured_from_the_files(tmp_path: Path) -> None:
    """The answer key must be computed, never typed. This writes a corpus whose
    correct answer is known by construction and checks `measure()` recovers it."""
    pd.DataFrame(
        {"movieId": [1, 2, 3], "title": ["a", "b", "c"], "genres": ["x|y", "x", "z"]}
    ).to_csv(tmp_path / "movies.csv", index=False)
    pd.DataFrame(
        {
            "userId": [1, 1, 2],
            "movieId": [1, 2, 2],
            "rating": [4.0, 3.5, 5.0],
            "timestamp": [1, 2, 3],
        }
    ).to_csv(tmp_path / "ratings.csv", index=False)
    pd.DataFrame({"userId": [1], "movieId": [1], "tag": ["t"], "timestamp": [1]}).to_csv(
        tmp_path / "tags.csv", index=False
    )
    pd.DataFrame({"movieId": [1, 2, 3], "imdbId": [10, 20, 30], "tmdbId": [1.0, None, 3.0]}).to_csv(
        tmp_path / "links.csv", index=False
    )

    truth = fetch_module.measure(tmp_path)

    assert truth["generated"] is False
    assert truth["primary_keys"]["movies.csv"] == ["movieId"]
    links_edge = next(e for e in truth["relationships"] if e["from"] == "links.csv")
    assert links_edge["cardinality"] == "1:1"
    assert links_edge["overlap_rate"] == 1.0

    implicit = truth["implicit_entities"][0]
    assert implicit["key"] == "userId" and implicit["has_parent_table"] is False
    assert implicit["tags_users_subset_of_rating_users"] is True

    tmdb = next(q for q in truth["quality_issues"] if q["column"] == "tmdbId")
    assert tmdb["missing_rows"] == 1, "counted from the frame, not asserted from memory"


def test_readme_txt_is_recorded_as_a_known_current_failure(tmp_path: Path) -> None:
    """The TXT routing gap must stay visible in the answer key rather than being
    absorbed into the expected result, or fixing it would look like a regression.

    Asserted against what `measure()` actually emits -- an earlier version of
    this test built the dict it then checked, which proved nothing.
    """
    for name, frame in {
        "movies.csv": pd.DataFrame({"movieId": [1], "title": ["a"], "genres": ["x"]}),
        "ratings.csv": pd.DataFrame(
            {"userId": [1], "movieId": [1], "rating": [4.0], "timestamp": [1]}
        ),
        "tags.csv": pd.DataFrame({"userId": [1], "movieId": [1], "tag": ["t"], "timestamp": [1]}),
        "links.csv": pd.DataFrame({"movieId": [1], "imdbId": [1], "tmdbId": [1.0]}),
    }.items():
        frame.to_csv(tmp_path / name, index=False)

    files = fetch_module.measure(tmp_path)["files"]

    assert files["README.txt"]["route"] == "documents", "prose belongs on the document path"
    assert files["README.txt"]["currently_misrouted_as_structured"] is True
    assert "why" in files["README.txt"], "a known-failing entry has to say why"
    assert files["movielens_analysis.pdf"]["route"] == "documents"


# ------------------------------------------------------- synthetic traps

def test_the_synthetic_corpus_carries_traps_real_data_does_not(tmp_path: Path) -> None:
    """Real data is the benchmark; this fixture covers what it cannot.

    MovieLens has no injected target leak, no national id numbers and no orphan
    rows, so those paths would otherwise go unexercised. Generating them is fine
    *here* -- the assertion is about the detector, not about the system's score,
    and nobody is being told a generated join proves capability.
    """
    synthetic = _load_fixture()
    truth = synthetic.build(tmp_path)

    leak = truth["leaking_columns"][0]
    assert (leak["table"], leak["column"]) == ("orders.csv", "refund_issued")

    orders = pd.read_csv(tmp_path / "orders.csv")
    customers = pd.read_csv(tmp_path / "customers.csv")
    churn = dict(zip(customers["customer_id"], customers["churned"], strict=True))
    merged = orders.assign(churn=orders["customer_id"].map(churn)).dropna(subset=["churn"])
    agreement = (merged["refund_issued"] == merged["churn"]).mean()
    assert 0.75 < agreement < 1.0, (
        "the leak must be strong enough to matter and short of a perfect copy, "
        "which would be trivially caught and prove less"
    )

    orphan_edge = next(e for e in truth["relationships"] if e["from"] == "orders.csv")
    assert orphan_edge["orphan_rows"] > 0, "orphans are the point of this table"
    assert orphan_edge["overlap_rate"] < 1.0

    ids = pd.read_csv(tmp_path / "customers.csv")["national_id"].astype(str)
    assert ids.map(_valid_turkish_id).all(), (
        "checksum-valid ids distinguish a real detector from one matching column names"
    )


def _load_fixture():
    spec = importlib.util.spec_from_file_location(
        "synthetic_corpus", ROOT / "tests" / "fixtures" / "synthetic_corpus.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_turkish_id(value: str) -> bool:
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    digits = [int(c) for c in value]
    odd, even = sum(digits[0:9:2]), sum(digits[1:8:2])
    return digits[9] == (odd * 7 - even) % 10 and digits[10] == sum(digits[:10]) % 10


# ------------------------------- vocabulary the two sides do not share

def test_a_file_and_the_table_loaded_from_it_are_the_same_thing() -> None:
    """The answer key names files, because that is what a person uploaded. The
    system names tables, because that is what it loaded.

    Scoring that difference failed a run that was entirely correct: every join
    found, cardinality right, and the report said recall 0.0.
    """
    truth = _truth([EDGE_RATINGS])
    as_reported = [
        {
            "from_table": "ratings",
            "from_columns": ["movie_id"],
            "to_table": "movies",
            "to_columns": ["movie_id"],
            "cardinality": "N:1",
            "overlap_rate": 1.0,
        }
    ]
    report = score_module.score(truth, as_reported)
    assert report["edges"]["recall"] == 1.0
    assert report["cardinality"]["accuracy"] == 1.0


def test_camelcase_and_snake_case_columns_match() -> None:
    """The loader normalises headers, so a key is not spelled the way its file
    spells it. Same split the loader uses, so the two stay in step."""
    assert score_module._column("movieId") == "movie_id"
    assert score_module._column("movie_id") == "movie_id"
    assert score_module._table("links.csv") == "links"
    assert score_module._table("links") == "links"


def test_a_true_but_redundant_join_does_not_cost_precision() -> None:
    """`links` is 1:1 with `movies`, so anything joining `movies` on `movieId`
    also joins `links` on it. Reporting that is a defensible modelling choice,
    not a false positive, and marking it down would push a system toward saying
    less than it measured."""
    redundant = {
        "from": "ratings.csv",
        "from_columns": ["movieId"],
        "to": "links.csv",
        "to_columns": ["movieId"],
    }
    truth = _truth([EDGE_RATINGS], redundant_relationships=[redundant])

    report = score_module.score(truth, [dict(EDGE_RATINGS), dict(redundant)])
    assert report["edges"]["precision"] == 1.0
    assert report["edges"]["unexpected"] == []
    assert report["edges"]["redundant_but_valid"], "reported, so it stays visible"


def test_a_redundant_join_is_still_not_required() -> None:
    """Not finding it is not a miss either -- recall counts the true edges only."""
    redundant = {
        "from": "ratings.csv",
        "from_columns": ["movieId"],
        "to": "links.csv",
        "to_columns": ["movieId"],
    }
    truth = _truth([EDGE_RATINGS], redundant_relationships=[redundant])
    report = score_module.score(truth, [dict(EDGE_RATINGS)])
    assert report["edges"]["recall"] == 1.0
    assert report["edges"]["redundant_but_valid"] == []
