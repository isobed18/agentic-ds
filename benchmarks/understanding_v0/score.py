"""Score what a run understood against what is measurably true of the corpus.

Reads `ground_truth.json` (produced by `fetch.py` from the files themselves) and
the structured understanding a run actually reported, then scores routing,
primary keys, implicit entities, quality findings, and relationships separately.

The categories are never averaged: they fail for different reasons, and an
overall number would let a join improvement conceal a routing regression.

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

Routing is exact per file. Primary keys are scored per table as well as by
key-fact recall/precision, so an absent key is not treated as an unscored blank.
Implicit entities score both identity and whether a parent table was claimed.
Quality findings score identity plus quantitative details that the key measured.

    python benchmarks/understanding_v0/score.py \\
        --truth data/benchmark-understanding/ground_truth.json \\
        --run understanding.json
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


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _measured_payload(measured: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    return {"relationships": measured} if isinstance(measured, list) else measured


def _section(payload: dict[str, Any], name: str, default: Any) -> Any:
    """Read a benchmark section directly or from a stage response's profile."""
    if name in payload:
        return payload[name]
    profile = payload.get("profile")
    if isinstance(profile, dict) and name in profile:
        return profile[name]
    return default


def _file(name: Any) -> str:
    return str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _normalise_route(value: Any) -> str:
    text = _column(value)
    aliases = {
        "table": "structured",
        "tabular": "structured",
        "document": "documents",
        "needs_review": "needs_review",
    }
    return aliases.get(text, text)


def _reported_routes(payload: dict[str, Any]) -> dict[str, str]:
    files = _section(payload, "files", None)
    if isinstance(files, dict):
        return {
            _file(name): _normalise_route(meta.get("route") if isinstance(meta, dict) else meta)
            for name, meta in files.items()
        }
    source_files = _section(payload, "source_files", [])
    if isinstance(source_files, list):
        return {
            _file(item.get("name") or item.get("file")): _normalise_route(item.get("route"))
            for item in source_files
            if isinstance(item, dict) and (item.get("name") or item.get("file"))
        }
    return {}


