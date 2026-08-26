"""Fetch the real corpus this benchmark measures against, and record what is in it.

`assurance_v0` asks whether the gate makes defensible decisions. This asks
something earlier: does the system understand what it was handed -- which files
are what, how they join, at what cardinality, and which parts of a document are
trustworthy.

Everything here is real. Nothing is generated. That constraint is the point:
synthetic tables are joined the way whoever wrote the generator imagined them,
so a system can score well on structure that was never in doubt. Real data
argues back. MovieLens contains, without anyone arranging it:

* a **1:1** relationship (`links` to `movies`) sitting next to a **N:1** one
  (`ratings` to `movies`), so cardinality has to be measured rather than
  assumed from the fact that a key matched;
* an **entity with no table**. `userId` appears in `ratings` and in `tags` and
  has no parent anywhere. A schema discovery that only looks for
  child-to-parent edges will miss that the two tables share a key space;
* 8 missing `tmdbId` values, a pipe-delimited multi-value `genres` column, a
  release year buried inside `title`, and epoch integers for timestamps -- all
  things a profiler should describe rather than silently flatten.

The document half is a real arXiv paper that analyses this exact dataset, so
cross-source synthesis has something genuinely checkable to do: its tables and
figures describe the same rows sitting in the CSVs. It is 25 pages with a real
text layer, which also exercises the extraction adapters on something other than
a one-page fixture.

`README.txt` ships with MovieLens and is prose, not a table. The architecture
report notes that TXT is currently routed as delimited text, so it is expected
to be misrouted today; it is here so that stops being invisible.

The ground truth is **measured from the downloaded files**, never hand-written.
An answer key someone typed is an opinion; one computed from the data is a fact,
and it stays correct if the upstream file ever changes.

    python benchmarks/understanding_v0/fetch.py --out data/benchmark-understanding
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

MOVIELENS_URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
PAPER_URL = "https://arxiv.org/pdf/2307.09985v3"
PAPER_NAME = "movielens_analysis.pdf"

#: Licences matter here: this corpus is redistributed to nobody, it is fetched.
#: MovieLens is free for research use under GroupLens' terms and must not be
#: redistributed, which is exactly why this script downloads rather than vendors.
CSV_FILES = ("movies.csv", "ratings.csv", "tags.csv", "links.csv")


def fetch(out: Path, *, timeout: float = 300.0) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen(MOVIELENS_URL, timeout=timeout) as response:  # noqa: S310
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    for member in archive.namelist():
        name = Path(member).name
        if name in CSV_FILES or name == "README.txt":
            (out / name).write_bytes(archive.read(member))

    with urllib.request.urlopen(PAPER_URL, timeout=timeout) as response:  # noqa: S310
        (out / PAPER_NAME).write_bytes(response.read())

    truth = measure(out)
    (out / "ground_truth.json").write_text(
        json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return truth


def measure(corpus: Path) -> dict[str, Any]:
    """Compute the answer key from the files themselves."""
    frames = {name: pd.read_csv(corpus / name) for name in CSV_FILES}
    movies, ratings, tags, links = (frames[n] for n in CSV_FILES)
    movie_ids = set(movies["movieId"])

    def edge(child: str, column: str, cardinality: str) -> dict[str, Any]:
        series = frames[child][column]
        return {
            "from": child,
            "from_columns": [column],
            "to": "movies.csv",
            "to_columns": ["movieId"],
            "cardinality": cardinality,
            "overlap_rate": round(float(series.isin(movie_ids).mean()), 4),
            "distinct_child_keys": int(series.nunique()),
            "parent_rows": len(movies),
        }

    rating_users = set(ratings["userId"])
    tag_users = set(tags["userId"])

    return {
        "corpus": "movielens ml-latest-small + arXiv 2307.09985v3",
        "generated": False,
        "files": {
            **{name: {"rows": len(frames[name]), "route": "structured"} for name in CSV_FILES},
            PAPER_NAME: {"route": "documents"},
            "README.txt": {
                "route": "documents",
                # Recorded as the intended answer, not the current one.
                "currently_misrouted_as_structured": True,
                "why": "prose describing the dataset; it is not delimited data",
            },
        },
        "primary_keys": {
            "movies.csv": ["movieId"] if movies["movieId"].is_unique else [],
            "links.csv": ["movieId"] if links["movieId"].is_unique else [],
            "ratings.csv": ["userId", "movieId"],
            "tags.csv": [],
        },
        "relationships": [
            edge("ratings.csv", "movieId", "N:1"),
            edge("tags.csv", "movieId", "N:1"),
            # 1:1 rather than N:1, and the only way to know is to measure that
            # every child key is distinct.
            edge("links.csv", "movieId", "1:1"),
        ],
        "implicit_entities": [
            {
                "key": "userId",
                "appears_in": ["ratings.csv", "tags.csv"],
                "has_parent_table": False,
                "distinct_in_ratings": len(rating_users),
                "distinct_in_tags": len(tag_users),
                "tags_users_subset_of_rating_users": tag_users <= rating_users,
                "why": (
                    "a real entity with no table of its own; the two tables share "
                    "one key space and a discovery that only looks for parents misses it"
                ),
            }
        ],
        "quality_issues": [
            {
                "table": "links.csv",
                "column": "tmdbId",
                "issue": "missing values",
                "missing_rows": int(links["tmdbId"].isna().sum()),
            },
            {
                "table": "movies.csv",
                "column": "genres",
                "issue": "pipe-delimited multi-value column, not categorical",
            },
            {
                "table": "movies.csv",
                "column": "title",
                "issue": "release year embedded in free text, e.g. 'Toy Story (1995)'",
            },
            {
                "table": "ratings.csv",
                "column": "timestamp",
                "issue": "epoch seconds as an integer, not a datetime",
            },
        ],
        "candidate_targets": [
            {
                "table": "ratings.csv",
                "column": "rating",
                "task": "regression",
                "why": "the dataset's natural prediction target",
            }
        ],
        "document_facts": {
            PAPER_NAME: {
                "about": "an analysis of the MovieLens datasets themselves",
                "relates_to": "the same rows present in the CSV files",
                "has_text_layer": True,
                "note": (
                    "figures in this paper are chart-only evidence. Values read "
                    "from a chart are not measured rows and must not enter training data."
                ),
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="Recompute ground_truth.json from files already present.",
    )
    args = parser.parse_args()

    if args.measure_only:
        truth = measure(args.out)
        (args.out / "ground_truth.json").write_text(
            json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        truth = fetch(args.out)

    print(f"corpus at {args.out}")
    for name, meta in truth["files"].items():
        rows = meta.get("rows")
        print(f"  {name:26} route={meta['route']}" + (f" rows={rows}" if rows else ""))
    print(f"  {len(truth['relationships'])} measured relationships")


if __name__ == "__main__":
    main()
