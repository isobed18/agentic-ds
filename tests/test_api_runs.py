"""Vertical-slice tests for source selection, run launch, and progress polling."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from ads.api import ControlPlane, create_app
from ads.contracts import (
    IntegrationPlan,
    Metric,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)
from ads.store import ArtifactStore


def _contracts() -> dict:
    return {
        "integration_plan": IntegrationPlan(
            base_table="table",
            base_grain=["id"],
            grain_description="One row per id.",
            joins=[],
        ).model_dump(mode="json"),
        "problem": ProblemDefinition(
            task_type=TaskType.REGRESSION,
            target_column="target",
            primary_metric=Metric.RMSE,
            title="Predict target",
            description="A bounded test problem.",
            confirmed_by="auto",
        ).model_dump(mode="json"),
        "validation_strategy": ValidationStrategy(
            strategy=SplitStrategy.RANDOM,
            n_folds=3,
            test_size=0.2,
            rationale="Rows are independent.",
        ).model_dump(mode="json"),
    }


def _plane(tmp_path: Path) -> ControlPlane:
    sources = tmp_path / "sources"
    (sources / "demo").mkdir(parents=True)
    (sources / "demo" / "table.csv").write_text("id,target\n1,2\n", encoding="utf-8")
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(sources,),
        upload_root=tmp_path / "uploads",
    )


def test_sources_and_uploads_are_selectable_without_path_traversal(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    assert plane.data_sources() == [{"source_id": "demo", "label": "demo"}]
    uploaded = plane.upload("new.csv", b"id,target\n2,4\n")
    assert uploaded["source_id"].startswith("upload:")
    assert {item["source_id"] for item in plane.data_sources()} == {
        "demo",
        uploaded["source_id"],
    }

    try:
        plane.source_path("../artifacts")
    except KeyError:
        pass
    else:
        raise AssertionError("source selection escaped the configured root")


def test_pdf_only_upload_is_available_for_staging_but_not_structured_pipeline(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    pdf_path = tmp_path / "brief.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": "Local briefing"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    uploaded = plane.upload("brief.pdf", pdf_path.read_bytes())
    profile = plane.source_profile(uploaded["source_id"])

    assert profile["tables"] == []
    assert profile["documents"][0]["name"] == "brief.pdf"
    assert profile["documents"][0]["understanding_status"] == "needs_ocr_or_vision"
    assert "Local briefing" in str(profile["documents"])
    assert "PDF text are omitted" in profile["privacy"]

    staged = plane.stage_run(uploaded["source_id"])
    workspace = plane.staging_workspace(staged["run_id"])
    enabled = {
        item["id"]
        for item in workspace["pipeline_blueprint"]["components"]
        if item["enabled"]
    }
    assert staged["status"] == "staged"
    assert "understand-documents" in enabled
    assert "default-ml-pipeline" not in enabled
    with pytest.raises(ValueError, match="cannot be continued"):
        plane.start_staged_run(staged["run_id"])


def test_http_start_and_progress_vertical_slice(tmp_path: Path, monkeypatch) -> None:
    plane = _plane(tmp_path)
    entered = threading.Event()

    def fake_run(spec, registry, state, *, rubrics, policy, on_event):
        on_event("stage_started", {"stage": "intake"})
        entered.set()
        on_event("gate_decided", {"stage": "intake", "verdict": "auto_proceed"})
        return SimpleNamespace(status="completed", error=None)

    monkeypatch.setattr("ads.api.service.run_workflow", fake_run)
    client = TestClient(create_app(plane.store.root, plane=plane))

    assert client.get("/api/data-sources").json()[0]["source_id"] == "demo"
    response = client.post(
        "/api/runs",
        json={"source_id": "demo", **_contracts(), "candidate_limit": 1},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert entered.wait(2)

    for _ in range(20):
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        if progress["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("background run did not complete")

    assert [event["event"] for event in progress["events"]] == [
        "stage_started",
        "gate_decided",
    ]
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["progress"]["run_id"] == run_id
    assert client.get("/api/runs").json()[0]["status"] == "completed"
