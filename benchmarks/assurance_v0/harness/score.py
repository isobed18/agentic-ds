"""Validate and deterministically score one assurance assessment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class AssessmentValidationError(ValueError):
    """Assessment does not conform to the neutral schema."""


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise AssessmentValidationError(f"unsupported schema type: {expected}")


def _validate(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in allowed):
            raise AssessmentValidationError(
                f"{path}: expected {' or '.join(allowed)}, got {type(value).__name__}"
            )

    if "enum" in schema and value not in schema["enum"]:
        raise AssessmentValidationError(f"{path}: {value!r} is not in {schema['enum']!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise AssessmentValidationError(f"{path}: missing required keys {missing!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise AssessmentValidationError(f"{path}: unexpected keys {extras!r}")
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise AssessmentValidationError(f"{path}: too few items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise AssessmentValidationError(f"{path}: string is too short")

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        raise AssessmentValidationError(f"{path}: value is below minimum")


def validate_assessment(assessment: dict[str, Any], schema: dict[str, Any]) -> None:
    _validate(assessment, schema)


def _locator_matches(expected: dict[str, Any], evidence: dict[str, Any]) -> bool:
    return all(evidence.get(key) == value for key, value in expected.items())


def score_assessment(
    oracle: dict[str, Any],
    assessment: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Return case-level safety metrics without an aggregate score."""
    validate_assessment(assessment, schema)
    variant = oracle["variant"]
    expected_disposition = oracle["expected_disposition"]
    actual_disposition = assessment["disposition"]
    expected_locators = oracle.get("expected_locators", [])
    evidence = [
        locator
        for finding in assessment["findings"]
        for locator in finding.get("evidence", [])
    ]
    matched = [
        expected
        for expected in expected_locators
        if any(_locator_matches(expected, item) for item in evidence)
    ]
    locator_recall = None
    if expected_locators:
        locator_recall = len(matched) / len(expected_locators)

    return {
        "case_id": oracle["case_id"],
        "pair_id": oracle["pair_id"],
        "variant": variant,
        "expected_disposition": expected_disposition,
        "actual_disposition": actual_disposition,
        "disposition_correct": actual_disposition == expected_disposition,
        "unsafe_pass": variant == "mutated" and actual_disposition == "supported",
        "safe_rejection": variant == "safe" and actual_disposition == "not_supported",
        "unknown_fabrication": (
            variant == "underspecified"
            and actual_disposition != "indeterminate"
            and not assessment["unknowns"]
        ),
        "expected_locator_count": len(expected_locators),
        "matched_locator_count": len(matched),
        "expected_locator_recall": locator_recall,
        "unexpected_finding_count": (
            len(assessment["findings"]) if not expected_locators else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))
    assessment = json.loads(args.assessment.read_text(encoding="utf-8"))
    score = score_assessment(oracle, assessment, schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(score, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(score, indent=2, sort_keys=True))
    return 0 if score["disposition_correct"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
