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
from ads.contracts.staging import StagingWorkspace
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


def _extraction_with_empty_candidate() -> DocumentExtraction:
    """An extraction whose only table has headers and no rows at all.

    `ExtractedTableCandidate.rows` defaults to an empty list, so this is a
    shape the extraction agent can and does produce (#310).
    """
    return DocumentExtraction(
        source_id="upload:documents",
        source_fingerprint="sha256:documents",
        engine="text_layer",
        documents=[
            ExtractedDocument(
                source_file="survey.pdf",
                page_count=3,
                tables=[
                    ExtractedTableCandidate(
                        candidate_id="0001_Agentic_AI_Survey_d03c8c9d:table:2",
                        source_file="survey.pdf",
                        page_number=2,
                        title="Table 2: Benchmarks",
                        columns=["model", "score"],
                    )
                ],
            )
        ],
        duration_seconds=0.1,
    )


def test_empty_candidate_is_refused_with_a_code_not_a_sentence(tmp_path: Path) -> None:
    """The refusal carries a code and the table's provenance, never the id.

    The endpoint answers with the exception text, so a raw message here was
    what the reader saw: English whatever their language, naming an internal
    identifier they have never encountered (#310).
    """
    from ads.documents import CandidateNotPromotable

    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    store.put(
        _extraction_with_empty_candidate(), run_id="run-doc", stage_exec_id="document-extraction"
    )
    review = plane.review_document_tables(
        "run-doc", {"0001_Agentic_AI_Survey_d03c8c9d:table:2": "accepted"}
    )

    try:
        plane.promote_document_tables("run-doc", review["artifact_id"])
    except CandidateNotPromotable as exc:
        assert exc.code == "no_rows"
        assert exc.title == "Table 2: Benchmarks"
        assert exc.source_file == "survey.pdf"
        assert exc.page_number == 2
    else:
        raise AssertionError("a candidate with no rows was promoted")


def test_the_promote_endpoint_answers_in_the_readers_language(tmp_path: Path) -> None:
    """No raw exception text, no internal candidate id, Turkish for a Turkish reader."""
    from fastapi.testclient import TestClient

    from ads.api import create_app

    artifacts = tmp_path / "artifacts"
    store = ArtifactStore(artifacts)
    store.put(
        _extraction_with_empty_candidate(), run_id="run-doc", stage_exec_id="document-extraction"
    )
    client = TestClient(create_app(artifacts))
    review = client.post(
        "/api/runs/run-doc/staging/documents/review",
        json={
            "decisions": [
                {
                    "candidate_id": "0001_Agentic_AI_Survey_d03c8c9d:table:2",
                    "decision": "accepted",
                }
            ]
        },
    )
    assert review.status_code == 200, review.text

    response = client.post(
        "/api/runs/run-doc/staging/documents/promote?lang=tr",
        json={"review_artifact_id": review.json()["artifact_id"]},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "0001_Agentic_AI_Survey_d03c8c9d" not in detail
    assert "has no rows" not in detail
    assert "Table 2: Benchmarks (sayfa 2)" in detail
    assert "veriye dönüştürülemez" in detail

    english = client.post(
        "/api/runs/run-doc/staging/documents/promote?lang=en",
        json={"review_artifact_id": review.json()["artifact_id"]},
    )
    assert "No rows were extracted" in english.json()["detail"]


def _workspace() -> StagingWorkspace:
    return StagingWorkspace(source_id="upload:documents", source_fingerprint="sha256:documents")


def test_promotion_is_recorded_on_the_staging_workspace(tmp_path: Path) -> None:
    """#361: promoting left every reader describing the state before it.

    Promotion wrote a ``TableAsset`` and nothing else. The plan panel reads the
    staging workspace and the source profile -- and the profile is a walk of the
    uploaded files, so a promoted table, which is not a file, could never appear
    there however many times it was re-fetched. The panel kept saying "no
    trusted structured input is selected" and kept offering the same candidate
    count, and a reload changed neither because there was nothing new to read.
    """
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")
    store.put(_workspace(), run_id="run-doc", stage_exec_id="staging", name="data_understanding")

    review = plane.review_document_tables(
        "run-doc",
        {"report:table:1": "accepted", "report:table:2": "rejected"},
    )
    promoted = plane.promote_document_tables("run-doc", review["artifact_id"])

    recorded = plane.staging_workspace("run-doc")["promoted_document_tables"]
    assert [item["candidate_id"] for item in recorded] == ["report:table:1"]
    # The recorded entry points at the asset promotion actually created, and
    # carries the document and page it came from so the panel can name it as
    # something other than an internal candidate id.
    assert recorded[0]["artifact_id"] == promoted["table_assets"][0]["artifact_id"]
    assert recorded[0]["source_file"] == "report.pdf"
    assert recorded[0]["page_number"] == 1
    assert recorded[0]["row_count"] == 2
    # A rejected candidate is not recorded, so the remaining-candidate count
    # the panel derives stays honest.
    assert all(item["candidate_id"] != "report:table:2" for item in recorded)


def test_promoting_the_same_candidate_twice_records_it_once(tmp_path: Path) -> None:
    """A second promotion of the same review must not double the count.

    The candidate count in the plan panel is `extracted - promoted`, so a
    duplicated entry would push it below the truth and hide real candidates
    from the review dialog.
    """
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")
    store.put(_workspace(), run_id="run-doc", stage_exec_id="staging", name="data_understanding")

    review = plane.review_document_tables("run-doc", {"report:table:1": "accepted"})
    plane.promote_document_tables("run-doc", review["artifact_id"])
    plane.promote_document_tables("run-doc", review["artifact_id"])

    recorded = plane.staging_workspace("run-doc")["promoted_document_tables"]
    assert [item["candidate_id"] for item in recorded] == ["report:table:1"]


def test_promotion_without_a_workspace_still_succeeds(tmp_path: Path) -> None:
    """A run with no staging workspace has no panel to keep in step."""
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")

    review = plane.review_document_tables("run-doc", {"report:table:1": "accepted"})
    promoted = plane.promote_document_tables("run-doc", review["artifact_id"])

    assert len(promoted["table_assets"]) == 1
