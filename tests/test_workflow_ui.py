"""Focused tests for the fixed workflow UI's executable vertical slice."""

from __future__ import annotations

import json
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
from ads.contracts.documents import DocumentExtraction, DocumentFileResult, ExtractedDocument
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
from ads.contracts.staging import StagingWorkspace
from ads.documents.pdf import PdfDocument, PdfPage
from ads.llm import LLMResponse, ModelProfile
from ads.pipeline.workflow import build_default_spec
from ads.staging import build_default_blueprint
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
        # Without this, agent mode constructs an OllamaClient and refuses the
        # run when no model is reachable, so the test passed on a developer
        # machine and could not pass anywhere else. Nothing here calls the
        # model: the spec only needs an object to wire the agents to.
        llm_factory=lambda: object(),
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
            "rationale_tr": "Sınıf temsilini koru.",
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
        # After the split on purpose: it sends training rows only, so the
        # external feature search cannot see the holdout.
        "rl_feature_engineering",
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


def test_manual_mode_graph_matches_the_executed_stages(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    graph = client.get("/api/workflow", params={"mode": "manual"}).json()

    assert graph["workflow"] == "deterministic-default"
    # Held to the spec rather than to a literal count, so adding a stage updates
    # this in one place instead of leaving a number nobody can tie to anything.
    assert [node["id"] for node in graph["nodes"]] == list(build_default_spec().stage_ids())
    assert not any(node["kind"] == "planner_agent" for node in graph["nodes"])


def test_human_approved_escalation_projects_as_completed_stage(tmp_path: Path, monkeypatch) -> None:
    """Approval resolves attention state without erasing the gate audit."""
    plane = _plane(tmp_path)
    escalated_attempt = {
        "stage_id": "leakage_audit",
        "attempt": 1,
        "started_at": "2026-08-24T18:01:28+00:00",
        "ended_at": "2026-08-24T18:01:47+00:00",
        "verdict": "escalate",
        "error": None,
        "artifact_ids": [],
        "input_bindings": {},
    }
    monkeypatch.setattr(
        plane,
        "progress",
        lambda run_id: {
            "run_id": run_id,
            "status": "completed",
            "current_stage": None,
            "configuration": {"mode": "agent"},
            "attempts": [escalated_attempt],
            "events": [
                {
                    "event": "human_decision_recorded",
                    "stage": "leakage_audit",
                    "decision": "approve",
                }
            ],
        },
    )

    graph = plane.workflow_graph("approved-run")
    leakage = next(node for node in graph["nodes"] if node["id"] == "leakage_audit")

    assert leakage["status"] == "succeeded"
    assert escalated_attempt["verdict"] == "escalate"


def test_run_options_are_derived_from_closed_contract_vocabularies(tmp_path: Path) -> None:
    client = TestClient(create_app(plane=_plane(tmp_path)))
    options = client.get("/api/run-options").json()

    assert "binary_classification" in options["task_types"]
    assert options["metrics_by_task"]["regression"] == ["mae", "mape", "r2", "rmse"]
    assert "grouped_temporal" in options["split_strategies"]


def test_document_only_source_gets_a_persisted_staging_graph(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)
    planner = _PlannerFakeLLM(
        {
            "reply": "The document evidence is ready for review.",
            "pipeline_decision": "no_pipeline",
            "decision_reason_en": "The documents support understanding, not an ML workflow.",
            "decision_reason_tr": "Belgeler anlamayı destekliyor, ML iş akışını değil.",
            "reports": [
                {
                    "kind": "documents",
                    "title_en": "Document briefing",
                    "title_tr": "Belge özeti",
                    "summary_en": "The PDF was inspected and its extraction status is explicit.",
                    "summary_tr": "PDF incelendi ve çıkarım durumu açıkça kaydedildi.",
                }
            ],
            "rationale_en": ["Keep document evidence separate from trusted tables."],
            "rationale_tr": ["Belge kanıtını güvenilir tablolardan ayrı tut."],
        }
    )
    plane.llm_factory = lambda: planner
    document_source = tmp_path / "sources" / "documents-only"
    document_source.mkdir()
    (document_source / "brief.pdf").write_bytes(b"test-document")
    profile = {
        "source_id": "documents-only",
        "label": "documents-only",
        "tables": [],
        "relationships": [],
        "documents": [{"name": "brief.pdf", "page_count": 1}],
    }
    monkeypatch.setattr(plane, "source_profile", lambda source_id: profile)
    monkeypatch.setattr(
        "ads.api.service.extract_document_directory",
        lambda *args, **kwargs: DocumentExtraction(
            source_id="documents-only",
            source_fingerprint="fingerprint",
            engine="docling",
            documents=[ExtractedDocument(source_file="brief.pdf", page_count=1)],
            file_results=[
                DocumentFileResult(source_file="brief.pdf", status="ready", page_count=1)
            ],
            duration_seconds=0.1,
        ),
    )

    staged = plane.stage_run("documents-only")
    workspace = plane.staging_workspace(staged["run_id"])
    components = {item["id"]: item for item in workspace["pipeline_blueprint"]["components"]}

    assert staged["status"] == "staging"
    assert components["intake"]["enabled"] is False
    assert components["understand-documents"]["enabled"] is True
    assert workspace["source_id"] == "documents-only"
    deadline = time.time() + 10
    while plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)
    progress = plane.progress(staged["run_id"])
    assert progress["status"] == "staged"
    assert "document_understanding_started" in {event["event"] for event in progress["events"]}
    assert "staging_analysis_ready" in {event["event"] for event in progress["events"]}
    completed = plane.staging_workspace(staged["run_id"])
    assert planner.calls
    automatic_schema = planner.calls[0]["schema"]
    schema_text = json.dumps(automatic_schema)
    assert "pipeline_component_additions" not in schema_text
    assert "pipeline_connections" not in schema_text
    automatic_prompt = json.loads(planner.calls[0]["prompt"])
    assert set(automatic_prompt) == {
        "source_files",
        "structured_tables",
        "measured_relationships",
        "document_extraction",
    }
    assert completed["reports"][0]["title"]["en"] == "Document briefing"
    assert completed["recommended_plan"] is not None
    assert completed["recommended_plan"]["pipeline_recommendation"] == "no_pipeline"
    assert completed["recommended_plan"]["configuration"] == {}


