from __future__ import annotations

from pathlib import Path

import pandas as pd

from ads.api import ControlPlane
from ads.contracts import BUILTIN_PROFILES
from ads.contracts.base import ArtifactType
from ads.contracts.dataflow import TableAsset
from ads.contracts.documents import (
    DocumentExtraction,
    ExtractedDocument,
    ExtractedTableCandidate,
)
from ads.contracts.staging import StagingWorkspace
from ads.dataflow import load_table_asset, persist_table_asset
from ads.documents.promotion import PROMOTED_SOURCE_FORMAT, load_promoted_document_tables
from ads.orchestration import RunState
from ads.pipeline.stages import (
    SOURCE_CARDS_KEY,
    SOURCE_FRAMES_KEY,
    SOURCE_PATH_KEY,
    intake_stage,
)
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


def test_promoted_tables_reach_the_abt_and_the_ui_says_what_happens():
    """#445: the three things that had to move together, and did.

    This replaces the pin #390 left behind. That test asserted the gap rather
    than the fix -- intake reads only the uploaded directory, the run resumes
    past intake, and the copy no longer claims otherwise -- and said in its own
    docstring that it existed so a future wiring change would move them all at
    once. This is that change, so the assertions turn over with it.
    """
    stages = Path("src/ads/pipeline/stages.py").read_text(encoding="utf-8")
    intake = stages[stages.index("def intake_stage(") : stages.index("def integration_stage(")]
    # 1. Intake reads the promoted assets as well as the uploaded directory.
    assert "load_directory(source_path)" in intake
    assert "load_promoted_document_tables(state.store, state.run_id)" in intake
    assert "loaded = [*loaded, *promoted]" in intake

    # 2. Promotion re-enters the graph at intake, so intake runs again with them.
    service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
    replan = service[
        service.index("def _replan_after_promotion(") : service.index(
            "def _measured_relationship_explanations("
        )
    ]
    assert 'start_at="intake"' in replan
    assert "stop_after=self.STAGE_UNTIL" in replan
    # 3. And the plan is re-authored, because it was written before the table.
    assert "self._generate_staging_analysis(runtime)" in replan
    # An accepted plan is not rewritten underneath the decisions made against it.
    assert 'return "plan_accepted"' in replan

    # The claims that measured false are gone from the rendered copy. Matched as
    # whole strings so the comments explaining *why* they went do not count.
    retired = (
        "Accepted tables become training data and are treated exactly like an uploaded file.",
        "They now behave like any other uploaded table.",
        "Extracted tables are candidates and cannot enter ML until reviewed.",
        "Review and promote each extracted table before it can enter training data.",
        # #390's correction, true only while the wiring was missing.
        "Promotion does not add them to the ML training table for this run",
        "They do not join the ML training table for this run.",
    )
    for path in (
        "web/src/components/DocumentTableReview.tsx",
        "web/src/components/UnderstandingWorkspace.tsx",
        "web/src/components/GuidedPipeline.tsx",
        "web/src/lib/i18n.ts",
    ):
        text = Path(path).read_text(encoding="utf-8")
        for claim in retired:
            assert claim not in text, f"{path} still says {claim!r}"

    # And what replaced them says what promotion actually does, including the
    # re-authoring -- which is the part a reader would otherwise be surprised by.
    dialog = Path("web/src/components/DocumentTableReview.tsx").read_text(encoding="utf-8")
    assert "Accepted tables join the ML training data for this run" in dialog
    assert "The plan is re-authored afterwards" in dialog


