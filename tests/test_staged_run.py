"""Staging a run, configuring it, and continuing it.

A staged run is a real run that has been asked to stop early: intake and
schema discovery execute the moment a dataset is chosen, so what the person
reads before configuring anything is what the run actually measured, and
continuing it resumes the same state rather than starting a second run.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ads.api.service import ControlPlane, _RunPauseRequested, create_app
from ads.contracts.base import ArtifactType
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration.runner import RunOutcome, RunStatus
from ads.pipeline.stages import CANDIDATE_LIMIT_KEY, STAGE_DIRECTIVES_KEY, VALIDATION_FOLDS_KEY
from ads.store import ArtifactStore


class _Recorder:
    """Stands in for the workflow runner and remembers how it was called.

    The point of these tests is which stages are asked to run and on what
    state, not what the stages compute -- executing the real pipeline would
    need Ollama and would test the pipeline instead of the staging protocol.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, spec, registry, state, **kwargs) -> RunOutcome:
        self.calls.append(
            {
                "state": state,
                "run_id": state.run_id,
                "stop_after": kwargs.get("stop_after"),
                "start_at": kwargs.get("start_at"),
                "checkpoints": list(state.profile.checkpoint_stages),
                "intent": state.user_intent,
            }
        )
        stopped = kwargs.get("stop_after")
        return RunOutcome(
            run_id=state.run_id,
            status=RunStatus.STAGED if stopped else RunStatus.COMPLETED,
            final_stage=stopped,
            decisions=[],
        )


class _PlannerLLM:
    """One local-model call that already contains English and Turkish output."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_first = fail_first

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": json_schema, "profile": profile}
        )
        if self.fail_first and len(self.calls) == 1:
            return LLMResponse(
                text='{"reply":"unfinished',
                model=profile.name,
                latency_s=0.01,
                parsed=None,
                parse_error="JSONDecodeError: unterminated string",
            )
        return LLMResponse(
            text="",
            model=profile.name,
            latency_s=0.01,
            parsed={
                "reply": "The source is ready for a bounded test run.",
                "reply_tr": "Kaynak, sınırlı bir test koşusuna hazır.",
                "reports": [
                    {
                        "title_en": "Pipeline readiness",
                        "title_tr": "Boru hattı hazırlığı",
                        "summary_en": "The measured schema is executable.",
                        "summary_tr": "Ölçülen şema çalıştırılabilir.",
                        "findings": [{"en": "Use one candidate.", "tr": "Bir aday kullanın."}],
                        "verification_questions": [],
                    }
                ],
                "configuration_patch": {"candidate_limit": 1, "n_folds": 4},
                "stage_directives": {"training": ["Keep the model search bounded."]},
                "max_retries_by_stage": {"training": 2},
                "plan_rationale": [
                    {
                        "en": "A small source needs a bounded search.",
                        "tr": "Küçük bir kaynak sınırlı arama gerektirir.",
                    }
                ],
                "pipeline_component_updates": {
                    "understand-documents": {
                        "enabled": False,
                        "settings": {"engine": "text_layer"},
                    }
                },
            },
        )


class _UnexplainedThenExplainedLLM:
    """Returns a verdict with no reason first, then a real reason on the re-roll."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.calls.append({"profile": profile})
        explained = len(self.calls) > 1
        return LLMResponse(
            text="",
            model=profile.name,
            latency_s=0.01,
            parsed={
                "reply": "Understanding is complete.",
                "pipeline_decision": "no_pipeline",
                "decision_reason_en": (
                    "No target column exists across the documents." if explained else ""
                ),
                "decision_reason_tr": "Belgelerde hedef sütun yok." if explained else "",
                "reports": [],
            },
        )


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def client(tmp_path: Path, recorder: _Recorder) -> TestClient:
    roots = tmp_path / "sources"
    demo = roots / "demo"
    demo.mkdir(parents=True)
    pd.DataFrame({"physician_id": [1, 2, 3], "tutar": [10.0, 20.0, 30.0]}).to_csv(
        demo / "table.csv", index=False
    )
    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(roots,),
        upload_root=tmp_path / "uploads",
        llm_factory=lambda: object(),  # build_full_spec wires it; nothing calls it
        workflow_runner=recorder,
    )
    client = TestClient(create_app(plane=plane))
    client.plane = plane  # type: ignore[attr-defined]
    return client