def test_document_failure_stops_staging_and_skips_planner(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)
    planner = _PlannerFakeLLM({"reply": "must not run"})
    plane.llm_factory = lambda: planner
    document_source = tmp_path / "sources" / "broken-documents"
    document_source.mkdir()
    filename = "toefl-ibt-teachers-resources-practice-test-3 (1).pdf"
    (document_source / filename).write_bytes(b"test-document")
    monkeypatch.setattr(
        plane,
        "source_profile",
        lambda source_id: {
            "source_id": source_id,
            "label": source_id,
            "tables": [],
            "relationships": [],
            "documents": [{"name": filename, "page_count": 1}],
        },
    )

    def failed_extraction(*args, **kwargs):
        del args, kwargs
        return DocumentExtraction(
            source_id="broken-documents",
            source_fingerprint="fingerprint",
            engine="docling",
            documents=[ExtractedDocument(source_file="other.pdf", page_count=1)],
            file_results=[
                DocumentFileResult(
                    source_file=filename,
                    status="failed",
                    warnings=[f"{filename}:ValidationError:invalid candidate_id"],
                )
            ],
            duration_seconds=0.1,
        )

    monkeypatch.setattr("ads.api.service.extract_document_directory", failed_extraction)

    staged = plane.stage_run("broken-documents")
    deadline = time.time() + 5
    while plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)

    progress = plane.progress(staged["run_id"])
    workspace = plane.staging_workspace(staged["run_id"])
    assert progress["status"] == "failed"
    # `error` is bilingual now, so the run message can be shown in Turkish (#263).
    assert "DocumentExtractionError" in progress["error"]["en"]
    assert any(event["event"] == "document_understanding_failed" for event in progress["events"])
    assert not any(event["event"] == "staging_analysis_ready" for event in progress["events"])
    assert planner.calls == []
    assert workspace["document_extractions"][-1]["status"] == "failed"
    assert workspace["document_extractions"][-1]["files"][0]["status"] == "failed"
    assert workspace["recommended_plan"] is None


