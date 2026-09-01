"""Chatting with the planner is a conversation, not an approval (#316).

`_persist_staging_workspace` defaulted `pipeline_recommendation` to
`"create_pipeline"` whenever the chat response did not name one. Every planner
turn persists a workspace, so any message at all -- a question about the raw
files, and even a reply whose own text said to review the extracted PDF tables
first -- published a runnable plan and unlocked the ML controls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads.api import ControlPlane
from ads.api.service import _resolved_pipeline_recommendation
from ads.contracts.documents import (
    DocumentExtraction,
    ExtractedDocument,
    ExtractedTableCandidate,
)
from ads.contracts.staging import RuntimeConfigurationPlan
from ads.store import ArtifactStore


def _plan(recommendation: str) -> RuntimeConfigurationPlan:
    return RuntimeConfigurationPlan(pipeline_recommendation=recommendation)


class TestWhatOneChatTurnMaySay:
    def test_a_turn_that_names_nothing_does_not_invent_create_pipeline(self) -> None:
        # The defect: this is the shape of every ordinary question.
        assert _resolved_pipeline_recommendation(None, None, False) == "defer_pipeline"
        assert _resolved_pipeline_recommendation("", None, False) == "defer_pipeline"

    def test_a_turn_that_names_nothing_preserves_what_the_workspace_decided(self) -> None:
        # A real proposal already exists; asking a follow-up question must not
        # retract it any more than it should have created one.
        assert (
            _resolved_pipeline_recommendation(None, _plan("create_pipeline"), False)
            == "create_pipeline"
        )
        assert (
            _resolved_pipeline_recommendation(None, _plan("no_pipeline"), False) == "no_pipeline"
        )

    def test_an_explicit_recommendation_is_honoured(self) -> None:
        assert _resolved_pipeline_recommendation("create_pipeline", None, False) == (
            "create_pipeline"
        )
        assert (
            _resolved_pipeline_recommendation("defer_pipeline", _plan("create_pipeline"), False)
            == "defer_pipeline"
        )

    def test_an_unrecognised_value_is_not_a_decision_either(self) -> None:
        assert _resolved_pipeline_recommendation("go_for_it", None, False) == "defer_pipeline"

    def test_unreviewed_table_candidates_defer_however_confident_the_planner_is(self) -> None:
        # Promoting an extracted table is a human decision the product refuses
        # to make silently, so proposing to start ML while candidates sit
        # unreviewed is proposing to skip it.
        assert _resolved_pipeline_recommendation("create_pipeline", None, True) == "defer_pipeline"
        assert (
            _resolved_pipeline_recommendation(None, _plan("create_pipeline"), True)
            == "defer_pipeline"
        )

    def test_pending_tables_do_not_upgrade_a_weaker_recommendation(self) -> None:
        assert _resolved_pipeline_recommendation("no_pipeline", None, True) == "no_pipeline"


def _extraction(*, with_tables: bool) -> DocumentExtraction:
    tables = (
        [
            ExtractedTableCandidate(
                candidate_id="report:table:1",
                source_file="report.pdf",
                page_number=1,
                columns=["year", "revenue"],
                rows=[[2024, 12.5]],
            )
        ]
        if with_tables
        else []
    )
    return DocumentExtraction(
        source_id="upload:documents",
        source_fingerprint="sha256:documents",
        engine="text_layer",
        documents=[ExtractedDocument(source_file="report.pdf", page_count=2, tables=tables)],
        duration_seconds=0.1,
    )


@pytest.fixture
def plane(tmp_path: Path) -> ControlPlane:
    return ControlPlane(store=ArtifactStore(tmp_path / "artifacts"))


class TestWhetherTablesAwaitReview:
    def test_a_run_with_no_documents_has_nothing_pending(self, plane: ControlPlane) -> None:
        assert plane._document_tables_await_review("run-none") is False

    def test_an_extraction_that_found_no_tables_has_nothing_pending(
        self, plane: ControlPlane
    ) -> None:
        plane.store.put(
            _extraction(with_tables=False), run_id="run-doc", stage_exec_id="document-extraction"
        )
        assert plane._document_tables_await_review("run-doc") is False

    def test_candidates_with_no_recorded_review_are_pending(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        assert plane._document_tables_await_review("run-doc") is True

    def test_rejecting_every_candidate_still_settles_it(self, plane: ControlPlane) -> None:
        # A review is a completed decision whichever way it went. Asking whether
        # anything was *promoted* would leave the flow blocked forever on a
        # document whose tables a person deliberately turned down.
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        assert plane._document_tables_await_review("run-doc") is False

    def test_accepting_a_candidate_settles_it_too(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        assert plane._document_tables_await_review("run-doc") is False
