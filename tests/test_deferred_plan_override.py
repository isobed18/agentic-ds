"""Answering a deferred plan by naming the target (#429).

The Planner returned `defer_pipeline` on a file whose target is measurably
viable -- `bank.csv`'s `y`, with no blocking reasons and not even an imbalance
warning -- and the panel that reported it offered nothing to do about it. No
way to perform the review it named, no way to name the target, no way to
proceed. Its own closing line said "the override is the Planner", but the
Planner is a chat box, not an override control.

The review it named is not a thing this product can be asked to do either: the
prompt invites one ("a named review or missing fact must resolve viability")
and nothing consumes the name. The only human review implemented is the PDF
table review, which does not apply to a single CSV.

So the override is naming the target -- which is also the only shape of
override that works, because a deferred plan is persisted with an empty
configuration and accepting one as-is yields no base table, grain, candidate
limit or folds.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts.staging import RuntimeConfigurationPlan, StagingWorkspace
from ads.store import ArtifactStore

#: Shaped like the reported file: a two-class subscription flag with a healthy
#: minority, a free-text note, and enough rows to clear the support floors.
ROWS = 400


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": range(1, ROWS + 1),
            "age": [20 + (index % 45) for index in range(ROWS)],
            "balance": [100.0 + index * 3.5 for index in range(ROWS)],
            "job": ["admin", "technician", "services", "retired"] * (ROWS // 4),
            "note": [f"contacted on cycle {index} about the term deposit" for index in range(ROWS)],
            "y": [1 if index % 5 == 0 else 0 for index in range(ROWS)],
        }
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    roots = tmp_path / "sources"
    bank = roots / "bank"
    bank.mkdir(parents=True)
    _frame().to_csv(bank / "bank.csv", index=False)
    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(roots,),
        upload_root=tmp_path / "uploads",
    )
    client = TestClient(create_app(plane=plane))
    client.plane = plane  # type: ignore[attr-defined]
    return client


def _deferred(client: TestClient, run_id: str = "run-bank") -> str:
    """A workspace in the exact state the issue reports: deferred, no configuration.

    #430 (merged separately) re-checks a `defer_pipeline` whose precondition it
    cannot observe resolving and treats it as not a block -- which is correct
    for a legacy snapshot predating `deferred_on`, but this fixture models a
    live, first-turn deferral instead: the planner named no recommendation, so
    the precondition is `planner_decision`, exactly what a first turn actually
    persists (see `_resolved_pipeline_recommendation`). That precondition only
    lifts on a planner turn that states one, so it stays deferred here until
    this file's override endpoint decides it -- unlike the empty string, which
    #430 would resolve to `create_pipeline` on the very next read regardless of
    what this endpoint does.
    """
    reference = client.plane.store.put(  # type: ignore[attr-defined]
        StagingWorkspace(
            source_id="bank",
            source_fingerprint="sha256:bank",
            recommended_plan=RuntimeConfigurationPlan(
                pipeline_recommendation="defer_pipeline",
                deferred_on="planner_decision",
                configuration={},
            ),
        ),
        run_id=run_id,
        stage_exec_id="staging",
    )
    return reference.artifact_id


class TestWhichColumnsAreOffered:
    def test_the_plausible_targets_are_marked(self, client: TestClient) -> None:
        # `is_usable_target` already knew `y` was one; the panel had no way to
        # show it.
        _deferred(client)

        columns = client.get("/api/runs/run-bank/staging/plan/targets").json()["columns"]
        by_name = {column["name"]: column for column in columns}

        assert by_name["y"]["candidate_target"] is True
        assert by_name["client_id"]["candidate_target"] is False, "an identifier is not a target"

    def test_the_names_are_the_ones_the_pipeline_will_see(self, client: TestClient) -> None:
        # The profiler snake-cases headers, so a picker offering the original
        # casing would produce a target `validate_targets_exist` rejects as an
        # unknown column.
        _deferred(client)

        columns = client.get("/api/runs/run-bank/staging/plan/targets").json()["columns"]

        assert all(name == name.lower() for name in (column["name"] for column in columns))

    def test_a_run_with_no_workspace_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/runs/nope/staging/plan/targets").status_code == 404


class TestNamingTheTarget:
    def test_a_viable_target_lifts_the_deferral(self, client: TestClient) -> None:
        base = _deferred(client)

        result = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        )

        assert result.status_code == 200, result.text
        body = result.json()
        assert body["viable"] is True
        assert body["blocking_reasons"] == []
        assert body["recommended_plan"]["pipeline_recommendation"] == "create_pipeline"

    def test_the_lifted_plan_carries_the_configuration_a_run_needs(
        self, client: TestClient
    ) -> None:
        # The caveat that makes the target picker the right override: a
        # deferred plan is persisted with `{}`, so accepting one as-is yields
        # no base table, grain, candidate limit or folds.
        base = _deferred(client)

        configuration = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        ).json()["recommended_plan"]["configuration"]

        assert configuration["base_table"] == "bank"
        assert configuration["target_column"] == "y"
        assert configuration["candidate_limit"] == 2
        assert configuration["n_folds"] == 5

    def test_the_named_target_is_a_constraint_not_a_hint(self, client: TestClient) -> None:
        # It travels as `problem_selection`, which `problem_discovery` builds
        # and measures directly (#241) rather than handing the agent prose to
        # rank first and possibly ignore.
        base = _deferred(client)

        configuration = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        ).json()["recommended_plan"]["configuration"]

        assert configuration["problem_selection"] == {
            "kind": "predict_column",
            "target_column": "y",
        }

    def test_the_task_type_is_read_off_the_column(self, client: TestClient) -> None:
        base = _deferred(client)

        body = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        ).json()

        assert body["task_type"] == "binary_classification"

    def test_a_stated_task_type_wins(self, client: TestClient) -> None:
        base = _deferred(client)

        body = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={
                "base_artifact_id": base,
                "target_column": "age",
                "task_type": "regression",
            },
        ).json()

        assert body["task_type"] == "regression"
        assert body["viable"] is True

    def test_the_override_is_on_the_record_with_its_evidence(self, client: TestClient) -> None:
        # An override is a person's decision about the agent's recommendation,
        # so it is kept beside it: `decision_summary` still says what the
        # Planner wanted, and this says what was done instead and on what.
        base = _deferred(client)

        plan = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        ).json()["recommended_plan"]

        assert plan["human_override"] is not None
        assert "y" in plan["human_override"]["en"]
        assert str(ROWS) in plan["human_override"]["en"]
        assert plan["human_override"]["tr"] != plan["human_override"]["en"]

    def test_unblocking_and_accepting_stay_two_decisions(self, client: TestClient) -> None:
        base = _deferred(client)

        plan = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "y"},
        ).json()["recommended_plan"]

        assert plan["status"] == "proposed"
        assert plan["accepted"] is False


class TestWhenTheColumnCannotCarryTheTask:
    def test_the_measured_reasons_come_back_instead_of_a_bare_refusal(
        self, client: TestClient
    ) -> None:
        # The answer a reader can act on. `compute_support` already computes
        # it; this is what surfaces it at the moment they chose the column.
        base = _deferred(client)

        body = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={
                "base_artifact_id": base,
                "target_column": "note",
                "task_type": "regression",
            },
        ).json()

        assert body["viable"] is False
        assert body["blocking_reasons"], "an unviable column must say why"
        assert any("note" in reason for reason in body["blocking_reasons"])

    def test_the_plan_is_left_exactly_as_it_was(self, client: TestClient) -> None:
        base = _deferred(client)

        client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={
                "base_artifact_id": base,
                "target_column": "note",
                "task_type": "regression",
            },
        )

        plan = client.get("/api/runs/run-bank/staging").json()["recommended_plan"]
        assert plan["pipeline_recommendation"] == "defer_pipeline"
        assert plan["human_override"] is None

    def test_a_column_the_table_does_not_have_is_refused(self, client: TestClient) -> None:
        base = _deferred(client)

        refused = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": "no_such_column"},
        )

        assert refused.status_code == 400

    def test_anomaly_detection_cannot_claim_a_target(self, client: TestClient) -> None:
        base = _deferred(client)

        refused = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={
                "base_artifact_id": base,
                "target_column": "y",
                "task_type": "anomaly_detection",
            },
        )

        assert refused.status_code == 400


class TestWhatCannotBeOverridden:
    def test_a_plan_that_already_proposes_a_pipeline_has_nothing_to_override(
        self, client: TestClient
    ) -> None:
        reference = client.plane.store.put(  # type: ignore[attr-defined]
            StagingWorkspace(
                source_id="bank",
                source_fingerprint="sha256:bank",
                recommended_plan=RuntimeConfigurationPlan(
                    pipeline_recommendation="create_pipeline"
                ),
            ),
            run_id="run-ready",
            stage_exec_id="staging",
        )

        refused = client.post(
            "/api/runs/run-ready/staging/plan/override",
            json={"base_artifact_id": reference.artifact_id, "target_column": "y"},
        )

        assert refused.status_code == 400

    def test_a_plan_a_person_already_accepted_is_not_re_decided(
        self, client: TestClient
    ) -> None:
        reference = client.plane.store.put(  # type: ignore[attr-defined]
            StagingWorkspace(
                source_id="bank",
                source_fingerprint="sha256:bank",
                recommended_plan=RuntimeConfigurationPlan(
                    pipeline_recommendation="defer_pipeline",
                    status="accepted",
                    accepted=True,
                ),
            ),
            run_id="run-done",
            stage_exec_id="staging",
        )

        refused = client.post(
            "/api/runs/run-done/staging/plan/override",
            json={"base_artifact_id": reference.artifact_id, "target_column": "y"},
        )

        assert refused.status_code == 400

    def test_a_stale_snapshot_is_a_conflict_not_a_bad_request(self, client: TestClient) -> None:
        # The client polls the workspace, so its base artifact id is a moving
        # value and a snapshot written in between invalidates it. That is
        # nobody's mistake and the same request against the current snapshot
        # succeeds -- so it is recoverable, and says so.
        _deferred(client)

        conflict = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": "sha256:stale", "target_column": "y"},
        )

        assert conflict.status_code == 409

    def test_naming_no_column_is_refused(self, client: TestClient) -> None:
        base = _deferred(client)

        refused = client.post(
            "/api/runs/run-bank/staging/plan/override",
            json={"base_artifact_id": base, "target_column": ""},
        )

        assert refused.status_code == 400


class TestNoPipelineIsOverridableToo:
    def test_a_declined_source_can_still_be_aimed_at_a_column(self, client: TestClient) -> None:
        """`no_pipeline` is the stronger claim, but leaving it unanswerable is
        the same dead end: the measurement is what should settle it."""
        reference = client.plane.store.put(  # type: ignore[attr-defined]
            StagingWorkspace(
                source_id="bank",
                source_fingerprint="sha256:bank",
                recommended_plan=RuntimeConfigurationPlan(
                    pipeline_recommendation="no_pipeline", configuration={}
                ),
            ),
            run_id="run-declined",
            stage_exec_id="staging",
        )

        body = client.post(
            "/api/runs/run-declined/staging/plan/override",
            json={"base_artifact_id": reference.artifact_id, "target_column": "y"},
        ).json()

        assert body["viable"] is True
        assert body["recommended_plan"]["pipeline_recommendation"] == "create_pipeline"