def test_planner_failure_cannot_mark_document_staging_ready(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)

    class FailingPlanner:
        def generate_structured(self, **kwargs):
            del kwargs
            raise UnicodeError("planner output could not be decoded")

    plane.llm_factory = FailingPlanner
    document_source = tmp_path / "sources" / "planner-failure"
    document_source.mkdir()
    (document_source / "brief.pdf").write_bytes(b"test-document")
    monkeypatch.setattr(
        plane,
        "source_profile",
        lambda source_id: {
            "source_id": source_id,
            "label": source_id,
            "tables": [],
            "relationships": [],
            "documents": [{"name": "brief.pdf", "page_count": 1}],
        },
    )
    monkeypatch.setattr(
        "ads.api.service.extract_document_directory",
        lambda *args, **kwargs: DocumentExtraction(
            source_id="planner-failure",
            source_fingerprint="fingerprint",
            engine="docling",
            documents=[ExtractedDocument(source_file="brief.pdf", page_count=1)],
            file_results=[
                DocumentFileResult(source_file="brief.pdf", status="ready", page_count=1)
            ],
            duration_seconds=0.1,
        ),
    )

    staged = plane.stage_run("planner-failure")
    deadline = time.time() + 5
    while plane.progress(staged["run_id"])["status"] == "staging" and time.time() < deadline:
        time.sleep(0.02)

    progress = plane.progress(staged["run_id"])
    workspace = plane.staging_workspace(staged["run_id"])
    assert progress["status"] == "failed"
    assert progress["current_stage"] is None
    assert "Planner could not create" in progress["error"]["en"]
    assert "staging_analysis_failed" in {event["event"] for event in progress["events"]}
    assert "staging_analysis_ready" not in {event["event"] for event in progress["events"]}
    assert workspace["recommended_plan"] is None
    assert "UnicodeError" in workspace["planner_error"]


