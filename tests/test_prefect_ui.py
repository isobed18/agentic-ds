"""Experiment B stays isolated while exercising the real Prefect adapter seam."""

from __future__ import annotations

from types import SimpleNamespace

from alternatives.prefect_ui import create_prefect_app
from alternatives.prefect_ui.app import prefect_runner
from fastapi.testclient import TestClient

from ads.api import ControlPlane
from ads.store import ArtifactStore


def test_experiment_b_has_its_own_ui_and_reuses_safe_api(tmp_path) -> None:
    source = tmp_path / "sources" / "demo"
    source.mkdir(parents=True)
    (source / "table.csv").write_text("id,target\n1,2\n", encoding="utf-8")
    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "sources",),
        upload_root=tmp_path / "uploads",
        workflow_runner=lambda *args, **kwargs: None,
    )
    client = TestClient(create_prefect_app(plane=plane))

    page = client.get("/")

    assert page.status_code == 200
    assert "Experiment B · Prefect-backed" in page.text
    assert "Start Prefect-backed run" in page.text
    assert "Planner / Orchestrator" not in page.text
    assert client.get("/api/workflow").json()["workflow"] == "agent-backed-full"
    assert client.get("/api/data-sources/demo/profile").json()["tables"][0]["rows"] == 1


def test_prefect_runner_executes_the_existing_outer_flow(monkeypatch) -> None:
    expected = SimpleNamespace(status="completed")
    captured = {}

    def fake_build(spec, registry, state, **kwargs):
        captured.update(spec=spec, registry=registry, state=state, kwargs=kwargs)
        return lambda: expected

    monkeypatch.setattr("alternatives.prefect_ui.app.build_prefect_flow", fake_build)
    spec, registry, state = object(), object(), object()

    assert prefect_runner(spec, registry, state, on_event="callback") is expected
    assert captured == {
        "spec": spec,
        "registry": registry,
        "state": state,
        "kwargs": {"on_event": "callback"},
    }
