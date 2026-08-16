"""Materialize the twenty-case Assurance Corpus half-corpus deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from generators.casebook import (  # noqa: E402
    BRIEF_ONLY,
    CASE_SPECS,
    DATA_ONLY,
    NOTEBOOK_ONLY,
    build_case,
)
from generators.mutations import assert_artifact_isolation  # noqa: E402


def _stable_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def materialize(root: Path = BENCHMARK_ROOT) -> dict[str, dict[str, str]]:
    """Write public cases, verify isolation, and return public-file hashes."""
    all_hashes: dict[str, dict[str, str]] = {}

    for case_id in CASE_SPECS:
        case_root = root / "cases" / case_id
        source_brief = BENCHMARK_ROOT / "cases" / case_id / "brief.md"
        brief = source_brief.read_text(encoding="utf-8")
        dataset_name, csv_content, notebook = build_case(case_id)
        public_files = {
            "brief.md": brief,
            f"dataset/{dataset_name}": csv_content,
            "analysis.ipynb": _stable_json(notebook),
        }
        hashes: dict[str, str] = {}
        for relative_path, content in public_files.items():
            _write(case_root / relative_path, content)
            hashes[relative_path] = _sha256(content.encode("utf-8"))

        checksum_document = {
            "case_id": case_id,
            "sha256": hashes,
        }
        _write(case_root / "checksums.json", _stable_json(checksum_document))
        all_hashes[case_id] = hashes

    for case_id, spec in CASE_SPECS.items():
        safe_id = "c001" if spec.scenario == "readmission" else "c011"
        dataset_key = next(
            key for key in all_hashes[case_id] if key.startswith("dataset/")
        )
        safe_dataset_key = next(
            key for key in all_hashes[safe_id] if key.startswith("dataset/")
        )
        assert_artifact_isolation(
            variant=spec.variant,
            data_hash=all_hashes[case_id][dataset_key],
            notebook_hash=all_hashes[case_id]["analysis.ipynb"],
            safe_data_hash=all_hashes[safe_id][safe_dataset_key],
            safe_notebook_hash=all_hashes[safe_id]["analysis.ipynb"],
            notebook_only=NOTEBOOK_ONLY,
            data_only=DATA_ONLY,
            brief_only=BRIEF_ONLY,
        )

    return all_hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=BENCHMARK_ROOT,
        help="Benchmark root to materialize.",
    )
    args = parser.parse_args()
    hashes = materialize(args.root.resolve())
    print(_stable_json({"materialized": sorted(hashes), "hashes": hashes}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