def test_planner_chat_can_configure_and_remember_rules_without_raw_rows(
    tmp_path: Path,
) -> None:
    fake = _PlannerFakeLLM(
        {
            "reply": "I suggest regression with a temporal split.",
            "reply_tr": "Zamansal bölmeyle regresyon öneriyorum.",
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
    assert "reply_tr" not in body
    assert "reply_tr" not in fake.calls[0]["schema"]["properties"]
    assert "reply_tr" not in fake.calls[0]["system"]
    assert "secret-one@example.test" not in fake.calls[0]["prompt"]
    assert "intake.guiding_data_understanding" in fake.calls[0]["system"]
    assert "intake.reading_documents" in fake.calls[0]["system"]
    assert "Reference guidance only" in fake.calls[0]["system"]


class _UnparseablePlannerLLM:
    """A backend whose model reply could not be decoded, mirroring what Ollama
    and DeepSeek do on malformed output: parsed=None, the raw parser text in
    parse_error. #242 is exactly this reaching the route."""

    def __init__(self, parse_error: str) -> None:
        self.parse_error = parse_error

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        return LLMResponse(
            text="{...truncated...",
            model=profile.name,
            latency_s=0.01,
            parsed=None,
            parse_error=self.parse_error,
        )


def test_malformed_planner_reply_is_a_502_with_a_readable_message_not_a_400_offset(
    tmp_path: Path,
) -> None:
    """#242: a JSON decode failure on the model's reply used to surface as an
    HTTP 400 whose whole body was the parser's offset -- "Expecting ',' delimiter:
    line 1 column 5151 (char 5150)". json.JSONDecodeError subclasses ValueError,
    so the route's `except ValueError` caught it and blamed the request. A bad
    model reply is an upstream failure (502) and the offset must never be the
    user-facing text."""
    offset = "Expecting ',' delimiter: line 1 column 5151 (char 5150)"
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: _UnparseablePlannerLLM(offset)
    client = TestClient(create_app(plane=plane))

    response = client.post(
        "/api/planner/chat",
        json={"message": "Give me a plan.", "source_id": "safe-demo"},
    )

    # Not a 400: the request was fine, the model's answer was not.
    assert response.status_code == 502
    detail = response.json()["detail"]
    # The parser offset is for the logs, never the person.
    assert offset not in detail
    assert "char 5150" not in detail
    assert "malformed" in detail.lower()


def test_ml_planner_recovers_source_context_and_ranks_problems_without_gate_actions(
    tmp_path: Path, monkeypatch
) -> None:
    """#190: an ML-workspace chat must know the schema even when its mount
    supplies only a run id, and ordinary recommendations must not be shaped as
    approve/retry/abort decisions or silently mutate the graph."""
    fake = _PlannerFakeLLM(
        {
            "reply": "1. Predict churned; it is a measured binary candidate.",
            "problem_recommendations": [
                {
                    "rank": 1,
                    "problem_title": "Predict churn",
                    "target_column": "churned",
                    "task_type": "binary_classification",
                    "primary_metric": "roc_auc",
                    "evidence": ["The profile marks churned as a candidate target."],
                    "caveats": ["Confirm that churn is known after prediction time."],
                },
                {
                    "rank": 2,
                    "problem_title": "Invented target",
                    "target_column": "not_a_real_column",
                    "task_type": "regression",
                    "primary_metric": "rmse",
                    "evidence": ["No measured evidence."],
                    "caveats": [],
                },
            ],
        }
    )
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: fake
    monkeypatch.setattr(
        plane,
        "progress",
        lambda run_id: {
            "run_id": run_id,
            "source_id": "safe-demo",
            "status": "staged",
            "current_stage": None,
            "events": [],
            "attempts": [],
        },
    )
    monkeypatch.setattr(plane, "gate_decisions", lambda run_id: [])

    result = plane.planner_chat(
        message="Rank the best target columns and ML problems for this data.",
        run_id="staged-run",
    )

    prompt = json.loads(fake.calls[0]["prompt"])
    column_names = {
        column["name"]
        for table in prompt["safe_source_profile"]["tables"]
        for column in table["columns"]
    }
    schema_properties = fake.calls[0]["schema"]["properties"]
    assert column_names == {"customer_id", "email", "age", "churned"}
    assert "secret-one@example.test" not in fake.calls[0]["prompt"]
    assert prompt["gate_action_available"] is False
    assert "problem_recommendations" in schema_properties
    assert "proposed_decision" not in schema_properties
    assert "decision_instructions" not in schema_properties
    assert [item["target_column"] for item in result["problem_recommendations"]] == [
        "churned"
    ]
    assert "Answer direct schema questions first" in fake.calls[0]["system"]


def test_pre_pipeline_planner_receives_intake_and_schema_discovery_together(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _PlannerFakeLLM(
        {
            "reply": "The source has one customer table.",
            "reply_tr": "Kaynakta bir müşteri tablosu var.",
        }
    )
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: fake

    monkeypatch.setattr(
        plane,
        "progress",
        lambda run_id: {
            "run_id": run_id,
            "status": "staged",
            "current_stage": "schema_discovery",
            "events": [],
            "attempts": [],
        },
    )
    monkeypatch.setattr(plane, "gate_decisions", lambda run_id: [])

    def stage_detail(run_id: str, stage_id: str) -> dict[str, Any]:
        del run_id
        return {
            "stage": {"id": stage_id},
            "status": "succeeded",
            "human_view": {"state_label": "Complete"},
            "outputs": [
                {
                    "type": "data_card" if stage_id == "intake" else "integration_plan",
                    "summary": f"{stage_id} summary",
                    "story": {"stage": stage_id},
                }
            ],
            "gate_decisions": [],
        }

    monkeypatch.setattr(plane, "stage_detail", stage_detail)

    plane.planner_chat(
        message="Help me understand this source.",
        source_id="safe-demo",
        run_id="staged-run",
        stage_id="intake",
    )

    prompt = json.loads(fake.calls[0]["prompt"])
    assert set(prompt["intake_and_schema_discovery"]) == {"intake", "schema_discovery"}
    assert (
        prompt["intake_and_schema_discovery"]["schema_discovery"]["outputs"][0]["type"]
        == "integration_plan"
    )


def test_planner_receives_bounded_page_provenance_from_local_pdf(
    tmp_path: Path, monkeypatch
) -> None:
    fake = _PlannerFakeLLM(
        {
            "reply": "The retention chart is discussed on page 2.",
            "reply_tr": "Elde tutma grafiği 2. sayfada ele alınıyor.",
        }
    )
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: fake
    (plane.source_path("safe-demo") / "brief.pdf").write_bytes(b"local-test-pdf")
    monkeypatch.setattr(
        "ads.api.service.load_pdf_directory",
        lambda path: [
            PdfDocument(
                name="brief.pdf",
                page_count=2,
                pages=(
                    PdfPage(1, "Overview of the provider study."),
                    PdfPage(2, "The retention chart compares monthly churn by provider."),
                ),
                image_count=1,
            )
        ],
    )

    plane.planner_chat(
        message="What does the retention chart show?",
        source_id="safe-demo",
    )

    prompt = json.loads(fake.calls[0]["prompt"])
    pdf = prompt["local_pdf_context"][0]
    assert pdf["name"] == "brief.pdf"
    assert {excerpt["page"] for excerpt in pdf["selected_excerpts"]} == {1, 2}
    assert "retention chart" in pdf["selected_excerpts"][1]["text"]


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


def test_main_page_progressively_reveals_one_automation_workspace() -> None:
    """Upload, understanding, proposal, and graph remain one progressive route."""
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

    # Source understanding is a real pre-pipeline workspace. It uses the same
    # measured graph and connects its planner to both the source and staged run.
    explore = (WEB_SRC / "pages" / "Explore.tsx").read_text(encoding="utf-8")
    for feature in ("<SchemaDiagram", "<AgentBriefing", "<DocumentUnderstanding", "starterPrompts"):
        assert feature in explore, f"the data-understanding screen is missing {feature}"
    assert "sourceId={currentRun?.dataset ?? null}" in workflows

    shell = (WEB_SRC / "components" / "Shell.tsx").read_text(encoding="utf-8")
    app = (WEB_SRC / "App.tsx").read_text(encoding="utf-8")
    automation = (WEB_SRC / "pages" / "Automation.tsx").read_text(encoding="utf-8")
    one_page = (WEB_SRC / "pages" / "AutomationWorkspace.tsx").read_text(encoding="utf-8")
    project_page = (WEB_SRC / "pages" / "ProjectWorkspace.tsx").read_text(encoding="utf-8")
    builder = (WEB_SRC / "components" / "PipelineBuilder.tsx").read_text(encoding="utf-8")
    guided = (WEB_SRC / "components" / "GuidedPipeline.tsx").read_text(encoding="utf-8")
    assert '{ to: "/explore", label: "Your data"' not in shell
    # #111 makes projects the only top-level concept: the sidebar is Home,
    # Projects and Settings, and the removed catalogues (including /explore's old
    # /datasets target) redirect to the project-first home rather than dead-end.
    assert '{ to: "/datasets"' not in shell
    assert 'path="/explore" element={<Navigate to="/" replace />}' in app
    assert '{ to: "/projects", label: "Projects"' in shell
    assert 'path="/projects"' in app
    assert "<AutomationWorkspace" in automation
    # #157 splits project-owned upload from the child automation. Understanding,
    # proposal, and the accepted workflow remain progressive inside that child.
    # #214 merged the proposal canvas into the accepted one: `GuidedPipeline`
    # renders both states, so the proposal no longer has a component of its own.
    for feature in (
        "<GuidedPipeline",
        "<PipelineBuilder",
        "<UnderstandingProgress",
        "acceptPlan",
    ):
        assert feature in one_page
    assert "<UnderstandingAndProposal" not in one_page
    assert "api.upload" in project_page
    assert "<AutomationInputSelector" in one_page
    assert "<PlannerPanel" in guided
    assert "<PlannerPanel" in builder
    for control in ("pause_after", "gate_handler", "max_retries", "ArtifactPreview"):
        assert control in builder


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


def test_a_refused_graph_edit_costs_the_edit_and_not_the_answer(
    tmp_path: Path, monkeypatch
) -> None:
    """#193: the planner could save a graph the runner would always reject.

    A turn that asked for a target column authored a `plan-validation` node with
    no incoming `integrated_table` edge. `apply_planner_graph_operations` only
    checked that the edges it was handed were well-formed, so the write
    succeeded and every later attempt to continue failed with a 400 the person
    could do nothing about.

    The edit is now refused where it is made. What is checked here is the other
    half: refusing it must not cost the reply that came with it, or a bad graph
    proposal would take the whole conversation down with it.
    """
    fake = _PlannerFakeLLM(
        {
            "reply": "Use `churned` as the target.",
            "reply_tr": "Hedef olarak `churned` kullanın.",
            "configuration_patch": {"target_column": "churned"},
            "pipeline_component_additions": [
                {"catalog_id": "agent.plan_validation", "component_id": "plan-validation"}
            ],
        }
    )
    plane = _plane(tmp_path)
    plane.llm_factory = lambda: fake

    baseline = build_default_blueprint(has_tables=True, has_documents=False)
    workspace = StagingWorkspace(
        source_id="safe-demo",
        source_fingerprint="fingerprint",
        pipeline_blueprint=baseline,
    )
    monkeypatch.setattr(plane, "_latest_staging_workspace", lambda run_id: workspace)
    monkeypatch.setattr(
        plane,
        "progress",
        lambda run_id: {"run_id": run_id, "status": "staged", "events": [], "attempts": []},
    )
    monkeypatch.setattr(plane, "gate_decisions", lambda run_id: [])

    result = plane.planner_chat(
        message="Set the target column.",
        source_id="safe-demo",
        run_id="staged-run",
    )

    # The answer survives, and so does the part of it that needed no graph edit.
    assert result["reply"].startswith("Use `churned`")
    assert result["configuration_patch"]["target_column"] == "churned"

    # The graph is untouched, and the refusal says what was wrong with it.
    assert "pipeline_blueprint" not in result
    rejected = result["graph_edit_rejected"]
    assert "plan-validation" in rejected
    assert "integrated_table" in rejected
    assert "pipeline_connections" in rejected
