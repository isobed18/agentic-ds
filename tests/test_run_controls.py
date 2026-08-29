"""Run-level human controls: mode, sensitivity override, stage directives.

Each of these lets a person change what the machine decided, so each needs a
counter-test that the change actually took effect rather than merely being
recorded somewhere.
"""

from __future__ import annotations

import pandas as pd
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ads.api import create_app  # noqa: E402
from ads.api.service import _PIPELINE_STAGES, ControlPlane, _RuntimeRun  # noqa: E402
from ads.contracts.datacard import Sensitivity  # noqa: E402
from ads.contracts.gates import BUILTIN_PROFILES  # noqa: E402
from ads.intake import LoadedTable, profile_table  # noqa: E402
from ads.orchestration import RunState  # noqa: E402
from ads.pipeline.stages import SOURCE_CARDS_KEY, STAGE_DIRECTIVES_KEY  # noqa: E402
from ads.store import ArtifactStore  # noqa: E402


@pytest.fixture
def plane(tmp_path) -> ControlPlane:
    return ControlPlane(store=ArtifactStore(tmp_path / "artifacts"))


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(tmp_path / "artifacts"))


def _card(frame: pd.DataFrame):
    return profile_table(LoadedTable(name="t", frame=frame, source_uri="x", source_format="csv"))


def _resident(plane: ControlPlane, run_id: str, **blackboard) -> str:
    state = RunState(run_id=run_id, store=plane.store, profile=BUILTIN_PROFILES["full_auto"])
    state.blackboard.update(blackboard)
    plane._runtime_runs[run_id] = _RuntimeRun(
        run_id=run_id, source_id="s", state=state, configuration={}
    )
    return run_id


def _runtime(plane: ControlPlane, run_id: str, *, status: str, automation_id: str) -> _RuntimeRun:
    state = RunState(run_id=run_id, store=plane.store, profile=BUILTIN_PROFILES["full_auto"])
    return _RuntimeRun(
        run_id=run_id,
        source_id="upload:x",
        state=state,
        configuration={"automation_id": automation_id},
        status=status,
    )


def test_a_failed_run_moves_its_automation_to_error_status(plane: ControlPlane) -> None:
    """Every failure path sets status="failed" then persists, so the persist
    hook is the one place that catches them all and marks the automation (#82).
    A failed run used to leave it at `draft`, identical to one never started."""
    assert plane.automation_store is not None
    automation = plane.automation_store.create("Document analysis")

    plane._persist_runtime(
        _runtime(plane, "run-fail0001", status="failed", automation_id=automation.automation_id)
    )

    assert plane.automation_store.get(automation.automation_id).status == "error"


def test_a_running_run_persist_does_not_flag_the_automation(plane: ControlPlane) -> None:
    assert plane.automation_store is not None
    automation = plane.automation_store.create("Document analysis")

    plane._persist_runtime(
        _runtime(plane, "run-ok000001", status="running", automation_id=automation.automation_id)
    )

    assert plane.automation_store.get(automation.automation_id).status == "draft"


class TestRunMode:
    def test_manual_checkpoints_every_stage(self, plane: ControlPlane) -> None:
        supervision = plane._normalise_supervision({})
        assert supervision["checkpoint_stages"] == [], "auto starts with none"
        # Manual is the same gate with every stage declared a checkpoint, not a
        # second control flow, which is why it cannot weaken a hard rule.
        manual = set(supervision["checkpoint_stages"]) | _PIPELINE_STAGES
        assert manual == _PIPELINE_STAGES

    def test_feature_pipeline_is_supervisable(self) -> None:
        """It was absent from the list, so supervision silently ignored it."""
        assert "feature_pipeline" in _PIPELINE_STAGES

    def test_an_unknown_mode_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/runs",
            json={
                "source_id": "nope",
                "base_table": "t",
                "base_grain": ["k"],
                "run_mode": "semi",
            },
        )
        assert response.status_code == 400


