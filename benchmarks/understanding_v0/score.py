"""Score what a run understood against what is measurably true of the corpus.

Reads `ground_truth.json` (produced by `fetch.py` from the files themselves) and
the relationships a run actually measured, and reports where they differ.

Three things are scored separately on purpose, because they fail for different
reasons and averaging them hides which one broke:

**Edges** -- did it find the joins, and did it invent any. Recall and precision
are reported separately: a system that proposes every column pair scores well on
recall alone, which is why precision is the number that stops it.

**Cardinality** -- of the edges it found, did it get N:1 versus 1:1 right. This
is scored only over matched edges, since being wrong about an edge you never
found is already counted as a miss. It is the sharpest signal here: `links` and
`ratings` both join `movies` on the same column with 100% overlap, and the only
thing separating them is whether the child keys are distinct. A system that
reads "the key matched" as "N:1" gets one of the two wrong every time.

**Overlap** -- are the reported rates close to the measured ones. Tolerance is
deliberately tight; these are counts, not estimates, and a system that is
approximately right about an exact quantity is wrong about it.

    python benchmarks/understanding_v0/score.py \\
        --truth data/benchmark-understanding/ground_truth.json \\
        --run relationships.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

OVERLAP_TOLERANCE = 0.01


def _key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    """Identify an edge by its endpoints, direction-insensitively.

    Direction is a modelling choice the two sides can legitimately disagree on
    -- `ratings` joins `movies` or `movies` is joined by `ratings` -- and
    scoring it as a miss would punish a correct answer for its phrasing.
    Cardinality is where direction actually matters, and that is scored below.
    """
    left = (_table(edge.get("from") or edge.get("from_table")), _cols(edge, "from"))
    right = (_table(edge.get("to") or edge.get("to_table")), _cols(edge, "to"))
    first, second = sorted([left, right])
    return (first[0], first[1], second[0], second[1])


#: Extensions stripped when comparing a table to the file it came from.
_DATA_SUFFIXES = (".csv", ".tsv", ".txt", ".parquet", ".pq", ".xlsx", ".xlsm", ".xls")


def _table(name: Any) -> str:
    """Compare `links.csv` and `links` as the same table.

    The answer key is written in terms of files, because that is what a person
    uploaded. The system reports tables, because that is what it loaded. Scoring
    the difference would fail a correct run -- which it did, reporting 0.0 while
    every join had in fact been found with the right cardinality.
    """
    text = str(name or "").strip()
    lowered = text.lower()
    for suffix in _DATA_SUFFIXES:
        if lowered.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _column(name: Any) -> str:
    """Compare `movieId` and `movie_id` as the same column.

    The loader normalises headers to snake_case, so the key it reports is not
    spelled the way the file spells it. Same split the loader uses, so the two
    stay in step.
    """
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name or ""))
    return re.sub(r"[^0-9a-zA-Z]+", "_", expanded).strip("_").lower()


def _cols(edge: dict[str, Any], side: str) -> str:
    columns = edge.get(f"{side}_columns") or []
    return ",".join(sorted(_column(c) for c in columns))


def _normalise_cardinality(value: Any) -> str:
    """Fold the spellings backends actually emit onto one vocabulary.

    `one-to-one`, `many_to_one`, `M-1` and `N:1` are the same finding written by
    different code paths. Scoring a formatting difference as a modelling error
    would make the benchmark measure string style.
    """
    text = str(value or "").strip().lower().replace("-", ":").replace("_", ":")
    text = text.replace(":to:", ":")
    if text in {"1:1", "one:one"}:
        return "1:1"
    if text in {"n:1", "many:one", "m:1"}:
        return "N:1"
    if text in {"1:n", "one:many", "1:m"}:
        return "1:N"
    if text in {"n:m", "many:many", "n:n", "m:n"}:
        return "N:M"
    return text.upper() or "unknown"


def score(truth: dict[str, Any], measured: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {_key(e): e for e in truth["relationships"]}
    false_friends = {_key(e) for e in truth.get("false_relationships", [])}
    redundant = {_key(e) for e in truth.get("redundant_relationships", [])}
    found = {_key(e): e for e in measured}

    matched = sorted(expected.keys() & found.keys())
    missed = sorted(expected.keys() - found.keys())
    extra = sorted(found.keys() - expected.keys() - redundant)
    invented = [k for k in extra if k in false_friends]
    # Reported and defensible: excluded from the precision denominator so a
    # correct answer is not marked down for saying something also true.
    redundant_found = sorted(found.keys() & redundant)

    cardinality_right, cardinality_wrong = [], []
    overlap_right, overlap_wrong = [], []
    for key in matched:
        want, got = expected[key], found[key]
        if _normalise_cardinality(want.get("cardinality")) == _normalise_cardinality(
            got.get("cardinality")
        ):
            cardinality_right.append(key)
        else:
            cardinality_wrong.append(
                {
                    "edge": key,
                    "expected": _normalise_cardinality(want.get("cardinality")),
                    "reported": _normalise_cardinality(got.get("cardinality")),
                }
            )

        want_overlap = want.get("overlap_rate")
        got_overlap = got.get("overlap_rate")
        if want_overlap is None or got_overlap is None:
            continue
        if abs(float(want_overlap) - float(got_overlap)) <= OVERLAP_TOLERANCE:
            overlap_right.append(key)
        else:
            overlap_wrong.append(
                {"edge": key, "expected": want_overlap, "reported": got_overlap}
            )

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "edges": {
            "expected": len(expected),
            "found": len(found),
            "matched": len(matched),
            "recall": ratio(len(matched), len(expected)),
            "precision": ratio(len(matched), len(found) - len(redundant_found)),
            "redundant_but_valid": [list(k) for k in redundant_found],
            "missed": [list(k) for k in missed],
            "unexpected": [list(k) for k in extra],
            # Scored separately from ordinary extras: proposing a join that the
            # corpus documents as a coincidence is a different, worse error than
            # proposing an unlisted but plausible one.
            "known_false_friends_proposed": [list(k) for k in invented],
        },
        "cardinality": {
            "scored_over": len(matched),
            "correct": len(cardinality_right),
            "accuracy": ratio(len(cardinality_right), len(matched)),
            "wrong": cardinality_wrong,
        },
        "overlap": {
            "scored_over": len(overlap_right) + len(overlap_wrong),
            "within_tolerance": len(overlap_right),
            "tolerance": OVERLAP_TOLERANCE,
            "outside_tolerance": overlap_wrong,
        },
    }


def _load_measured(path: Path) -> list[dict[str, Any]]:
    """Accept either a bare list or a staging workspace payload."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    for field in ("measured_relationships", "relationships", "relationship_explanations"):
        value = payload.get(field)
        if isinstance(value, list) and value:
            return value
    raise SystemExit(f"No relationship list found in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()

    report = score(
        json.loads(args.truth.read_text(encoding="utf-8")), _load_measured(args.run)
    )
    print(json.dumps(report, indent=2))

    edges = report["edges"]
    cardinality = report["cardinality"]
    print(
        f"\nedges  recall={edges['recall']} precision={edges['precision']}"
        f"   cardinality accuracy={cardinality['accuracy']}"
    )
    if edges["known_false_friends_proposed"]:
        print("proposed a documented false friend -- name affinity is being trusted")


if __name__ == "__main__":
    main()
