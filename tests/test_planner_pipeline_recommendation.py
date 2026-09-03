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
from ads.contracts.staging import RuntimeConfigurationPlan, StagingWorkspace
from ads.store import ArtifactStore


def _plan(recommendation: str, deferred_on: str = "") -> RuntimeConfigurationPlan:
    return RuntimeConfigurationPlan(
        pipeline_recommendation=recommendation, deferred_on=deferred_on
    )


class TestWhatOneChatTurnMaySay:
    def test_a_turn_that_names_nothing_does_not_invent_create_pipeline(self) -> None:
        # The defect: this is the shape of every ordinary question. #430: and
        # the deferral now says what would lift it -- the planner stating one.
        assert _resolved_pipeline_recommendation(None, None, False) == (
            "defer_pipeline",
            "planner_decision",
        )
        assert _resolved_pipeline_recommendation("", None, False) == (
            "defer_pipeline",
            "planner_decision",
        )

    def test_a_turn_that_names_nothing_preserves_what_the_workspace_decided(self) -> None:
        # A real proposal already exists; asking a follow-up question must not
        # retract it any more than it should have created one.
        assert _resolved_pipeline_recommendation(None, _plan("create_pipeline"), False) == (
            "create_pipeline",
            "",
        )
        assert _resolved_pipeline_recommendation(None, _plan("no_pipeline"), False) == (
            "no_pipeline",
            "",
        )

    def test_an_explicit_recommendation_is_honoured(self) -> None:
        assert _resolved_pipeline_recommendation("create_pipeline", None, False) == (
            "create_pipeline",
            "",
        )

    def test_an_unrecognised_value_is_not_a_decision_either(self) -> None:
        assert _resolved_pipeline_recommendation("go_for_it", None, False) == (
            "defer_pipeline",
            "planner_decision",
        )

    def test_unreviewed_table_candidates_defer_however_confident_the_planner_is(self) -> None:
        # Promoting an extracted table is a human decision the product refuses
        # to make silently, so proposing to start ML while candidates sit
        # unreviewed is proposing to skip it.
        assert _resolved_pipeline_recommendation("create_pipeline", None, True) == (
            "defer_pipeline",
            "document_table_review",
        )
        assert _resolved_pipeline_recommendation(None, _plan("create_pipeline"), True) == (
            "defer_pipeline",
            "document_table_review",
        )

    def test_pending_tables_do_not_upgrade_a_weaker_recommendation(self) -> None:
        assert _resolved_pipeline_recommendation("no_pipeline", None, True) == ("no_pipeline", "")


class TestADeferralHasToBeActionable:
    """#430: a deferral nothing can resolve is not a decision, it is a dead end.

    The reviews the ML pipeline performs are its own stages -- leakage_audit,
    validation_strategy, eda -- so deferring the pipeline to wait for one
    blocks the pipeline that contains the audit it is waiting for. On the
    reported bank.csv run, whose target is measurably viable, the run simply
    stopped there and stayed stopped: no timer, no re-check, no event.
    """

    def test_a_deferral_with_nothing_outstanding_is_refused(self) -> None:
        assert _resolved_pipeline_recommendation("defer_pipeline", None, False) == (
            "create_pipeline",
            "",
        )

    def test_a_deferral_is_honoured_while_a_human_decision_is_outstanding(self) -> None:
        # The one precondition this product can point at and watch resolve.
        assert _resolved_pipeline_recommendation("defer_pipeline", None, True) == (
            "defer_pipeline",
            "document_table_review",
        )

    def test_a_turn_naming_nothing_does_not_re_assert_a_resolved_block(self) -> None:
        # The absorbing state: previous.pipeline_recommendation was inherited
        # verbatim, so a conversational answer re-persisted the block and the
        # documented escape (tell the Planner what it is missing) only worked
        # on a turn that happened to volunteer create_pipeline.
        resolved = _plan("defer_pipeline", "document_table_review")
        assert _resolved_pipeline_recommendation(None, resolved, False) == ("create_pipeline", "")

    def test_a_turn_naming_nothing_keeps_a_block_that_is_still_outstanding(self) -> None:
        pending = _plan("defer_pipeline", "document_table_review")
        assert _resolved_pipeline_recommendation(None, pending, True) == (
            "defer_pipeline",
            "document_table_review",
        )

    def test_waiting_on_the_planner_is_settled_only_by_the_planner(self) -> None:
        # Nothing a person does to the data lifts this one; it needs a turn
        # that states a decision. Saying so is the point -- an unexplained
        # block is what a reader could not act on.
        waiting = _plan("defer_pipeline", "planner_decision")
        assert _resolved_pipeline_recommendation(None, waiting, False) == (
            "defer_pipeline",
            "planner_decision",
        )
        assert _resolved_pipeline_recommendation("create_pipeline", waiting, False) == (
            "create_pipeline",
            "",
        )

    def test_a_legacy_deferral_carrying_no_precondition_is_not_a_block(self) -> None:
        # Snapshots written before deferred_on existed. They name nothing to
        # wait for, which is exactly the dead end this issue reports.
        assert _resolved_pipeline_recommendation(None, _plan("defer_pipeline"), False) == (
            "create_pipeline",
            "",
        )


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