def _score_routing(truth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    truth_files = truth.get("files", {})
    expected = {
        _file(name): _normalise_route(meta.get("route") if isinstance(meta, dict) else meta)
        for name, meta in truth_files.items()
    }
    found = _reported_routes(payload)
    correct = sorted(name for name, route in expected.items() if found.get(name) == route)
    missing = sorted(name for name in expected if name not in found)
    wrong = []
    for name in sorted(expected.keys() & found.keys()):
        if expected[name] == found[name]:
            continue
        meta = truth_files.get(name) or truth_files.get(
            next((raw for raw in truth_files if _file(raw) == name), ""), {}
        )
        wrong.append(
            {
                "file": name,
                "expected": expected[name],
                "reported": found[name],
                "known_current_failure": bool(
                    isinstance(meta, dict) and meta.get("currently_misrouted_as_structured")
                ),
            }
        )
    return {
        "expected": len(expected),
        "reported": len(found),
        "correct": len(correct),
        "accuracy": _ratio(len(correct), len(expected)),
        "missing": missing,
        "wrong": wrong,
        "unexpected": sorted(found.keys() - expected.keys()),
    }


def _columns(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted(_column(column) for column in value if str(column).strip()))


def _key_candidates(value: Any) -> set[tuple[str, ...]]:
    if not isinstance(value, list) or not value:
        return set()
    if all(not isinstance(item, (list, tuple)) for item in value):
        columns = _columns(value)
        return {columns} if columns else set()
    candidates = {_columns(item) for item in value if isinstance(item, (list, tuple))}
    return {candidate for candidate in candidates if candidate}


def _reported_keys(payload: dict[str, Any]) -> dict[str, set[tuple[str, ...]]]:
    primary = _section(payload, "primary_keys", None)
    if isinstance(primary, dict):
        return {_table(table): _key_candidates(value) for table, value in primary.items()}
    tables = _section(payload, "tables", [])
    if not isinstance(tables, list):
        return {}
    reported: dict[str, set[tuple[str, ...]]] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        name = table.get("name") or table.get("table_name") or table.get("table")
        if not name:
            continue
        candidates = table.get("candidate_keys")
        if candidates is None:
            candidates = table.get("candidate_primary_keys", [])
        reported[_table(name)] = _key_candidates(candidates)
    return reported


def _display_key_fact(fact: tuple[str, tuple[str, ...]]) -> dict[str, Any]:
    return {"table": fact[0], "columns": list(fact[1])}


def _score_primary_keys(truth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    expected = {
        _table(table): ({columns} if (columns := _columns(value)) else set())
        for table, value in truth.get("primary_keys", {}).items()
    }
    found = _reported_keys(payload)
    expected_facts = {(table, columns) for table, values in expected.items() for columns in values}
    found_facts = {(table, columns) for table, values in found.items() for columns in values}
    matched = expected_facts & found_facts
    correct_tables = [
        table for table, values in expected.items() if table in found and found[table] == values
    ]
    absent_tables = [table for table, values in expected.items() if not values]
    absent_right = [table for table in absent_tables if table in found and not found[table]]
    wrong = [
        {
            "table": table,
            "expected": [list(value) for value in sorted(values)],
            "reported": [list(value) for value in sorted(found.get(table, set()))],
        }
        for table, values in sorted(expected.items())
        if table not in found or found[table] != values
    ]
    return {
        "tables_scored": len(expected),
        "tables_correct": len(correct_tables),
        "accuracy": _ratio(len(correct_tables), len(expected)),
        "expected_keys": len(expected_facts),
        "reported_keys": len(found_facts),
        "matched_keys": len(matched),
        "recall": _ratio(len(matched), len(expected_facts)),
        "precision": _ratio(len(matched), len(found_facts)),
        "absence_scored": len(absent_tables),
        "absence_correct": len(absent_right),
        "absence_accuracy": _ratio(len(absent_right), len(absent_tables)),
        "wrong": wrong,
        "missed": [_display_key_fact(fact) for fact in sorted(expected_facts - found_facts)],
        "unexpected": [_display_key_fact(fact) for fact in sorted(found_facts - expected_facts)],
    }


def _entity_key(entity: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    tables = entity.get("appears_in") or entity.get("tables") or []
    return (
        _column(entity.get("key") or entity.get("column")),
        tuple(sorted(_table(table) for table in tables)),
    )


def _display_entity(key: tuple[str, tuple[str, ...]]) -> dict[str, Any]:
    return {"key": key[0], "appears_in": list(key[1])}


def _score_entities(truth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    expected = {
        _entity_key(entity): entity
        for entity in truth.get("implicit_entities", [])
        if isinstance(entity, dict)
    }
    reported_entities = _section(payload, "implicit_entities", [])
    if not isinstance(reported_entities, list):
        reported_entities = []
    found = {
        _entity_key(entity): entity for entity in reported_entities if isinstance(entity, dict)
    }
    matched = expected.keys() & found.keys()
    parent_right = []
    parent_wrong = []
    for key in sorted(matched):
        want = expected[key].get("has_parent_table")
        got = found[key].get("has_parent_table")
        if want == got:
            parent_right.append(key)
        else:
            parent_wrong.append(
                {"entity": _display_entity(key), "expected": want, "reported": got}
            )
    return {
        "expected": len(expected),
        "reported": len(found),
        "matched": len(matched),
        "recall": _ratio(len(matched), len(expected)),
        "precision": _ratio(len(matched), len(found)),
        "parent_table_accuracy": _ratio(len(parent_right), len(matched)),
        "wrong_parent_table": parent_wrong,
        "missed": [_display_entity(key) for key in sorted(expected.keys() - found.keys())],
        "unexpected": [_display_entity(key) for key in sorted(found.keys() - expected.keys())],
    }


def _normalise_quality_issue(value: Any) -> str:
    text = _column(value)
    if "missing" in text or "null" in text:
        return "missing_values"
    if ("pipe" in text or "delimit" in text) and ("multi" in text or "categor" in text):
        return "multi_value_delimited"
    if "year" in text and ("embed" in text or "free_text" in text):
        return "embedded_year"
    if "epoch" in text:
        return "epoch_datetime"
    return text


def _quality_key(issue: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _table(issue.get("table") or issue.get("table_name")),
        _column(issue.get("column") or issue.get("column_name")),
        _normalise_quality_issue(issue.get("issue") or issue.get("code")),
    )


def _display_quality(key: tuple[str, str, str]) -> dict[str, str]:
    return {"table": key[0], "column": key[1], "issue": key[2]}


def _score_quality(truth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    expected = {
        _quality_key(issue): issue
        for issue in truth.get("quality_issues", [])
        if isinstance(issue, dict)
    }
    reported_issues = _section(payload, "quality_issues", [])
    if not isinstance(reported_issues, list):
        reported_issues = []
    found = {_quality_key(issue): issue for issue in reported_issues if isinstance(issue, dict)}
    matched = expected.keys() & found.keys()
    detail_right = 0
    detail_wrong = []
    detail_total = 0
    for key in sorted(matched):
        for field in ("missing_rows",):
            if field not in expected[key]:
                continue
            detail_total += 1
            if expected[key][field] == found[key].get(field):
                detail_right += 1
            else:
                detail_wrong.append(
                    {
                        "finding": _display_quality(key),
                        "field": field,
                        "expected": expected[key][field],
                        "reported": found[key].get(field),
                    }
                )
    return {
        "expected": len(expected),
        "reported": len(found),
        "matched": len(matched),
        "recall": _ratio(len(matched), len(expected)),
        "precision": _ratio(len(matched), len(found)),
        "detail_accuracy": _ratio(detail_right, detail_total),
        "detail_checks": detail_total,
        "wrong_details": detail_wrong,
        "missed": [_display_quality(key) for key in sorted(expected.keys() - found.keys())],
        "unexpected": [_display_quality(key) for key in sorted(found.keys() - expected.keys())],
    }


def _reported_relationships(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for container in (payload, payload.get("profile")):
        if not isinstance(container, dict):
            continue
        for field in ("measured_relationships", "relationships", "relationship_explanations"):
            value = container.get(field)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _score_relationships(
    truth: dict[str, Any], measured: list[dict[str, Any]]
) -> dict[str, Any]:
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

    return {
        "edges": {
            "expected": len(expected),
            "found": len(found),
            "matched": len(matched),
            "recall": _ratio(len(matched), len(expected)),
            "precision": _ratio(len(matched), len(found) - len(redundant_found)),
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
            "accuracy": _ratio(len(cardinality_right), len(matched)),
            "wrong": cardinality_wrong,
        },
        "overlap": {
            "scored_over": len(overlap_right) + len(overlap_wrong),
            "within_tolerance": len(overlap_right),
            "tolerance": OVERLAP_TOLERANCE,
            "outside_tolerance": overlap_wrong,
        },
    }


def score(
    truth: dict[str, Any], measured: list[dict[str, Any]] | dict[str, Any]
) -> dict[str, Any]:
    payload = _measured_payload(measured)
    relationships = _score_relationships(truth, _reported_relationships(payload))
    return {
        "routing": _score_routing(truth, payload),
        "primary_keys": _score_primary_keys(truth, payload),
        "implicit_entities": _score_entities(truth, payload),
        "quality": _score_quality(truth, payload),
        **relationships,
    }


def _load_measured(path: Path) -> dict[str, Any]:
    """Accept a bare relationship list or a structured run/profile payload."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"relationships": payload}
    if isinstance(payload, dict):
        return payload
    raise SystemExit(f"Expected an object or relationship list in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()

    report = score(
        json.loads(args.truth.read_text(encoding="utf-8")), _load_measured(args.run)
    )
    print(json.dumps(report, indent=2))

    routing = report["routing"]
    keys = report["primary_keys"]
    entities = report["implicit_entities"]
    quality = report["quality"]
    edges = report["edges"]
    cardinality = report["cardinality"]
    overlap = report["overlap"]
    print(
        f"\nrouting accuracy={routing['accuracy']}"
        f"   primary keys accuracy={keys['accuracy']}"
        f"\nimplicit entities recall={entities['recall']} precision={entities['precision']}"
        f"   parent-table accuracy={entities['parent_table_accuracy']}"
        f"\nquality recall={quality['recall']} precision={quality['precision']}"
        f"   detail accuracy={quality['detail_accuracy']}"
        f"\nedges recall={edges['recall']} precision={edges['precision']}"
        f"   cardinality accuracy={cardinality['accuracy']}"
        f"   overlap={overlap['within_tolerance']}/{overlap['scored_over']}"
    )
    if edges["known_false_friends_proposed"]:
        print("proposed a documented false friend -- name affinity is being trusted")


if __name__ == "__main__":
    main()
