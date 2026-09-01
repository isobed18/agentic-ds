"""The evidence a person needs to accept or reject an extracted PDF table (#303).

Approving a candidate promotes numbers a machine read off a PDF page into the
data a model learns from. Before this, the review UI showed only a title and a
page number, so the decision was a blind click. The preview now carries the
detected headers and a bounded sample of the rows -- enough to tell a real
table from a mis-detection -- without streaming an arbitrarily large extraction
to the browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads.api import ControlPlane
from ads.contracts.documents import (
    DocumentExtraction,
    ExtractedDocument,
    ExtractedTableCandidate,
)
from ads.store import ArtifactStore


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def plane(store: ArtifactStore) -> ControlPlane:
    return ControlPlane(store=store)


class TestSampleBounds:
    def test_rows_and_columns_are_capped(self) -> None:
        rows = [[f"r{r}c{c}" for c in range(40)] for r in range(40)]
        sample = ControlPlane._document_table_sample(rows, max_rows=6, max_columns=10)
        assert len(sample) == 6
        assert all(len(row) == 10 for row in sample)

    def test_cells_are_stringified_and_none_becomes_empty(self) -> None:
        sample = ControlPlane._document_table_sample([[1, 2.5, True, None, "x"]])
        assert sample == [["1", "2.5", "True", "", "x"]]

    def test_long_cells_are_truncated(self) -> None:
        sample = ControlPlane._document_table_sample([["a" * 500]], cell_limit=80)
        assert len(sample[0][0]) == 80


class TestPreviewCarriesTheEvidence:
    def test_preview_surfaces_headers_and_a_bounded_row_sample(
        self, plane: ControlPlane, store: ArtifactStore
    ) -> None:
        table = ExtractedTableCandidate(
            candidate_id="t1",
            source_file="report.pdf",
            page_number=2,
            title="Quarterly orders",
            columns=["quarter", "orders"],
            rows=[["Q1", 10], ["Q2", 20], ["Q3", 30]],
        )
        extraction = DocumentExtraction(
            source_id="src1",
            source_fingerprint="f" * 8,
            engine="text_layer",
            duration_seconds=0.1,
            documents=[
                ExtractedDocument(source_file="report.pdf", tables=[table]),
            ],
        )
        ref = store.put(extraction, run_id="run1", name="extraction")

        preview = plane.artifact_preview(ref.artifact_id)
        candidate = preview["documents"][0]["tables"][0]
        assert candidate["columns"] == ["quarter", "orders"]
        assert candidate["row_count"] == 3
        # Stringified so the UI renders a table it can trust, not raw JSON types.
        assert candidate["sample_rows"] == [["Q1", "10"], ["Q2", "20"], ["Q3", "30"]]