class TestTheDeferralIsRe_evaluatedWithoutAPlannerTurn:
    """#430: the recommendation only ever changed on a planner turn.

    `_resolved_pipeline_recommendation` had one caller, inside the
    `if planner_result is not None:` branch, and everywhere else the plan was
    carried forward verbatim. So once deferred the state sustained itself --
    reloading, re-opening and waiting all changed nothing, because there was
    no timer, no re-check and no event. `_reconsidered_plan` runs wherever a
    plan is read or carried, so the precondition resolving is enough. This is
    also the reopen path #316 never had.
    """

    def test_a_pending_review_keeps_the_block(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        plan = _plan("defer_pipeline", "document_table_review")

        reconsidered = plane._reconsidered_plan("run-doc", plan)

        assert reconsidered is not None
        assert reconsidered.pipeline_recommendation == "defer_pipeline"
        assert reconsidered.deferred_on == "document_table_review"

    def test_recording_the_review_lifts_it(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        plan = _plan("defer_pipeline", "document_table_review")
        plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        reconsidered = plane._reconsidered_plan("run-doc", plan)

        assert reconsidered is not None
        assert reconsidered.pipeline_recommendation == "create_pipeline"
        assert reconsidered.deferred_on == ""

    def test_the_lifted_plan_is_actually_runnable(self, plane: ControlPlane) -> None:
        """A deferred plan carries an empty configuration, so lifting it
        without filling one would produce an accepted plan that cannot start."""
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        reconsidered = plane._reconsidered_plan(
            "run-doc", _plan("defer_pipeline", "document_table_review")
        )

        assert reconsidered is not None
        assert reconsidered.configuration["candidate_limit"] == 2
        assert reconsidered.configuration["n_folds"] == 5

    def test_a_lifted_deferral_stops_at_the_audit_instead_of_not_starting(
        self, plane: ControlPlane
    ) -> None:
        """The planner wanted a review, and the reviews this pipeline performs
        are its own stages. Refusing to start withholds the evidence; running
        to the stage and stopping there is the same review, performed."""
        reconsidered = plane._reconsidered_plan("run-none", _plan("defer_pipeline"))

        assert reconsidered is not None
        assert reconsidered.pipeline_recommendation == "create_pipeline"
        assert "leakage_audit" in reconsidered.checkpoint_stages

    def test_a_plan_a_person_already_accepted_is_left_alone(self, plane: ControlPlane) -> None:
        # Their decision is not something to recompute.
        accepted = RuntimeConfigurationPlan(
            pipeline_recommendation="defer_pipeline",
            deferred_on="document_table_review",
            status="accepted",
            accepted=True,
        )

        assert plane._reconsidered_plan("run-none", accepted) is accepted

    def test_nothing_is_recomputed_for_a_plan_that_is_not_deferred(
        self, plane: ControlPlane
    ) -> None:
        for recommendation in ("create_pipeline", "no_pipeline"):
            plan = _plan(recommendation)
            assert plane._reconsidered_plan("run-none", plan) is plan

    def test_no_plan_stays_no_plan(self, plane: ControlPlane) -> None:
        assert plane._reconsidered_plan("run-none", None) is None


class TestTheReadPathShowsTheCurrentAnswer:
    """`staging_workspace` returned the stored snapshot verbatim.

    #430: the client polls it every 2.2s, so re-checking here means a
    precondition that resolves lifts the block on the next read with no timer
    and no event. The snapshot stays the immutable record; this is a
    projection over it, like `component_outputs` beside it.
    """

    @staticmethod
    def _workspace(plane: ControlPlane, run_id: str, plan: RuntimeConfigurationPlan) -> None:
        plane.store.put(
            StagingWorkspace(
                source_id="upload:documents",
                source_fingerprint="sha256:documents",
                recommended_plan=plan,
            ),
            run_id=run_id,
            stage_exec_id="staging",
        )

    def test_a_resolved_precondition_reads_as_runnable(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        self._workspace(plane, "run-doc", _plan("defer_pipeline", "document_table_review"))

        blocked = plane.staging_workspace("run-doc")
        assert blocked["recommended_plan"]["pipeline_recommendation"] == "defer_pipeline"

        plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        # No planner turn, no new snapshot, no reload of anything else.
        lifted = plane.staging_workspace("run-doc")
        assert lifted["recommended_plan"]["pipeline_recommendation"] == "create_pipeline"
        assert lifted["recommended_plan"]["deferred_on"] == ""

    def test_accepting_freezes_what_the_reader_was_shown(self, plane: ControlPlane) -> None:
        # Freezing the stored snapshot verbatim would accept a defer_pipeline
        # with an empty configuration -- an accepted plan that cannot start.
        plane.store.put(
            _extraction(with_tables=True), run_id="run-doc", stage_exec_id="document-extraction"
        )
        self._workspace(plane, "run-doc", _plan("defer_pipeline", "document_table_review"))
        plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        shown = plane.staging_workspace("run-doc")

        assert shown["recommended_plan"]["pipeline_recommendation"] == "create_pipeline"
        assert shown["recommended_plan"]["configuration"]["n_folds"] == 5

    def test_a_workspace_with_no_plan_still_reads(self, plane: ControlPlane) -> None:
        self._workspace_without_plan(plane, "run-bare")

        assert plane.staging_workspace("run-bare")["recommended_plan"] is None

    @staticmethod
    def _workspace_without_plan(plane: ControlPlane, run_id: str) -> None:
        plane.store.put(
            StagingWorkspace(source_id="upload:x", source_fingerprint="sha256:x"),
            run_id=run_id,
            stage_exec_id="staging",
        )


class TestRecordingTheDecisionLiftsTheGate:
    """#316 reopened: the gate closes correctly and nothing reopens it.

    `_resolved_pipeline_recommendation` clamps `create_pipeline` to
    `defer_pipeline` while candidates sit unreviewed, and it is called from one
    place -- inside `_persist_staging_workspace`'s `if planner_result is not
    None:` branch. Everywhere else the plan is carried forward verbatim, so the
    clamp outlived the condition that applied it: promoting the tables left the
    plan node red and saying "Blocked", and reloading changed nothing.

    The invariant: recording the human decision the gate is waiting on must be
    sufficient to lift the gate.
    """

    @staticmethod
    def _deferred_run(plane: ControlPlane, run_id: str = "run-doc") -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id=run_id, stage_exec_id="document-extraction"
        )
        plane.store.put(
            StagingWorkspace(
                source_id="upload:documents",
                source_fingerprint="sha256:documents",
                recommended_plan=_plan("defer_pipeline"),
            ),
            run_id=run_id,
            stage_exec_id="staging",
        )

    def test_the_gate_holds_until_the_review_is_recorded(self, plane: ControlPlane) -> None:
        self._deferred_run(plane)

        plan = plane.staging_workspace("run-doc")["recommended_plan"]
        assert plan["pipeline_recommendation"] == "defer_pipeline"

    def test_accepting_a_candidate_lifts_it(self, plane: ControlPlane) -> None:
        self._deferred_run(plane)

        plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        plan = plane.staging_workspace("run-doc")["recommended_plan"]
        assert plan["pipeline_recommendation"] == "create_pipeline"

    def test_rejecting_every_candidate_lifts_it_too(self, plane: ControlPlane) -> None:
        # A rejection is a completed decision, which is what
        # `_document_tables_await_review`'s own docstring says -- and it is the
        # outcome "Continue without these tables" is for.
        self._deferred_run(plane)

        plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        plan = plane.staging_workspace("run-doc")["recommended_plan"]
        assert plan["pipeline_recommendation"] == "create_pipeline"

    def test_the_lifted_plan_can_actually_start(self, plane: ControlPlane) -> None:
        # A deferred plan is persisted with an empty configuration, so a lift
        # without these unblocks a plan that then cannot run.
        self._deferred_run(plane)

        plane.review_document_tables("run-doc", {"report:table:1": "rejected"})

        configuration = plane.staging_workspace("run-doc")["recommended_plan"]["configuration"]
        assert configuration["candidate_limit"] == 2
        assert configuration["n_folds"] == 5

    def test_the_lift_survives_the_promotion_written_after_it(
        self, plane: ControlPlane
    ) -> None:
        # `_record_promoted_document_tables` copies the previous snapshot
        # forward, so the order of the two writes matters.
        self._deferred_run(plane)
        review = plane.review_document_tables("run-doc", {"report:table:1": "accepted"})

        plane.promote_document_tables("run-doc", review["artifact_id"])

        workspace = plane.staging_workspace("run-doc")
        assert workspace["recommended_plan"]["pipeline_recommendation"] == "create_pipeline"
        assert len(workspace["promoted_document_tables"]) == 1

    def test_a_declined_source_is_left_alone(self, plane: ControlPlane) -> None:
        # `no_pipeline` is a decision about the data, not a clamp this gate
        # applied, so this must not overturn it.
        plane.store.put(
            _extraction(with_tables=True), run_id="run-none", stage_exec_id="document-extraction"
        )
        plane.store.put(
            StagingWorkspace(
                source_id="upload:documents",
                source_fingerprint="sha256:documents",
                recommended_plan=_plan("no_pipeline"),
            ),
            run_id="run-none",
            stage_exec_id="staging",
        )

        plane.review_document_tables("run-none", {"report:table:1": "rejected"})

        plan = plane.staging_workspace("run-none")["recommended_plan"]
        assert plan["pipeline_recommendation"] == "no_pipeline"

    def test_a_plan_a_person_accepted_is_left_alone(self, plane: ControlPlane) -> None:
        plane.store.put(
            _extraction(with_tables=True), run_id="run-acc", stage_exec_id="document-extraction"
        )
        plane.store.put(
            StagingWorkspace(
                source_id="upload:documents",
                source_fingerprint="sha256:documents",
                recommended_plan=RuntimeConfigurationPlan(
                    pipeline_recommendation="defer_pipeline",
                    status="accepted",
                    accepted=True,
                ),
            ),
            run_id="run-acc",
            stage_exec_id="staging",
        )

        plane.review_document_tables("run-acc", {"report:table:1": "rejected"})

        plan = plane.staging_workspace("run-acc")["recommended_plan"]
        assert plan["pipeline_recommendation"] == "defer_pipeline"