def _stage(client: TestClient) -> str:
    staged = client.post("/api/runs/staged", json={"source_id": "demo"})
    assert staged.status_code == 200, staged.text
    run_id = staged.json()["run_id"]
    _settle(client, run_id)
    return run_id


def _settle(client: TestClient, run_id: str, target: str = "staged") -> None:
    """Staging executes on a worker thread; wait for it rather than sleeping."""
    for _ in range(100):
        if client.plane._runtime_runs[run_id].status == target:  # noqa: SLF001
            return
        time.sleep(0.02)
    run = client.plane._runtime_runs[run_id]  # noqa: SLF001
    err = getattr(run, "error", None)
    status = run.status
    raise AssertionError(f"run stayed {status!r} (error: {err!r}), expected {target!r}")


class TestStagingRunsTheFirstStages:
    def test_default_pipeline_is_available_before_a_run_exists(self, client: TestClient) -> None:
        response = client.get("/api/data-sources/demo/pipeline-blueprint")

        assert response.status_code == 200, response.text
        assert response.json()["components"][0]["id"] == "data-source"

    def test_it_executes_up_to_schema_discovery_and_stops(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        """The whole point: the pipeline has really run, and only that far."""
        _stage(client)

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["stop_after"] == "schema_discovery"
        assert recorder.calls[0]["start_at"] is None

    def test_the_profile_comes_back_immediately(self, client: TestClient) -> None:
        """The screen needs something to draw before the stages finish."""
        staged = client.post("/api/runs/staged", json={"source_id": "demo"}).json()

        assert staged["status"] == "staging"
        assert staged["profile"]["tables"][0]["rows"] == 3

    def test_it_is_listed_while_it_stages(self, client: TestClient) -> None:
        run_id = _stage(client)

        listed = {run["run_id"]: run["status"] for run in client.get("/api/runs").json()}

        assert listed[run_id] == "staged"

    def test_staging_is_a_durable_artifact(self, client: TestClient) -> None:
        run_id = _stage(client)

        response = client.get(f"/api/runs/{run_id}/staging")

        assert response.status_code == 200, response.text
        workspace = response.json()
        assert workspace["artifact_id"]
        assert workspace["source_id"] == "demo"
        assert workspace["cache_reused"] is False
        assert workspace["pipeline_blueprint"]["name"]["en"].startswith("Multimodal")
        component_ids = {
            component["id"] for component in workspace["pipeline_blueprint"]["components"]
        }
        assert {
            "data-source",
            "structured-brief",
            "understanding-synthesis",
            "default-ml-pipeline",
        } <= component_ids
        assert "planner" not in component_ids

    def test_one_planner_call_persists_bilingual_reports_chat_and_plan(
        self, client: TestClient
    ) -> None:
        planner = _PlannerLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]

        run_id = _stage(client)
        workspace = client.get(f"/api/runs/{run_id}/staging").json()

        assert len(planner.calls) == 1
        assert workspace["reports"][0]["title"] == {
            "en": "Pipeline readiness",
            "tr": "Boru hattı hazırlığı",
        }
        assert workspace["chat_history"][-1]["content"]["tr"].startswith("The source")
        assert workspace["recommended_plan"]["configuration"]["candidate_limit"] == 1
        report_output = next(
            output
            for output in workspace["component_outputs"]
            if output["component_id"] == "understanding-synthesis"
            and output["port_id"] == "reports"
        )
        assert report_output["status"] == "ready"
        assert report_output["artifact_ids"]
        document = next(
            component
            for component in workspace["pipeline_blueprint"]["components"]
            if component["id"] == "understand-documents"
        )
        assert document["settings"]["engine"] == "text_layer"
        assert document["configured_by"] == "planner"
        assert "Artifacts remain bilingual" in planner.calls[0]["system"]

    def test_malformed_planner_output_is_retried_once(self, client: TestClient) -> None:
        planner = _PlannerLLM(fail_first=True)
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]

        run_id = _stage(client)
        workspace = client.get(f"/api/runs/{run_id}/staging").json()

        assert len(planner.calls) == 2
        assert workspace["planner_error"] is None
        assert workspace["reports"][0]["title"]["tr"] == "Boru hattı hazırlığı"
        assert workspace["recommended_plan"] is not None

    def test_the_verdict_call_is_seeded_for_reproducibility(self, client: TestClient) -> None:
        """#65: an unseeded call let identical inputs produce different verdicts.

        temperature=0 does not pin local inference; a seed does. This asserts the
        seed reaches the model rather than the run-to-run outcome, which needs a
        real backend to observe.
        """
        planner = _PlannerLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]

        _stage(client)

        assert planner.calls[0]["profile"].seed is not None

    def test_a_verdict_without_an_explanation_is_rejected_and_re_rolled(
        self, client: TestClient
    ) -> None:
        """#65: a model that returns a verdict but no words for it used to be
        accepted and shown as generic boilerplate. It must be re-rolled instead,
        on a different seed so the second attempt is not the same empty answer."""
        planner = _UnexplainedThenExplainedLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]

        run_id = _stage(client)
        workspace = client.get(f"/api/runs/{run_id}/staging").json()

        assert len(planner.calls) == 2, "the unexplained verdict was not re-rolled"
        assert planner.calls[0]["profile"].seed != planner.calls[1]["profile"].seed
        summary = workspace["recommended_plan"]["decision_summary"]
        assert summary["en"] == "No target column exists across the documents."
        assert workspace["recommended_plan"]["pipeline_recommendation"] == "no_pipeline"