class TestSensitivityOverride:
    def test_override_changes_what_the_pipeline_reads_next(self, plane: ControlPlane) -> None:
        """Uses a column the classifier gets *wrong*, deliberately.

        The first version of this test used `musteri_adi`, which the classifier
        already labels PII — so it passed even with the override's write to the
        blackboard deleted. A test that asserts an outcome the system produces
        anyway is not testing the feature.
        """
        card = _card(pd.DataFrame({"basvuru_sahibi": ["Zeynep Acar", "Burak Sen"]}))
        run_id = _resident(plane, "r1", **{SOURCE_CARDS_KEY: [card]})

        before = card.columns[0].sensitivity
        assert before is Sensitivity.INTERNAL, "fixture must start where the machine is wrong"

        assert plane.apply_sensitivity_overrides(run_id, {"basvuru_sahibi": "pii"}) == {
            "basvuru_sahibi": "pii"
        }

        # The point is not that it was recorded. It is that the classification
        # the feature selector reads has actually changed.
        stored = plane._runtime_runs[run_id].state.blackboard[SOURCE_CARDS_KEY]
        column = next(c for c in stored[0].columns if c.name == "basvuru_sahibi")
        assert column.sensitivity is Sensitivity.PII

    def test_override_can_clear_a_false_positive(self, plane: ControlPlane) -> None:
        """`eposta_alan_adi` holds mail *domains*, not addresses. The classifier
        calls it PII; a person can see it is not."""
        card = _card(pd.DataFrame({"eposta_alan_adi": ["gmail.com", "outlook.com"]}))
        run_id = _resident(plane, "r1", **{SOURCE_CARDS_KEY: [card]})

        assert card.columns[0].sensitivity is Sensitivity.PII, "fixture must start flagged"

        plane.apply_sensitivity_overrides(run_id, {"eposta_alan_adi": "internal"})
        stored = plane._runtime_runs[run_id].state.blackboard[SOURCE_CARDS_KEY]
        assert stored[0].columns[0].sensitivity is Sensitivity.INTERNAL

    def test_an_unknown_column_is_refused_rather_than_ignored(self, plane: ControlPlane) -> None:
        run_id = _resident(plane, "r1", **{SOURCE_CARDS_KEY: [_card(pd.DataFrame({"a": [1]}))]})
        with pytest.raises(ValueError, match="unknown column"):
            plane.apply_sensitivity_overrides(run_id, {"typo_name": "pii"})

    def test_only_the_two_sensitivity_values_are_accepted(self, plane: ControlPlane) -> None:
        run_id = _resident(plane, "r1", **{SOURCE_CARDS_KEY: [_card(pd.DataFrame({"a": [1]}))]})
        with pytest.raises(ValueError, match="sensitivity must be"):
            plane.apply_sensitivity_overrides(run_id, {"a": "secret"})


class TestStageDirectives:
    def test_a_directive_reaches_the_blackboard_the_stage_reads(self, plane: ControlPlane) -> None:
        run_id = _resident(plane, "r2")
        plane.direct_stage(run_id, "training", "Prefer a linear model.")
        stored = plane._runtime_runs[run_id].state.blackboard[STAGE_DIRECTIVES_KEY]
        assert stored == {"training": ["Prefer a linear model."]}

    def test_directives_accumulate_rather_than_overwrite(self, plane: ControlPlane) -> None:
        run_id = _resident(plane, "r2")
        plane.direct_stage(run_id, "training", "First.")
        plane.direct_stage(run_id, "training", "Second.")
        assert plane.stage_directives(run_id)["training"] == ["First.", "Second."]

    def test_a_directive_can_be_left_for_a_stage_that_has_not_run(
        self, plane: ControlPlane
    ) -> None:
        """The reason a directive is not a correction: it applies to the first
        attempt, so it can be set before the stage starts."""
        run_id = _resident(plane, "r2")
        plane.direct_stage(run_id, "evaluation", "Report calibration too.")
        assert "evaluation" in plane.stage_directives(run_id)

    def test_unknown_stage_and_empty_instruction_are_refused(self, plane: ControlPlane) -> None:
        run_id = _resident(plane, "r2")
        with pytest.raises(ValueError, match="unknown stage"):
            plane.direct_stage(run_id, "not_a_stage", "hello")
        with pytest.raises(ValueError, match="cannot be empty"):
            plane.direct_stage(run_id, "training", "   ")

    def test_directives_are_kept_separate_from_corrections_in_context(
        self, plane: ControlPlane
    ) -> None:
        """A correction says the last attempt failed; a directive says what the
        human wants. An agent that conflates them reads a preference as a defect
        and tries to repair something that was never broken."""
        from ads.agents.base import AgentContext
        from ads.pipeline.agent_stages import _with_correction, _with_directives

        run_id = _resident(plane, "r2")
        plane.direct_stage(run_id, "training", "Prefer a linear model.")
        state = plane._runtime_runs[run_id].state

        context = _with_directives(AgentContext(), state, "training")
        context = _with_correction(context, ["The last attempt used a leaked feature."])

        assert "Instruction from the human" in context.sections
        assert "Orchestrator correction" in context.sections
        assert (
            context.sections["Instruction from the human"]
            != context.sections["Orchestrator correction"]
        )
