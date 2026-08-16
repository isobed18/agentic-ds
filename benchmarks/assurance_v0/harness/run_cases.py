"""Materialize and execute the half-corpus, retaining measured metric gaps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from generators.casebook import CASE_SPECS  # noqa: E402

from harness.execute_notebook import execute_notebook  # noqa: E402
from harness.materialize import materialize  # noqa: E402


def run_cases(root: Path = BENCHMARK_ROOT) -> dict[str, Any]:
    """Execute every supplied notebook and measure gaps against scenario-safe siblings."""
    materialize(root)
    evaluations: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}

    for case_id in CASE_SPECS:
        case_root = root / "cases" / case_id
        try:
            execute_notebook(
                case_root / "analysis.ipynb",
                root / "results" / f"{case_id}.executed.ipynb",
            )
            evaluations[case_id] = json.loads(
                (case_root / "evaluation.json").read_text(encoding="utf-8")
            )
        except Exception as exc:  # retain every failure; CLI returns non-zero below
            failures[case_id] = f"{type(exc).__name__}: {exc}"

    measurements: dict[str, dict[str, Any]] = {}
    for case_id, evaluation in evaluations.items():
        spec = CASE_SPECS[case_id]
        safe_id = "c001" if spec.scenario == "readmission" else "c011"
        safe_evaluation = evaluations.get(safe_id)
        safe_value = None if safe_evaluation is None else safe_evaluation["value"]
        supplied_value = evaluation["value"]
        measurements[case_id] = {
            "metric": evaluation["metric"],
            "supplied_metric": supplied_value,
            "oracle_metric": safe_value,
            "optimism_gap": (
                None if safe_value is None else supplied_value - safe_value
            ),
            "group_overlap_count": evaluation["group_overlap_count"],
        }

    report = {
        "materialized_count": len(CASE_SPECS),
        "executed_count": len(evaluations),
        "failure_count": len(failures),
        "failures": failures,
        "measurements": measurements,
    }
    output = root / "results" / "measurements.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=BENCHMARK_ROOT)
    args = parser.parse_args()
    report = run_cases(args.root.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