class TestPromotedTablesEnterIntake:
    """#445: the read side, measured rather than pinned by source shape.

    `load_table_asset` had no production caller. Promotion wrote a
    Parquet-backed asset with verified provenance, recorded the human decision
    and settled the review gate -- and the rows went nowhere.
    """

    @staticmethod
    def _promoted(store: ArtifactStore, plane: ControlPlane) -> None:
        store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")
        review = plane.review_document_tables("run-doc", {"report:table:1": "accepted"})
        plane.promote_document_tables("run-doc", review["artifact_id"])

    def test_a_promoted_asset_comes_back_as_a_table_intake_can_profile(
        self, tmp_path: Path
    ) -> None:
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        self._promoted(store, plane)

        tables = load_promoted_document_tables(store, "run-doc")

        assert len(tables) == 1
        assert list(tables[0].frame.columns) == ["year", "revenue"]
        assert len(tables[0].frame) == 2

    def test_the_rows_stay_distinguishable_from_uploaded_ones(self, tmp_path: Path) -> None:
        # The product's whole stance on extracted tables is that they are not
        # the same as a file someone uploaded. Joining them must not flatten
        # that into "just another table": the page they came from rides along,
        # and the format says where they came from.
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        self._promoted(store, plane)

        table = load_promoted_document_tables(store, "run-doc")[0]

        assert table.source_format == PROMOTED_SOURCE_FORMAT
        assert table.source_uri == "report.pdf#page=1"
        # And the name is one a plan can join on, not the raw candidate id.
        assert table.name == "doc_report_table_1"

    def test_the_integrated_abt_is_not_mistaken_for_a_promoted_table(self, tmp_path: Path) -> None:
        # `integration_stage` writes a `TableAsset` too. Selecting on the
        # artifact type alone would feed the previous run's ABT back into
        # intake as a source table.
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        self._promoted(store, plane)
        persist_table_asset(
            store,
            pd.DataFrame({"a": [1, 2]}),
            run_id="run-doc",
            producer_component_id="integrate-data",
            name="integrated_table",
        )

        tables = load_promoted_document_tables(store, "run-doc")

        assert [table.name for table in tables] == ["doc_report_table_1"]

    def test_a_corrupted_blob_is_left_out_rather_than_mixed_into_training_data(
        self, tmp_path: Path
    ) -> None:
        # `load_table_asset` verifies the content-addressed blob. A failure
        # there must not become an exception that kills intake, nor rows that
        # silently disagree with their own fingerprint.
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        self._promoted(store, plane)
        reference = store.latest("run-doc", ArtifactType.TABLE_ASSET)
        assert reference is not None
        asset = store.load(reference.artifact_id, TableAsset)
        (store.blob_dir(reference.artifact_id) / asset.blob.filename).write_bytes(b"not parquet")

        assert load_promoted_document_tables(store, "run-doc") == []

    def test_intake_puts_them_on_the_blackboard_beside_the_uploaded_tables(
        self, tmp_path: Path
    ) -> None:
        # The end of the read side: `integration_stage` joins whatever intake
        # left on the blackboard, so this is the moment the rows become
        # joinable at all.
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        self._promoted(store, plane)
        uploads = tmp_path / "csvs"
        uploads.mkdir()
        pd.DataFrame({"year": [2024], "spend": [3.0]}).to_csv(uploads / "budget.csv", index=False)
        state = RunState(run_id="run-doc", store=store, profile=BUILTIN_PROFILES["full_auto"])
        state.blackboard[SOURCE_PATH_KEY] = uploads

        intake_stage(state)

        frames = state.blackboard[SOURCE_FRAMES_KEY]
        assert set(frames) == {"budget", "doc_report_table_1"}
        cards = {card.table_name: card for card in state.blackboard[SOURCE_CARDS_KEY]}
        assert cards["doc_report_table_1"].source_format == PROMOTED_SOURCE_FORMAT
        assert cards["doc_report_table_1"].n_rows == 2


class TestThePromotionRecordSurvives:
    def test_a_later_snapshot_does_not_erase_the_promotion(self) -> None:
        # `_persist_staging_workspace` rebuilds the snapshot from scratch and
        # this field was not among the ones carried over, so every planner turn
        # after a promotion dropped it -- and the review dialog offered the same
        # candidates again. The re-plan ends in exactly that call, so this had
        # to hold before a promotion could trigger one.
        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        persist = service[
            service.index("def _persist_staging_workspace(") : service.index(
                "def _staging_component_outputs("
            )
        ]
        assert (
            "promoted_document_tables=(list(previous.promoted_document_tables) if previous else [])"
            in persist
        )


class TestPromotionAnswersWithWhatItDidToThePlan:
    def test_an_archived_run_says_so_instead_of_pretending(self, tmp_path: Path) -> None:
        # No runtime means the spec, registry and blackboard did not survive the
        # restart, so the graph cannot be re-entered. The promotion is still
        # real; the re-plan is reported as not done rather than silently skipped.
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")
        review = plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        promoted = plane.promote_document_tables("run-doc", review["artifact_id"])

        assert promoted["replan"] == "unavailable"
        assert len(promoted["table_assets"]) == 1

    def test_promoting_nothing_asks_for_nothing(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path / "artifacts")
        plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
        store.put(_extraction(), run_id="run-doc", stage_exec_id="document-extraction")
        review = plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        promoted = plane.promote_document_tables("run-doc", review["artifact_id"])

        assert promoted["replan"] == "nothing_promoted"
        assert promoted["table_assets"] == []
