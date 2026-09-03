"""Promoting an extracted PDF table candidate must not crash on messy headers.

A table extracted from merged or multi-row PDF headers routinely produces
blank or duplicate column labels -- two sub-columns collapsing to the same
header text, an empty merged cell. Parquet (unlike pandas itself) rejects
duplicate column names outright, so promotion crashed with an opaque
"Duplicate column names found" error on exactly the kind of real-world table
this review step exists to let a human accept.
"""

from __future__ import annotations

from pathlib import Path

from ads.contracts.documents import (
    DocumentExtraction,
    DocumentFileResult,
    ExtractedDocument,
    ExtractedTableCandidate,
)
from ads.dataflow import load_table_asset
from ads.documents.promotion import (
    create_document_table_review,
    promote_reviewed_document_tables,
)
from ads.store import ArtifactStore


def _extraction_with(candidate: ExtractedTableCandidate) -> DocumentExtraction:
    return DocumentExtraction(
        source_id="upload:test",
        source_fingerprint="0" * 64,
        engine="docling",
        documents=[
            ExtractedDocument(
                source_file=candidate.source_file,
                page_count=1,
                tables=[candidate],
            )
        ],
        file_results=[
            DocumentFileResult(
                source_file=candidate.source_file,
                status="ready",
                page_count=1,
                table_candidates=1,
            )
        ],
        duration_seconds=1.0,
    )


def test_promoting_a_table_with_duplicate_headers_does_not_crash(tmp_path: Path) -> None:
    # Mirrors a real EPA fuel-economy PDF table: a merged two-row header
    # collapses to the same text ("MPG(e) Combined.") in more than one column.
    candidate = ExtractedTableCandidate(
        candidate_id="table-1",
        source_file="fuel_economy.pdf",
        page_number=3,
        columns=["Model", "MPG(e) Combined.", "MPG(e) Combined.", ""],
        rows=[
            ["Sedan", "32", "30", "AWD"],
            ["Coupe", "28", "27", "FWD"],
        ],
    )
    extraction = _extraction_with(candidate)
    store = ArtifactStore(tmp_path / "store")
    run_id = "run-promotion-test"
    extraction_ref = store.put(extraction, run_id=run_id, stage_exec_id="document-extraction")

    review = create_document_table_review(
        extraction,
        extraction_artifact_id=extraction_ref.artifact_id,
        decisions={"table-1": "accepted"},
    )
    review_ref = store.put(review, run_id=run_id, stage_exec_id="document-table-review")

    promoted = promote_reviewed_document_tables(store, run_id=run_id, review=review)

    assert len(promoted) == 1
    asset, reference = promoted[0]
    assert asset.row_count == 2
    column_names = [column.name for column in asset.columns]
    assert len(column_names) == len(set(column_names)), f"duplicate columns survived: {column_names}"
    # The blank header and the two duplicate "MPG(e) Combined." headers must
    # both have been renamed to something distinct, the same way CSV intake
    # would, not silently dropped.
    assert len(column_names) == 4

    _, frame = load_table_asset(store, reference.artifact_id)
    assert list(frame.columns) == column_names
    assert len(frame) == 2