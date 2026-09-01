"""Score the built-in PDF table extractor against the bundled CSV oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ads.documents import extract_document_directory
from ads.testing.pdf_demo import PDF_DEMO_NAME, pdf_demo_fixture_dir, read_pdf_demo_truth


def _same(left: object, right: object) -> bool:
    return str(left).strip() == str(right).strip()


def score_demo(
    dataset_dir: str | Path | None = None, *, output_dir: str | Path
) -> dict[str, Any]:
    """Run the guaranteed offline engine and return row-free benchmark metrics."""
    root = Path(dataset_dir) if dataset_dir is not None else pdf_demo_fixture_dir()
    source = root / PDF_DEMO_NAME
    truth_columns, truth_rows = read_pdf_demo_truth(root)
    extraction = extract_document_directory(
        root,
        source_id="pdf-extraction-demo",
        source_fingerprint=hashlib.sha256(source.read_bytes()).hexdigest(),
        engine="text_layer",
        settings={"ocr": "never", "extract_tables": True, "extract_figures": False},
        output_dir=output_dir,
    )
    candidates = [table for document in extraction.documents for table in document.tables]
    matching = next((table for table in candidates if table.columns == truth_columns), None)
    actual_rows = matching.rows if matching is not None else []
    expected_cells = len(truth_rows) * len(truth_columns)
    correct_cells = sum(
        _same(actual_rows[row_index][column_index], expected)
        for row_index, truth_row in enumerate(truth_rows)
        for column_index, expected in enumerate(truth_row)
        if row_index < len(actual_rows) and column_index < len(actual_rows[row_index])
    )
    columns_exact = matching is not None
    row_count = len(actual_rows)
    cell_accuracy = correct_cells / expected_cells if expected_cells else 1.0
    passed = (
        len(candidates) == 1
        and columns_exact
        and row_count == len(truth_rows)
        and cell_accuracy == 1.0
        and matching is not None
        and matching.page_number == 1
    )
    return {
        "benchmark": "pdf-extraction-v0",
        "engine": extraction.engine,
        "passed": passed,
        "candidate_tables": len(candidates),
        "columns_exact": columns_exact,
        "row_count": row_count,
        "expected_row_count": len(truth_rows),
        "cell_accuracy": cell_accuracy,
        "page_number_exact": bool(matching and matching.page_number == 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=pdf_demo_fixture_dir())
    parser.add_argument("--output", type=Path, default=Path("data/pdf-extraction-benchmark"))
    args = parser.parse_args()
    result = score_demo(args.data, output_dir=args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