class TestContinuingKeepsTheWork:
    def test_pause_request_is_recorded_for_the_next_stage_boundary(
        self, client: TestClient
    ) -> None:
        run_id = _stage(client)
        runtime = client.plane._runtime_runs[run_id]  # noqa: SLF001
        runtime.status = "running"
        runtime.current_stage = "exploratory_analysis"

        response = client.post(f"/api/runs/{run_id}/pause")

        assert response.status_code == 200, response.text
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        assert progress["pause_requested"] is True
        assert progress["events"][-1]["event"] == "pause_requested"
        assert progress["events"][-1]["stage"] == "exploratory_analysis"

    def test_pause_refuses_a_run_that_is_not_active(self, client: TestClient) -> None:
        run_id = _stage(client)

        response = client.post(f"/api/runs/{run_id}/pause")

        assert response.status_code == 409

    def test_pause_occurs_only_after_a_stage_that_can_proceed(self, client: TestClient) -> None:
        run_id = _stage(client)
        runtime = client.plane._runtime_runs[run_id]  # noqa: SLF001
        runtime.status = "running"
        runtime.pause_requested = True
        event = client.plane._event_recorder(runtime)  # noqa: SLF001

        event("gate_decided", {"stage": "training", "verdict": "retry"})
        with pytest.raises(_RunPauseRequested):
            event("gate_decided", {"stage": "training", "verdict": "auto_proceed"})

    def test_the_run_id_does_not_change(self, client: TestClient) -> None:
        """It used to be replaced by a freshly started run, which threw away
        the intake the person had just read and re-ran it against the same
        files."""
        run_id = _stage(client)

        started = client.post(f"/api/runs/{run_id}/start", json={})

        assert started.status_code == 200, started.text
        assert started.json()["run_id"] == run_id

    def test_it_resumes_after_the_staged_stages_on_the_same_state(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        run_id = _stage(client)

        client.post(f"/api/runs/{run_id}/start", json={})
        _settle(client, run_id, target="completed")

        assert recorder.calls[1]["start_at"] == "integration"
        assert recorder.calls[1]["state"] is recorder.calls[0]["state"], (
            "continuing must reuse the staged state, or the artifacts it produced are recomputed"
        )

    def test_manual_mode_makes_every_stage_a_checkpoint(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        run_id = _stage(client)

        client.post(f"/api/runs/{run_id}/start", json={"run_mode": "manual"})
        _settle(client, run_id, target="completed")

        checkpoints = recorder.calls[1]["checkpoints"]
        assert {"training", "evaluation", "feature_pipeline"} <= set(checkpoints)

    def test_problem_discovery_is_always_a_checkpoint(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        """Nobody has stated a problem at this point -- the agent picks it two
        stages later -- so that choice is never made unseen."""
        run_id = _stage(client)

        client.post(f"/api/runs/{run_id}/start", json={"run_mode": "auto"})
        _settle(client, run_id, target="completed")

        assert "problem_discovery" in recorder.calls[1]["checkpoints"]

    def test_fully_auto_applies_an_explicitly_accepted_planner_plan(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        planner = _PlannerLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]
        run_id = _stage(client)

        proposal = client.get(f"/api/runs/{run_id}/staging").json()
        accepted = client.post(
            f"/api/runs/{run_id}/staging/plan/accept",
            json={"base_artifact_id": proposal["artifact_id"]},
        )
        assert accepted.status_code == 200, accepted.text
        # #166: the editor keeps the run selected by reading run_id off the accept
        # response. StagingWorkspace has no run_id field, so without an explicit
        # one here the response omitted it, the URL became run=undefined, and the
        # screen fell back to the empty upload state -- "accepting the plan lands
        # somewhere unrelated". The GET /staging response has always carried it;
        # accept must return the same run_id, not None.
        assert accepted.json()["run_id"] == run_id

        started = client.post(f"/api/runs/{run_id}/start", json={"run_mode": "fully_auto"})
        assert started.status_code == 200, started.text
        _settle(client, run_id, target="completed")

        state = recorder.calls[1]["state"]
        assert "problem_discovery" not in recorder.calls[1]["checkpoints"]
        assert state.blackboard[CANDIDATE_LIMIT_KEY] == 1
        assert state.blackboard[VALIDATION_FOLDS_KEY] == 4
        assert state.blackboard[STAGE_DIRECTIVES_KEY]["training"] == [
            "Keep the model search bounded."
        ]
        workspace = client.get(f"/api/runs/{run_id}/staging").json()
        assert workspace["recommended_plan"]["accepted"] is True
        execution_plan = client.plane.store.latest(  # type: ignore[attr-defined]
            run_id, ArtifactType.AUTOMATION_EXECUTION_PLAN
        )
        assert execution_plan is not None

    def test_fully_auto_refuses_a_proposal_that_the_human_has_not_accepted(
        self, client: TestClient
    ) -> None:
        planner = _PlannerLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]
        run_id = _stage(client)

        started = client.post(f"/api/runs/{run_id}/start", json={"run_mode": "fully_auto"})

        assert started.status_code == 400
        assert "accept the Planner proposal" in started.json()["detail"]

    def test_fully_auto_refuses_an_enabled_document_component_without_outputs(
        self, client: TestClient
    ) -> None:
        planner = _PlannerLLM()
        client.plane.llm_factory = lambda: planner  # type: ignore[attr-defined]
        run_id = _stage(client)
        workspace = client.get(f"/api/runs/{run_id}/staging").json()
        document = next(
            component
            for component in workspace["pipeline_blueprint"]["components"]
            if component["id"] == "understand-documents"
        )
        document["enabled"] = True
        updated = client.put(
            f"/api/runs/{run_id}/staging/pipeline",
            json={
                "base_artifact_id": workspace["artifact_id"],
                "blueprint": workspace["pipeline_blueprint"],
            },
        )
        assert updated.status_code == 200, updated.text

        accepted = client.post(
            f"/api/runs/{run_id}/staging/plan/accept",
            json={"base_artifact_id": updated.json()["artifact_id"]},
        )
        assert accepted.status_code == 200, accepted.text

        started = client.post(f"/api/runs/{run_id}/start", json={"run_mode": "fully_auto"})

        assert started.status_code == 400
        assert "document understanding outputs are not ready" in started.json()["detail"]

    def test_what_was_configured_reaches_the_agent_as_preference(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        run_id = _stage(client)

        client.post(f"/api/runs/{run_id}/start", json={"target_column": "tutar"})
        _settle(client, run_id, target="completed")

        assert "tutar" in (recorder.calls[1]["intent"] or "")

    def test_an_unknown_run_mode_is_refused(self, client: TestClient) -> None:
        run_id = _stage(client)

        refused = client.post(f"/api/runs/{run_id}/start", json={"run_mode": "yolo"})

        assert refused.status_code == 400


class TestConfiguringAndDiscarding:
    def test_manual_layout_is_durable_and_does_not_change_execution_fingerprint(
        self, client: TestClient
    ) -> None:
        run_id = _stage(client)
        original = client.get(f"/api/runs/{run_id}/staging").json()
        before = client.post(f"/api/runs/{run_id}/automation/compile").json()

        response = client.put(
            f"/api/runs/{run_id}/staging/layout",
            json={
                "base_artifact_id": original["artifact_id"],
                "layout": {
                    "nodes": [
                        {
                            "component_id": "data-source",
                            "x": 84.5,
                            "y": 210.0,
                            "collapsed": True,
                        }
                    ],
                    "collapsed_branches": ["cost"],
                },
            },
        )

        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["pipeline_layout"]["nodes"][0]["x"] == 84.5
        after = client.post(f"/api/runs/{run_id}/automation/compile").json()
        assert before["plan"]["blueprint_fingerprint"] == after["plan"]["blueprint_fingerprint"]

    def test_layout_save_rejects_a_stale_workspace_revision(self, client: TestClient) -> None:
        run_id = _stage(client)
        original = client.get(f"/api/runs/{run_id}/staging").json()
        body = {
            "base_artifact_id": original["artifact_id"],
            "layout": {
                "nodes": [
                    {"component_id": "data-source", "x": 10, "y": 20, "collapsed": True}
                ],
                "collapsed_branches": [],
            },
        }
        assert client.put(f"/api/runs/{run_id}/staging/layout", json=body).status_code == 200

        stale = client.put(f"/api/runs/{run_id}/staging/layout", json=body)

        assert stale.status_code == 400
        assert "changed" in stale.json()["detail"]

    def test_pipeline_preferences_are_persisted_as_a_new_staging_snapshot(
        self, client: TestClient
    ) -> None:
        run_id = _stage(client)
        original = client.get(f"/api/runs/{run_id}/staging").json()
        blueprint = original["pipeline_blueprint"]
        document = next(
            component
            for component in blueprint["components"]
            if component["id"] == "understand-documents"
        )
        document["settings"]["engine"] = "unstructured"
        document["enabled"] = True

        response = client.put(
            f"/api/runs/{run_id}/staging/pipeline",
            json={"base_artifact_id": original["artifact_id"], "blueprint": blueprint},
        )

        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["artifact_id"] != original["artifact_id"]
        document = next(
            component
            for component in saved["pipeline_blueprint"]["components"]
            if component["id"] == "understand-documents"
        )
        assert document["settings"]["engine"] == "unstructured"
        assert document["configured_by"] == "human"

    def test_pipeline_rejects_an_edge_whose_output_type_does_not_match_input(
        self, client: TestClient
    ) -> None:
        run_id = _stage(client)
        blueprint = client.get(f"/api/runs/{run_id}/staging").json()["pipeline_blueprint"]
        blueprint["connections"].append(
            {
                "id": "bad-edge",
                "source_component": "data-source",
                "source_port": "documents",
                "target_component": "intake",
                "target_port": "structured_files",
            }
        )

        original = client.get(f"/api/runs/{run_id}/staging").json()
        response = client.put(
            f"/api/runs/{run_id}/staging/pipeline",
            json={"base_artifact_id": original["artifact_id"], "blueprint": blueprint},
        )

        assert response.status_code == 400
        assert "incompatible" in response.json()["detail"]

    def test_pipeline_edit_refuses_to_overwrite_a_newer_staging_snapshot(
        self, client: TestClient
    ) -> None:
        run_id = _stage(client)
        original = client.get(f"/api/runs/{run_id}/staging").json()
        document = next(
            component
            for component in original["pipeline_blueprint"]["components"]
            if component["id"] == "understand-documents"
        )
        document["settings"]["engine"] = "unstructured"
        body = {
            "base_artifact_id": original["artifact_id"],
            "blueprint": original["pipeline_blueprint"],
        }
        first = client.put(f"/api/runs/{run_id}/staging/pipeline", json=body)
        assert first.status_code == 200, first.text

        stale = client.put(f"/api/runs/{run_id}/staging/pipeline", json=body)

        assert stale.status_code == 400
        assert "changed" in stale.json()["detail"]

    def test_the_configuration_can_be_amended_before_it_starts(self, client: TestClient) -> None:
        run_id = _stage(client)

        updated = client.patch(f"/api/runs/{run_id}/staged", json={"target_column": "tutar"})

        assert updated.status_code == 200, updated.text
        assert updated.json()["configuration"]["target_column"] == "tutar"
        assert updated.json()["configuration"]["source_id"] == "demo"

    def test_discarding_removes_it_from_the_list(self, client: TestClient) -> None:
        """Popping the in-memory copy is not enough; the snapshot brings it
        back. Seen on the deployment before this was fixed: the API answered
        `discarded` and the run was still listed as staged."""
        run_id = _stage(client)

        discarded = client.post(f"/api/runs/{run_id}/discard")

        assert discarded.status_code == 200, discarded.text
        assert [r["run_id"] for r in client.get("/api/runs").json()] == []

    def test_discard_is_refused_for_a_run_that_is_not_staged(self, client: TestClient) -> None:
        """Otherwise discard answers `discarded` for a run it never touched."""
        run_id = _stage(client)
        client.plane._runtime_runs[run_id].status = "running"  # type: ignore[attr-defined]  # noqa: SLF001

        refused = client.post(f"/api/runs/{run_id}/discard")

        assert refused.status_code == 409
        assert "not staged" in refused.json()["detail"]

    def test_amending_a_discarded_run_is_refused(self, client: TestClient) -> None:
        run_id = _stage(client)
        client.post(f"/api/runs/{run_id}/discard")

        refused = client.patch(f"/api/runs/{run_id}/staged", json={"target_column": "x"})

        assert refused.status_code == 400


def test_the_app_serves_datasets_from_the_roots_it_was_given(tmp_path: Path) -> None:
    """Sources default to `data/` relative to the working directory.

    A server launched from a deployment checkout therefore looked for datasets
    inside that checkout, where `data/` is gitignored and absent, and answered
    with an empty list and no error to explain it. Observed on the deployment:
    every dataset endpoint returned `[]` while the data sat one directory away.
    """
    roots = tmp_path / "elsewhere"
    (roots / "demo").mkdir(parents=True)
    pd.DataFrame({"a": [1, 2]}).to_csv(roots / "demo" / "t.csv", index=False)

    app = create_app(tmp_path / "artifacts", source_roots=[roots])
    listed = TestClient(app).get("/api/data-sources").json()

    assert [entry["source_id"] for entry in listed] == ["demo"]


class TestTheRunListDescribesTheRun:
    def test_the_summary_names_the_dataset(self, client: TestClient) -> None:
        """The frontend read `dataset` off this object from the day it was
        written and nothing ever put one there, so every screen that needed to
        know which source a run was working on received undefined."""
        run_id = _stage(client)

        summary = next(r for r in client.get("/api/runs").json() if r["run_id"] == run_id)

        assert summary["source_id"] == "demo"
        assert summary["dataset"] == "demo"

    def test_a_staged_run_draws_the_agent_pipeline(self, client: TestClient) -> None:
        """Its configuration has no mode yet -- that is chosen on this screen --
        and defaulting to manual drew the nine-stage deterministic pipeline for
        a run that had already executed schema discovery, a stage that pipeline
        does not contain."""
        run_id = _stage(client)

        graph = client.get(f"/api/workflow?run_id={run_id}").json()

        ids = [node["id"] for node in graph["nodes"]]
        assert "schema_discovery" in ids
        assert "problem_discovery" in ids


def test_a_staged_run_records_that_it_is_an_agent_run(client: TestClient) -> None:
    """Which spec a run executes has to be a recorded fact, not an inference.

    Staging builds the agent spec. Leaving the mode unset meant every reader
    defaulted to the nine-stage deterministic pipeline, so the rail drew the
    wrong graph and the stage endpoint answered 404 for schema discovery --
    the stage the run had just executed and the person was looking at.
    """
    run_id = _stage(client)

    configuration = client.plane._runtime_runs[run_id].configuration  # noqa: SLF001

    assert configuration["mode"] == "agent"


def test_the_stage_endpoint_finds_an_agent_only_stage(client: TestClient) -> None:
    run_id = _stage(client)

    detail = client.get(f"/api/runs/{run_id}/stages/schema_discovery")

    assert detail.status_code == 200, detail.text


def test_a_run_that_stopped_to_ask_carries_the_question(client: TestClient) -> None:
    """`progress` is what the run screen polls.

    The escalation lived only on the in-memory outcome, so a run in
    `awaiting_human` reported that status and nothing else -- the screen knew
    the run had stopped for a person and had no question to put on it.
    """
    from ads.contracts.gates import (  # noqa: PLC0415
        DecisionOption,
        GateDecision,
        GateVerdict,
        HumanPrompt,
    )

    run_id = _stage(client)
    runtime = client.plane._runtime_runs[run_id]  # noqa: SLF001
    decision = GateDecision(
        stage_id="schema_discovery",
        attempt=2,
        verdict=GateVerdict.ESCALATE,
        reason_code="repeated_identical_failure",
        human_prompt=HumanPrompt(
            stage_id="schema_discovery",
            question="The plan failed the same checks twice. What now?",
            context_summary="Attempt 2 failed on exactly the criteria attempt 1 did.",
            options=[
                DecisionOption(
                    option_id="retry",
                    label="Send back for rework",
                    consequence="The stage runs again.",
                    recommended=True,
                ),
                DecisionOption(
                    option_id="abort",
                    label="Stop the run",
                    consequence="Nothing further executes.",
                ),
            ],
        ),
    )
    runtime.outcome = RunOutcome(
        run_id=run_id,
        status=RunStatus.AWAITING_HUMAN,
        final_stage="schema_discovery",
        decisions=[decision],
    )

    progress = client.get(f"/api/runs/{run_id}/progress").json()

    assert progress["pending_question"] is not None
    assert progress["pending_question"]["human_prompt"]["options"][0]["option_id"] == "retry"


class TestStagedRunCaching:
    def test_repeat_staging_on_same_source_reuses_cache(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        """Staging a second run for the same dataset reuses the first two stages."""
        run1 = _stage(client)
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["stop_after"] == "schema_discovery"

        # Stage a second run for the same source
        staged2 = client.post("/api/runs/staged", json={"source_id": "demo", "reuse_cache": True})
        assert staged2.status_code == 200, staged2.text
        run2 = staged2.json()["run_id"]
        assert run2 != run1
        _settle(client, run2, target="staged")

        # The runner should NOT have been invoked a second time for staging
        assert len(recorder.calls) == 1

        # Continuing run2 resumes at integration
        client.post(f"/api/runs/{run2}/start", json={})
        _settle(client, run2, target="completed")
        assert len(recorder.calls) == 2
        assert recorder.calls[1]["run_id"] == run2
        assert recorder.calls[1]["start_at"] == "integration"

    def test_repeat_staging_reruns_by_default(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        """A cached understanding is an explicit choice, never a hidden shortcut."""
        _stage(client)
        second = client.post("/api/runs/staged", json={"source_id": "demo"})
        assert second.status_code == 200, second.text
        _settle(client, second.json()["run_id"], target="staged")

        assert len(recorder.calls) == 2

    def test_file_modification_invalidates_staged_cache(
        self, client: TestClient, recorder: _Recorder
    ) -> None:
        """Modifying a source file forces staging to run again."""
        run1 = _stage(client)
        assert len(recorder.calls) == 1

        # Modify the source data
        source_dir = client.plane.source_path("demo")  # type: ignore[attr-defined]
        pd.DataFrame({"physician_id": [1, 2, 3, 4], "tutar": [10.0, 20.0, 30.0, 40.0]}).to_csv(
            source_dir / "table.csv", index=False
        )

        # Stage again: cache is invalidated, so runner executes staging again
        run2 = _stage(client)
        assert run2 != run1
        assert len(recorder.calls) == 2
        assert recorder.calls[1]["stop_after"] == "schema_discovery"
        assert recorder.calls[1]["run_id"] == run2
