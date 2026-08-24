"""Focused tests for the fixed workflow UI's executable vertical slice."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts import Metric, ProblemDefinition, TaskType
from ads.contracts.datacard import NumericStats
from ads.contracts.eda import (
    CorrelationMatrix,
    DistributionKind,
    EDAReport,
    MissingnessSummary,
    OutlierSummary,
    TargetDistribution,
    TargetRelationship,
)
from ads.contracts.gates import DecisionOption, GateDecision, GateVerdict, HumanPrompt
from ads.llm import LLMResponse, ModelProfile
from ads.store import ArtifactStore


class _PlannerFakeLLM:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema})
        return LLMResponse(
            text="",
            model=profile.name,
            latency_s=0.01,
            parsed=self.result,
        )


def _plane(tmp_path: Path) -> ControlPlane:
    source_root = tmp_path / "sources"
    source = source_root / "safe-demo"
    source.mkdir(parents=True)
    (source / "customers.csv").write_text(
        "customer_id,email,age,churned\n"
        "1,secret-one@example.test,31,0\n"
        "2,secret-two@example.test,48,1\n"
        "3,secret-three@example.test,39,0\n",
        encoding="utf-8",
    )
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(source_root,),
        upload_root=tmp_path / "uploads",
    )


def _configuration() -> dict:
    return {
        "source_id": "safe-demo",
        "base_table": "customers",
        "base_grain": ["customer_id"],
        "target_column": "churned",
        "task_type": "binary_classification",
        "primary_metric": "roc_auc",
        "problem_title": "Predict churn",
        "problem_description": "Prioritise retention outreach.",
        "excluded_columns": ["email"],
        "validation": {
            "strategy": "stratified",
            "n_folds": 3,
            "test_size": 0.2,
            "rationale": "Preserve class representation.",
        },
        "candidate_limit": 2,
        "instructions": "Prefer a compact, interpretable result.",
    }


def test_workflow_graph_shows_complete_agentic_spec_before_run(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    graph = client.get("/api/workflow").json()

    assert graph["workflow"] == "agent-backed-full"
    assert [node["id"] for node in graph["nodes"]] == [
        "intake",
        "schema_discovery",
        "integration",
        "problem_discovery",
        "validation_strategy",
        "eda",
        "leakage_audit",
        "feature_pipeline",
        "splitting",
        "training",
        "evaluation",
        "report",
    ]
    assert all(node["status"] == "pending" for node in graph["nodes"])
    assert {node["id"] for node in graph["nodes"] if node["kind"] == "planner_agent"} == {
        "schema_discovery",
        "problem_discovery",
        "validation_strategy",
    }
    assert {edge["condition"] for edge in graph["edges"]} == {
        "on_proceed",
        "on_retry",
    }
    assert any(
        edge == {"source": "leakage_audit", "target": "leakage_audit", "condition": "on_retry"}
        for edge in graph["edges"]
    )


def test_manual_mode_graph_matches_the_nine_executed_stages(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    graph = client.get("/api/workflow", params={"mode": "manual"}).json()

    assert graph["workflow"] == "deterministic-default"
    assert len(graph["nodes"]) == 9
    assert not any(node["kind"] == "planner_agent" for node in graph["nodes"])


def test_run_options_are_derived_from_closed_contract_vocabularies(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    options = client.get("/api/run-options").json()

    assert "binary_classification" in options["task_types"]
    assert options["metrics_by_task"]["regression"] == ["mae", "mape", "r2", "rmse"]
    assert "grouped_temporal" in options["split_strategies"]


def test_planner_chat_can_configure_and_remember_rules_without_raw_rows(
    tmp_path: Path,
) -> None:
    fake = _PlannerFakeLLM(
        {
            "reply": "I suggest regression with a temporal split.",
            "configuration_patch": {
                "task_type": "regression",
                "validation_strategy": "temporal",
                "unsafe_unknown_key": "ignored",
            },
            "rules_to_remember": ["Ask before model selection."],
            "checkpoint_stages": ["training", "not_a_stage"],
            "auto_proceed_stages": ["eda"],
            "max_retries_by_stage": {"eda": 2, "not_a_stage": 9},
            "focus_stage": "validation_strategy",
            "proposed_decision": None,
            "decision_instructions": [],
        }
    )
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: fake
    client = TestClient(create_app(plane=plane))

    response = client.post(
        "/api/planner/chat",
        json={
            "message": "Use time-aware validation and ask me before choosing a model.",
            "source_id": "safe-demo",
            "configuration": {"target_column": "churned"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configuration_patch"] == {
        "task_type": "regression",
        "validation_strategy": "temporal",
    }
    assert body["rules_to_remember"] == ["Ask before model selection."]
    assert body["checkpoint_stages"] == ["training"]
    assert body["auto_proceed_stages"] == ["eda"]
    assert body["max_retries_by_stage"] == {"eda": 2}
    assert "secret-one@example.test" not in fake.calls[0]["prompt"]


def test_planner_supervision_maps_to_real_checkpoint_and_retry_policy(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    supervision = plane._normalise_supervision(  # noqa: SLF001 - policy seam contract
        {
            "checkpoint_stages": ["training", "eda"],
            "auto_proceed_stages": ["eda"],
            "max_retries_by_stage": {"eda": 2},
        }
    )
    policy = plane._policy_for_supervision(supervision)  # noqa: SLF001

    assert supervision["checkpoint_stages"] == ["training"]
    assert policy.stage("eda").max_attempts == 3


def test_multiple_dropped_files_form_one_persisted_source(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    client = TestClient(create_app(plane=plane))

    first = client.post("/api/uploads/accounts.csv", content=b"account_id,balance\n1,10\n")
    assert first.status_code == 200
    source_id = first.json()["source_id"]
    second = client.post(
        "/api/uploads/events.csv",
        params={"source_id": source_id},
        content=b"account_id,event_count\n1,2\n",
    )

    assert second.status_code == 200
    assert second.json()["source_id"] == source_id
    assert second.json()["files"] == ["accounts.csv", "events.csv"]
    assert {path.name for path in plane.source_path(source_id).iterdir()} == {
        "accounts.csv",
        "events.csv",
    }


def test_source_profile_never_serves_raw_values_or_source_paths(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    response = client.get("/api/data-sources/safe-demo/profile")
    rendered = response.text

    assert response.status_code == 200
    assert "secret-one@example.test" not in rendered
    assert "secret-two@example.test" not in rendered
    assert str(tmp_path) not in rendered
    assert "sample_values" not in rendered
    assert "top_values" not in rendered
    assert response.json()["privacy"].startswith("Schema and aggregate statistics only")


def test_structured_launch_drives_real_run_boundary_and_durable_progress(
    tmp_path: Path, monkeypatch
) -> None:
    plane = _plane(tmp_path)
    entered = threading.Event()
    captured = {}

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        captured["workflow"] = spec.name
        captured["intent"] = state.user_intent
        on_event("stage_started", {"stage": "intake", "attempt": 1})
        entered.set()
        on_event(
            "gate_decided",
            {"stage": "intake", "verdict": "auto_proceed", "reason": "no_rule_triggered"},
        )
        return SimpleNamespace(status="completed", error=None, final_stage=None)

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane=plane))
    response = client.post("/api/runs", json=_configuration())
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert entered.wait(2)

    for _ in range(50):
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        if progress["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("background workflow did not complete")

    assert captured == {
        "workflow": "deterministic-default",
        "intent": "Prefer a compact, interpretable result.",
    }
    assert progress["configuration"]["target_column"] == "churned"
    assert progress["configuration"]["validation"]["strategy"] == "stratified"
    assert [event["event"] for event in progress["events"]] == [
        "stage_started",
        "gate_decided",
    ]

    restarted = ControlPlane(
        store=plane.store,
        source_roots=plane.source_roots,
        upload_root=plane.upload_root,
    )
    assert restarted.progress(run_id)["status"] == "completed"
    assert restarted.list_runs()[0].run_id == run_id


def test_agent_mode_launches_the_same_complete_spec_shown_before_run(
    tmp_path: Path, monkeypatch
) -> None:
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: SimpleNamespace()
    entered = threading.Event()
    captured = {}

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        captured["workflow"] = spec.name
        captured["stages"] = spec.stage_ids()
        captured["intent"] = state.user_intent
        entered.set()
        return SimpleNamespace(status="completed", error=None, final_stage=None)

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane=plane))
    configuration = {**_configuration(), "mode": "agent"}
    response = client.post("/api/runs", json=configuration)

    assert response.status_code == 200
    assert entered.wait(2)
    assert captured["workflow"] == "agent-backed-full"
    assert captured["stages"] == tuple(
        node["id"] for node in client.get("/api/workflow").json()["nodes"]
    )
    assert "Prefer a compact, interpretable result." in captured["intent"]
    assert "target 'churned'" in captured["intent"]
    assert "validation 'stratified'" in captured["intent"]


def test_graph_progress_and_stage_inspection_have_workflow_states(
    tmp_path: Path, monkeypatch
) -> None:
    plane = _plane(tmp_path)
    entered = threading.Event()

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        attempt = state.begin_attempt("intake")
        attempt.ended_at = attempt.started_at
        on_event("stage_started", {"stage": "intake", "attempt": 1})
        entered.set()
        return SimpleNamespace(status="failed", error="fixture failure", final_stage="intake")

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane=plane))
    run_id = client.post("/api/runs", json=_configuration()).json()["run_id"]
    assert entered.wait(2)
    for _ in range(50):
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        if progress["status"] == "failed":
            break
        time.sleep(0.01)

    graph = client.get("/api/workflow", params={"run_id": run_id}).json()
    intake = next(node for node in graph["nodes"] if node["id"] == "intake")
    assert intake["status"] == "failed"
    assert intake["attempt_count"] == 1

    stage = client.get(f"/api/runs/{run_id}/stages/intake").json()
    assert stage["stage"]["description"]
    assert stage["attempts"][0]["attempt"] == 1
    assert set(stage) >= {
        "inputs",
        "outputs",
        "measurements",
        "gate_decisions",
        "corrections",
        "human_questions",
    }


WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def test_main_page_is_the_workflow_experience_not_an_artifact_dashboard() -> None:
    """The three-region layout the brief specifies, asserted at its source.

    design_prompt.txt is explicit: pipeline at the top, stage workspace in the
    centre, planner chat on the right. The main screen must compose those three
    regions rather than list artifacts.
    """
    workflows = (WEB_SRC / "pages" / "Workflows.tsx").read_text(encoding="utf-8")

    for region in ("PipelineRail", "StageWorkspace", "PlannerPanel"):
        assert f"<{region}" in workflows, f"the main screen does not render {region}"

    # Branching must stay legible rather than being flattened into a line.
    rail = (WEB_SRC / "components" / "PipelineRail.tsx").read_text(encoding="utf-8")
    assert "branch_of" in rail

    # Stage output is rendered from measured structures, not dumped as JSON.
    workspace = (WEB_SRC / "components" / "StageWorkspace.tsx").read_text(encoding="utf-8")
    for structure in ("warnings", "model_comparison", "holdout_metrics"):
        assert structure in workspace, f"{structure} has no renderer"

    # Analyses are a horizontal strip of chart thumbnails that expand on click,
    # not a vertical stack of collapsed headings a reader has to open one by one.
    strip = (WEB_SRC / "components" / "AnalysisStrip.tsx").read_text(encoding="utf-8")
    assert "overflow-x-auto" in strip
    assert "Key insights" in strip
    assert "Summary statistics" in strip
    assert "<AnalysisStrip" in workspace

    # Charts are drawn in-bundle: the deployment target is air-gapped, so a
    # chart library loaded from a CDN would render nothing at all.
    charts = (WEB_SRC / "components" / "Charts.tsx").read_text(encoding="utf-8")
    for kind in ("bar", "histogram", "hbar", "heatmap", "box", "donut"):
        assert f'case "{kind}"' in charts, f"no renderer for a {kind} chart"

    # The schema must be legible to a person, not only to the agent.
    assert "<SchemaMap" in workspace


def test_every_pipeline_stage_has_a_reachable_workspace(tmp_path: Path, monkeypatch) -> None:
    """Each stage in the spec must be inspectable, including before it runs.

    The old assertion listed canned per-stage prose in the served HTML. The
    workspace now renders each stage from the backend, so the property that
    matters is coverage: every node the graph advertises resolves to a stage
    endpoint rather than a 404.
    """
    plane = _plane(tmp_path)
    entered = threading.Event()

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        entered.set()
        return SimpleNamespace(status="completed", error=None, final_stage=None)

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane=plane))

    run_id = client.post("/api/runs", json={**_configuration(), "mode": "agent"}).json()["run_id"]
    assert entered.wait(2)

    # Query the graph the way the workspace does — annotated with the run. The
    # unparameterised graph is the agent spec, but a manual run executes eight
    # stages, so the two disagree by construction.
    nodes = client.get("/api/workflow", params={"run_id": run_id}).json()["nodes"]
    assert len(nodes) >= 8

    for node in nodes:
        response = client.get(f"/api/runs/{run_id}/stages/{node['id']}")
        assert response.status_code == 200, f"stage {node['id']} has no workspace"
        assert response.json()["stage"]["id"] == node["id"]


def test_eda_card_exposes_safe_visual_aggregates_and_deterministic_checks(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    report = EDAReport(
        problem_title="Forecast customer value",
        task_type=TaskType.REGRESSION,
        target_column="value",
        row_count=100,
        feature_columns=["age", "spend"],
        model_eligible_columns=["age", "spend"],
        covered_columns=["age", "spend"],
        target_distribution=TargetDistribution(
            target_column="value",
            kind=DistributionKind.NUMERIC_SUMMARY,
            total_count=100,
            non_null_count=90,
            null_count=10,
            null_rate=0.1,
            n_unique=90,
            numeric=NumericStats(
                min=10,
                max=100,
                mean=50,
                std=20,
                p25=35,
                p50=48,
                p75=65,
                zero_rate=0,
                negative_rate=0,
            ),
        ),
        missingness=[
            MissingnessSummary(column="age", null_count=0, null_rate=0),
            MissingnessSummary(column="spend", null_count=12, null_rate=0.12),
            MissingnessSummary(column="value", null_count=10, null_rate=0.1),
        ],
        correlation_matrix=CorrelationMatrix(
            columns=["age", "spend", "value"],
            values=[[1, 0.2, 0.3], [0.2, 1, 0.95], [0.3, 0.95, 1]],
        ),
        target_relationships=[
            TargetRelationship(
                column="age", pearson_correlation=0.3, adjusted_mutual_information=0.1
            ),
            TargetRelationship(
                column="spend", pearson_correlation=0.95, adjusted_mutual_information=0.8
            ),
        ],
        outliers=[
            OutlierSummary(
                column="spend",
                lower_fence=-5,
                upper_fence=200,
                evaluated_count=88,
                outlier_count=4,
                outlier_rate=4 / 88,
            )
        ],
    )
    plane.store.put(report, run_id="eda-story", stage_exec_id="eda")

    detail = plane.stage_detail("eda-story", "eda")
    story = next(item["story"] for item in detail["outputs"] if item["type"] == "eda_report")

    assert story["visuals"]["target"]["numeric"]["p50"] == 48
    assert story["visuals"]["missingness"][0]["column"] == "spend"
    assert story["visuals"]["relationships"][0]["column"] == "spend"
    assert all(story["criteria"].values())
    assert "correlation" in story["warnings"][1]


def test_problem_card_explains_the_agents_suggestion_in_human_terms(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    problem = ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="churned",
        primary_metric=Metric.RMSE,
        title="Forecast customer churn score",
        description="Prioritise retention outreach using pre-outcome evidence.",
        confirmed_by="auto",
    )
    plane.store.put(problem, run_id="story-run", stage_exec_id="problem_discovery")

    detail = plane.stage_detail("story-run", "problem_discovery")
    story = next(
        item["story"] for item in detail["outputs"] if item["type"] == "problem_definition"
    )

    assert "Forecast customer churn score" in story["suggestion"]
    assert "predicting churned" in story["suggestion"]
    assert story["rationale"].startswith("Prioritise retention")
    assert detail["human_view"]["state_label"] == "No action needed"


def test_every_blocked_stage_card_surfaces_question_and_recommended_option(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    decision = GateDecision(
        stage_id="leakage_audit",
        attempt=1,
        verdict=GateVerdict.ESCALATE,
        reason_code="leakage_unresolved",
        human_prompt=HumanPrompt(
            stage_id="leakage_audit",
            question="Should the suspicious features be removed?",
            context_summary="Ten features include information from after the cutoff.",
            options=[
                DecisionOption(
                    option_id="retry",
                    label="Remove and retry",
                    consequence="Drop the unsafe features and re-run the audit.",
                    recommended=True,
                )
            ],
        ),
    )
    plane.store.put(decision, run_id="blocked-run", stage_exec_id="leakage_audit")

    detail = plane.stage_detail("blocked-run", "leakage_audit")

    assert detail["human_view"]["needs_human"] is True
    assert detail["human_view"]["state_label"] == "Your decision is needed"
    assert detail["human_view"]["question"]["question"].startswith("Should")
    assert detail["human_view"]["question"]["options"][0]["recommended"] is True


def test_resolved_retry_does_not_keep_an_old_human_question_active(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    first_at = datetime.now(UTC)
    plane.store.put(
        GateDecision(
            created_at=first_at,
            stage_id="leakage_audit",
            attempt=1,
            verdict=GateVerdict.ESCALATE,
            reason_code="leakage_unresolved",
            human_prompt=HumanPrompt(
                stage_id="leakage_audit",
                question="Remove the suspicious feature?",
                context_summary="One feature uses future information.",
                options=[
                    DecisionOption(
                        option_id="retry",
                        label="Remove and retry",
                        consequence="Drop it and audit again.",
                        recommended=True,
                    )
                ],
            ),
        ),
        run_id="resolved-run",
        stage_exec_id="leakage_audit",
    )
    plane.store.put(
        GateDecision(
            created_at=first_at + timedelta(seconds=1),
            stage_id="leakage_audit",
            attempt=2,
            verdict=GateVerdict.AUTO_PROCEED,
            reason_code="no_rule_triggered",
        ),
        run_id="resolved-run",
        stage_exec_id="leakage_audit",
    )

    detail = plane.stage_detail("resolved-run", "leakage_audit")

    assert detail["status"] == "succeeded"
    assert detail["human_view"]["needs_human"] is False
    assert detail["human_view"]["question"] is None


def test_runs_without_ui_snapshots_still_have_switchable_progress(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.store.put(
        ProblemDefinition(
            task_type=TaskType.REGRESSION,
            target_column="churned",
            primary_metric=Metric.RMSE,
            title="Archived problem",
            description="Created before durable UI snapshots.",
            confirmed_by="auto",
        ),
        run_id="old-run",
        stage_exec_id="problem_discovery",
    )

    progress = plane.progress("old-run")

    assert progress["legacy_snapshot"] is True
    assert progress["status"] == "archived"
    assert progress["attempts"][0]["stage_id"] == "problem_discovery"
