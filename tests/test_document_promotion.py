from __future__ import annotations

from pathlib import Path

from ads.api import ControlPlane
from ads.contracts.base import ArtifactType
from ads.contracts.dataflow import TableAsset
from ads.contracts.documents import (
    DocumentExtraction,
    ExtractedDocument,
    ExtractedTableCandidate,
)
from ads.dataflow import load_table_asset
from ads.store import ArtifactStore


def _extraction() -> DocumentExtraction:
    return DocumentExtraction(
        source_id="upload:documents",
        source_fingerprint="sha256:documents",
        engine="text_layer",
        documents=[
            ExtractedDocument(
                source_file="report.pdf",
                page_count=2,
                tables=[
                    ExtractedTableCandidate(
                        candidate_id="report:table:1",
                        source_file="report.pdf",
                        page_number=1,
                        columns=["year", "revenue"],
                        rows=[[2024, 12.5], [2025, 14.0]],
                    ),
                    ExtractedTableCandidate(
                        candidate_id="report:table:2",
                        source_file="report.pdf",
                        page_number=2,
                        columns=["note"],
                        rows=[["context only"]],
                    ),
                ],
            )
        ],
        duration_seconds=0.1,
    )


def test_human_review_and_promotion_are_two_durable_transitions(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    extraction_ref = store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")

    review = plane.review_document_tables(
        "run-doc",
        {"report:table:1": "accepted", "report:table:2": "rejected"},
    )
    assert review["accepted"] == 1
    assert review["rejected"] == 1
    assert store.latest("run-doc", ArtifactType.DOCUMENT_TABLE_REVIEW) is not None
    assert store.list("run-doc", artifact_type=ArtifactType.TABLE_ASSET) == []

    promoted = plane.promote_document_tables("run-doc", review["artifact_id"])

    assert len(promoted["table_assets"]) == 1
    table_id = promoted["table_assets"][0]["artifact_id"]
    asset, frame = load_table_asset(store, table_id)
    assert isinstance(asset, TableAsset)
    assert frame.to_dict(orient="records") == [
        {"year": 2024, "revenue": 12.5},
        {"year": 2025, "revenue": 14.0},
    ]
    assert asset.provenance.source_artifact_ids == [extraction_ref.artifact_id]
    assert asset.provenance.source_uris == ["report.pdf#page=1"]


def test_promotion_rejects_review_from_another_run(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    store.put(_extraction(), run_id="run-a", stage_exec_id="document-extraction")
    review = plane.review_document_tables("run-a", {"report:table:1": "accepted"})

    try:
        plane.promote_document_tables("run-b", review["artifact_id"])
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("cross-run document review was promoted")
