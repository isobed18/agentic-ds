"""Functional product-library endpoints and safe stale-run retention."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts import Metric, TaskType
from ads.contracts.datacard import DataCard
from ads.contracts.training import (
    CandidateResult,
    MetricEvaluation,
    ModelBlobReference,
    TrainingReport,
)
from ads.pipeline import FinalReport
from ads.store import ArtifactStore


def _trained_model(*, blob_id: str | None) -> TrainingReport:
    """A minimal winner-only training report, optionally carrying a saved blob."""
    return TrainingReport(
        task_type=TaskType.REGRESSION,
        primary_metric=Metric.RMSE,
        winner_id="cand-1",
        results=[
            CandidateResult(
                candidate_id="cand-0",
                display_name="Baseline",
                estimator_class="sklearn.dummy.DummyRegressor",
                is_baseline=True,
                metrics=[
                    MetricEvaluation(
                        metric=Metric.RMSE,
                        fold_scores=[1.9, 2.1],
                        cv_mean=2.0,
                        cv_std=0.1,
                        holdout_score=2.05,
                    )
                ],
            ),
            CandidateResult(
                candidate_id="cand-1",
                display_name="Ridge",
                estimator_class="sklearn.linear_model.Ridge",
                metrics=[
                    MetricEvaluation(
                        metric=Metric.RMSE,
                        fold_scores=[0.9, 1.1],
                        cv_mean=1.0,
                        cv_std=0.1,
                        holdout_score=1.05,
                    )
                ],
            ),
        ],
        model_blob=ModelBlobReference(artifact_id=blob_id) if blob_id else None,
    )


def _plane(tmp_path: Path) -> ControlPlane:
    sources = tmp_path / "sources"
    sources.mkdir()
    uploads = tmp_path / "uploads"
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(sources,),
        upload_root=uploads,
    )


def test_dataset_catalog_is_useful_and_never_serves_source_values(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    source = plane.source_roots[0] / "customers"
    source.mkdir()
    secret = "person-unique-secret-9271"
    (source / "customers.csv").write_text(
        f"customer_id,age,churned\n{secret},35,1\nsecond-person,44,0\n",
        encoding="utf-8",
    )

    response = TestClient(create_app(plane=plane)).get("/api/catalog/datasets")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["tables"] == 1
    assert payload["rows"] == 2
    assert payload["table_summaries"][0]["name"] == "customers"
    assert secret not in response.text
    assert "second-person" not in response.text


def test_artifact_preview_reports_the_real_type_for_unrecognised_artifacts(
    tmp_path: Path,
) -> None:
    """An artifact the preview did not special-case reported a generic
    "artifact" type, so it opened under a meaningless "ARTIFACT" eyebrow (#74).
    The index knows the real type; the preview now reports it."""
    plane = _plane(tmp_path)
    card = DataCard(
        table_name="customers",
        source_uri="fixture://customers.csv",
        source_format="csv",
        n_rows=2,
        n_columns=1,
        columns=[],
        profiled_rows=2,
    )
    ref = plane.store.put(card, run_id="run-x", stage_exec_id="intake")

    preview = plane.artifact_preview(ref.artifact_id)

    assert preview["artifact_type"] == "data_card"
    # Generic previews are deliberately privacy-safe, but they still have to
    # carry enough structure for the UI to show something useful instead of an
    # empty modal (#164). Scalars and collection sizes meet that contract
    # without exposing any table rows.
    assert preview["fields"]["table_name"] == "customers"
    assert preview["fields"]["n_rows"] == 2
    assert preview["collection_sizes"]["columns"] == 0


def test_dataset_catalog_paginates_and_only_profiles_the_page(tmp_path: Path) -> None:
    """The catalog profiled every source on every load, so /datasets slowed
    without bound as datasets accumulated (#72). It now returns one page and
    profiles only the sources on it."""
    plane = _plane(tmp_path)
    for i in range(30):
        source = plane.source_roots[0] / f"set-{i:03d}"
        source.mkdir()
        (source / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    profiled: list[str] = []
    original = plane.source_profile

    def counting_profile(source_id: str) -> dict:
        profiled.append(source_id)
        return original(source_id)

    plane.source_profile = counting_profile  # type: ignore[method-assign]
    client = TestClient(create_app(plane=plane))

    first = client.get("/api/catalog/datasets?page=1&page_size=25").json()
    assert first["total"] == 30
    assert first["page_size"] == 25
    assert len(first["items"]) == 25
    # Only the visible page was profiled, not all 30 sources.
    assert len(profiled) == 25

    second = client.get("/api/catalog/datasets?page=2&page_size=25").json()
    assert len(second["items"]) == 5
    # Stable order: no source appears on both pages.
    first_ids = {item["source_id"] for item in first["items"]}
    second_ids = {item["source_id"] for item in second["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_dataset_catalog_search_filters_by_label(tmp_path: Path) -> None:
    """A search bar filters by label so browsing is not the only option (#72)."""
    plane = _plane(tmp_path)
    for name in ("customers", "orders", "customer_events"):
        source = plane.source_roots[0] / name
        source.mkdir()
        (source / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = TestClient(create_app(plane=plane)).get(
        "/api/catalog/datasets?search=customer"
    ).json()

    assert result["total"] == 2
    labels = sorted(item["label"] for item in result["items"])
    assert labels == ["customer_events", "customers"]


def test_dataset_catalog_summarises_pdf_only_sources(tmp_path: Path) -> None:
    """A PDF-only source used to render as an empty "0 tables" row because the
    catalog was built solely from `profile["tables"]` and never looked at
    `profile["documents"]` (#69). Assert the document summary is now carried."""
    from test_kesif_pdf import _basit_pdf

    plane = _plane(tmp_path)
    source = plane.source_roots[0] / "contracts"
    source.mkdir()
    (source / "agreement.pdf").write_bytes(
        _basit_pdf(["Supplier agreement", "Payment terms are thirty days."])
    )

    response = TestClient(create_app(plane=plane)).get("/api/catalog/datasets")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["tables"] == 0
    assert payload["documents"] == 1
    assert payload["document_pages"] == 1
    assert payload["document_summaries"][0]["name"] == "agreement.pdf"


def test_report_download_and_hardening_keep_their_real_endpoints(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    report = FinalReport(
        evaluation_artifact_id="a" * 64,
        markdown="# Result\n\nThe measured model passed its holdout checks.",
    )
    report_ref = plane.store.put(
        report, run_id="finished-run", stage_exec_id="report", name="final_report"
    )
    snapshot = plane._run_state_root / "finished-run.json"  # noqa: SLF001
    snapshot.write_text(
        json.dumps(
            {
                "run_id": "finished-run",
                "configuration": {"mode": "manual", "problem_title": "Churn"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T01:00:00+00:00",
                "status": "completed",
                "current_stage": None,
                "events": [],
                "attempts": [],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(plane=plane))

    hardening = client.get("/api/hardening").json()

    assert client.get(f"/api/reports/{report_ref.artifact_id}/download").text.startswith(
        "# Result"
    )
    assert hardening["agents"]["tool_allowlists"] is True
    assert hardening["sandbox"]["network"] == "none"
    assert hardening["sandbox"]["root_filesystem"] == "read-only"


def test_ui_orphaned_global_catalogue_routes_are_not_registered(tmp_path: Path) -> None:
    """#155: project and automation views own these outputs now. Leaving the
    old global reads registered preserves an undocumented second product model
    that no UI can reach and lets new callers accidentally bypass containment."""
    client = TestClient(create_app(plane=_plane(tmp_path)))

    for path in (
        "/api/catalog/experiments",
        "/api/catalog/models",
        "/api/catalog/reports",
    ):
        assert client.get(path).status_code == 404


def test_project_contents_are_owned_by_execution_history_not_global_catalogues(
    tmp_path: Path,
) -> None:
    """#111: two projects may reuse one input, but one project's output view
    must never acquire the other project's report from the global store."""
    plane = _plane(tmp_path)
    uploaded = plane.upload("customers.csv", b"customer_id,churned\n1,0\n2,1\n")
    first_project = plane.create_project("Retention")
    second_project = plane.create_project("Campaign")
    plane.add_project_source(first_project["project_id"], uploaded["source_id"])
    plane.add_project_source(second_project["project_id"], uploaded["source_id"])
    first = plane.create_project_automation(first_project["project_id"], "Churn model")
    second = plane.create_project_automation(second_project["project_id"], "Campaign report")
    plane.automation_store.attach_execution(
        first["automation_id"], run_id="retention-run", source_id=uploaded["source_id"]
    )
    plane.automation_store.attach_execution(
        second["automation_id"], run_id="campaign-run", source_id=uploaded["source_id"]
    )
    for run_id, heading in (("retention-run", "Retention"), ("campaign-run", "Campaign")):
        report = FinalReport(evaluation_artifact_id="a" * 64, markdown=f"# {heading}")
        plane.store.put(report, run_id=run_id, stage_exec_id="report", name="final_report")
        (plane._run_state_root / f"{run_id}.json").write_text(  # noqa: SLF001
            json.dumps(
                {
                    "run_id": run_id,
                    "configuration": {},
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T01:00:00+00:00",
                    "status": "completed",
                    "current_stage": None,
                    "events": [],
                    "attempts": [],
                    "error": None,
                }
            ),
            encoding="utf-8",
        )

    response = TestClient(create_app(plane=plane)).get(
        f"/api/projects/{first_project['project_id']}/contents"
    )

    assert response.status_code == 200
    contents = response.json()
    assert contents["data"][0]["source_id"] == uploaded["source_id"]
    assert [item["name"] for item in contents["automations"]] == ["Churn model"]
    assert [item["run_id"] for item in contents["executions"]] == ["retention-run"]
    assert [item["run_id"] for item in contents["reports"]] == ["retention-run"]
    assert contents["reports"][0]["automation_name"] == "Churn model"
    automation_contents = TestClient(create_app(plane=plane)).get(
        f"/api/automations/{first['automation_id']}/contents"
    ).json()
    assert automation_contents["project"]["project_id"] == first_project["project_id"]
    assert [item["run_id"] for item in automation_contents["reports"]] == ["retention-run"]
    # The two top-level projects reuse one source, but aggregate outputs remain
    # owned by parent -> child automation history. Campaign's report must not
    # surface in Retention merely because both pools contain the same source.
    outputs = json.dumps(
        {section: contents[section] for section in ("executions", "models", "reports")}
    )
    assert "Campaign" not in outputs


def test_home_leads_with_projects_that_need_attention_and_finds_output_by_label(
    tmp_path: Path,
) -> None:
    """#111: the home page is the project-first browsing surface. It leads with
    the project waiting on a person, reports each project's state, and lets an
    output be rediscovered by label when its project has been forgotten."""
    plane = _plane(tmp_path)
    uploaded = plane.upload("customers.csv", b"customer_id,churned\n1,0\n2,1\n")
    waiting_project = plane.create_project("Churn campaign")
    finished_project = plane.create_project("Retention model")
    waiting = plane.create_project_automation(waiting_project["project_id"], "Campaign report")
    finished = plane.create_project_automation(finished_project["project_id"], "Retention flow")
    plane.automation_store.attach_execution(
        finished["automation_id"], run_id="finished-run", source_id=uploaded["source_id"]
    )
    plane.automation_store.attach_execution(
        waiting["automation_id"], run_id="waiting-run", source_id=uploaded["source_id"]
    )
    _snapshot(plane, "finished-run", "completed")
    _snapshot(plane, "waiting-run", "awaiting_human")
    report = FinalReport(evaluation_artifact_id="a" * 64, markdown="# Holdout summary")
    plane.store.put(report, run_id="finished-run", stage_exec_id="report", name="final_report")
    client = TestClient(create_app(plane=plane))

    overview = client.get("/api/home").json()

    assert overview["totals"] == {
        "projects": 2,
        "executions": 2,
        "running": 0,
        "awaiting_human": 1,
        "failed": 0,
        "completed": 1,
    }
    # The project waiting on a person leads, regardless of recency.
    assert overview["projects"][0]["name"] == "Churn campaign"
    assert overview["projects"][0]["state"] == "awaiting_human"
    assert overview["projects"][0]["needs_attention"] is True
    states = {p["name"]: p["state"] for p in overview["projects"]}
    assert states["Retention model"] == "completed"
    # The report is discoverable by its heading, labelled with its owning project.
    produced = overview["recent"]
    assert [item["kind"] for item in produced] == ["report"]
    assert produced[0]["project_name"] == "Retention model"
    assert produced[0]["label"] == "Holdout summary"

    # Search spans both project names and produced-output labels.
    by_output = client.get("/api/home?search=holdout").json()
    assert [p["name"] for p in by_output["projects"]] == []
    assert [item["run_id"] for item in by_output["recent"]] == ["finished-run"]
    by_project = client.get("/api/home?search=churn").json()
    assert [p["name"] for p in by_project["projects"]] == ["Churn campaign"]


def test_executed_reusable_source_cannot_be_mutated(tmp_path: Path) -> None:
    """#111: output history is immutable, so its reusable input must not be
    edited underneath every project that references it."""
    plane = _plane(tmp_path)
    uploaded = plane.upload("customers.csv", b"customer_id,churned\n1,0\n2,1\n")
    project = plane.automation_store.create("Retention")
    plane.automation_store.attach_execution(
        project.automation_id, run_id="retention-run", source_id=uploaded["source_id"]
    )

    with pytest.raises(ValueError, match="used by an execution is immutable"):
        plane.upload("orders.csv", b"order_id,total\n1,10\n", source_id=uploaded["source_id"])
    with pytest.raises(ValueError, match="used by an execution is immutable"):
        plane.remove_upload_file(uploaded["source_id"], "customers.csv")

    assert plane.data_sources()[0]["files"] == ["customers.csv"]


def test_delete_run_requires_exact_confirmation_and_preserves_shared_payload(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    card = DataCard(
        table_name="shared",
        source_uri="fixture://shared.csv",
        source_format="csv",
        n_rows=1,
        n_columns=0,
        columns=[],
        profiled_rows=1,
    )
    reference = plane.store.put(card, run_id="old-run", stage_exec_id="intake")
    plane.store.put(card, run_id="kept-run", stage_exec_id="intake")
    snapshot = plane._run_state_root / "old-run.json"  # noqa: SLF001
    snapshot.write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "configuration": {"mode": "manual"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T01:00:00+00:00",
                "status": "completed",
                "current_stage": None,
                "events": [],
                "attempts": [],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(plane=plane))

    refused = client.post("/api/runs/old-run/delete", json={"confirmation": "wrong"})
    deleted = client.post("/api/runs/old-run/delete", json={"confirmation": "old-run"})

    assert refused.status_code == 409
    assert deleted.status_code == 200
    assert plane.store.list("old-run") == []
    assert plane.store.list("kept-run")
    assert plane.store.exists(reference.artifact_id)
    assert not snapshot.exists()


def test_delete_run_detaches_automation_history_and_unlocks_input_changes(
    tmp_path: Path,
) -> None:
    """#334: deleting the artifacts alone left a ghost execution id on the
    automation, so its run count stayed non-zero and its input binding could
    never be changed again."""
    plane = _plane(tmp_path)
    automation = plane.automation_store.create("Reusable analysis")
    attached = plane.automation_store.attach_execution(
        automation.automation_id,
        run_id="old-run",
        source_id="upload:original",
    )
    card = DataCard(
        table_name="customers",
        source_uri="fixture://customers.csv",
        source_format="csv",
        n_rows=1,
        n_columns=0,
        columns=[],
        profiled_rows=1,
    )
    plane.store.put(card, run_id="old-run", stage_exec_id="intake")
    snapshot = plane._run_state_root / "old-run.json"  # noqa: SLF001
    snapshot.write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "configuration": {"mode": "manual"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T01:00:00+00:00",
                "status": "completed",
                "current_stage": None,
                "events": [],
                "attempts": [],
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    deleted = TestClient(create_app(plane=plane)).post(
        "/api/runs/old-run/delete",
        json={"confirmation": "old-run"},
    )

    assert deleted.status_code == 200, deleted.text
    detached = plane.automation_store.get(automation.automation_id)
    assert detached.execution_ids == ()
    rebound = plane.automation_store.update(
        automation.automation_id,
        expected_revision=detached.revision,
        changes={"source_id": "upload:replacement"},
    )
    assert rebound.source_id == "upload:replacement"
    assert attached.execution_ids == ("old-run",)


WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def test_saved_model_is_downloadable_and_an_unsaved_one_reports_no_blob(
    tmp_path: Path,
) -> None:
    """#166 ends at "the model can be downloaded", but only reports had a route.

    A completed run leaves a trained-model artifact whose fitted pipeline lives
    in a separate joblib blob; without a download route the model could not be
    taken off the machine. A model whose blob was never saved has nothing to
    hand back and must say so rather than stream an empty or wrong file.
    """
    plane = _plane(tmp_path)
    blob_id = "a" * 64
    (plane.store.blob_dir(blob_id) / "model.joblib").write_bytes(b"FITTED-PIPELINE-BYTES")
    saved = plane.store.put(
        _trained_model(blob_id=blob_id),
        run_id="model-run",
        stage_exec_id="training",
        name="trained_model",
    )
    unsaved = plane.store.put(
        _trained_model(blob_id=None),
        run_id="deferred-run",
        stage_exec_id="training",
        name="trained_model",
    )
    client = TestClient(create_app(plane=plane))

    ok = client.get(f"/api/models/{saved.artifact_id}/download")
    assert ok.status_code == 200
    assert ok.content == b"FITTED-PIPELINE-BYTES"
    disposition = ok.headers["content-disposition"]
    assert "attachment" in disposition and disposition.endswith('model.joblib"')

    assert client.get(f"/api/models/{unsaved.artifact_id}/download").status_code == 404
    assert client.get("/api/models/" + "f" * 64 + "/download").status_code == 404


def test_model_and_report_deletion_removes_only_that_artifact(tmp_path: Path) -> None:
    """#258: a model or report can be deleted on its own, without touching its run.

    Deleting one artifact must not delete the run that produced it, nor any
    other artifact indexed under that run -- unlike `delete_run`, which takes
    everything. Attempting to delete a model through the report route (or vice
    versa) is refused, since the wrong route silently deleting the wrong kind
    of artifact would be far worse than a 400.
    """
    plane = _plane(tmp_path)
    model_ref = plane.store.put(
        _trained_model(blob_id=None), run_id="run-1", stage_exec_id="training", name="trained_model"
    )
    report_ref = plane.store.put(
        FinalReport(evaluation_artifact_id="a" * 64, markdown="# Result\n\nOK."),
        run_id="run-1",
        stage_exec_id="report",
        name="final_report",
    )
    client = TestClient(create_app(plane=plane))

    assert client.delete(f"/api/models/{report_ref.artifact_id}").status_code == 400
    assert client.delete(f"/api/reports/{model_ref.artifact_id}").status_code == 400
    assert client.delete("/api/models/" + "f" * 64).status_code == 404

    deleted = client.delete(f"/api/models/{model_ref.artifact_id}")
    assert deleted.status_code == 200
    assert deleted.json()["artifact_id"] == model_ref.artifact_id
    remaining = {ref.artifact_id for ref in plane.store.list("run-1")}
    assert model_ref.artifact_id not in remaining
    assert report_ref.artifact_id in remaining

    assert client.delete(f"/api/reports/{report_ref.artifact_id}").status_code == 200
    assert plane.store.list("run-1") == []


def test_left_product_navigation_is_functional_not_decorative(tmp_path: Path) -> None:
    """Every sidebar destination must route somewhere and be backed by data.

    The brief calls out decorative navigation specifically. Since the UI is a
    React bundle, "the link exists" is asserted against the source that
    declares it, and "it leads to something real" is asserted against the API
    that fills it — a route to a page whose endpoint 404s would satisfy
    neither half alone.

    #111 makes projects the only top-level concept, so the sidebar is Home,
    Projects and Settings; the four global catalogues (Datasets, Experiments,
    Models, Reports) are gone as destinations and their bookmarks redirect to
    the project-first home rather than 404.
    """
    shell = (WEB_SRC / "components" / "Shell.tsx").read_text(encoding="utf-8")
    app_routes = (WEB_SRC / "App.tsx").read_text(encoding="utf-8")

    destinations = ("/", "/projects", "/settings")
    for destination in destinations:
        assert f'"{destination}"' in shell, f"{destination} is missing from the sidebar"
        assert f'path="{destination}"' in app_routes, f"{destination} has no route"

    # #111 settles the customer-facing route on /projects; the old /automation
    # deep links still resolve, carrying their ?automation=<id> query across.
    assert 'path="/automation" element={<RedirectWithQuery to="/projects"' in app_routes

    # The removed catalogues are no longer sidebar destinations, and their old
    # deep links redirect home instead of dead-ending.
    for gone in ("/datasets", "/experiments", "/models", "/reports"):
        assert f'label: "{gone}"' not in shell
        assert f'to="{gone}"' not in shell
        assert f'<Route path="{gone}" element={{<Navigate to="/"' in app_routes

    client = TestClient(create_app(plane=_plane(tmp_path)))
    # Home, Projects and Settings each lead to a real endpoint.
    assert client.get("/api/home").status_code == 200
    assert client.get("/api/automations").status_code == 200
    assert client.get("/api/hardening").status_code == 200


def test_sidebar_and_planner_collapse_and_the_account_menu_exists() -> None:
    """Collapsibility and the account surface are explicit product requirements."""
    shell = (WEB_SRC / "components" / "Shell.tsx").read_text(encoding="utf-8")
    planner = (WEB_SRC / "components" / "PlannerPanel.tsx").read_text(encoding="utf-8")

    # Collapsed state persists, so the choice survives a reload.
    assert "localStorage" in shell
    assert "collapsed" in shell
    assert "onToggle" in planner

    for item in ("Account", "Preferences", "Sign out"):
        assert item in shell, f"account menu is missing {item!r}"


def test_run_deletion_asks_before_it_destroys() -> None:
    """The API answers 409 without an exact-id confirmation; the UI must send one.

    A delete control that fires straight at the endpoint would either fail or,
    if the guard were ever relaxed, silently destroy a run's artifacts on a
    single stray click.
    """
    api_client = (WEB_SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    # #434: Workflows.tsx, the page this test used to read, was unreachable
    # dead code and is gone; the live delete control is the one on the
    # automation workspace, guarding `deleteRun` with a native confirm.
    automation_workspace = (
        WEB_SRC / "pages" / "AutomationWorkspace.tsx"
    ).read_text(encoding="utf-8")

    assert "confirmation: id" in api_client
    assert "window.confirm(" in automation_workspace


def _snapshot(plane: ControlPlane, run_id: str, status: str) -> Path:
    """Write a run-state snapshot directly, as a previous process would have."""
    path = plane._run_state_root / f"{run_id}.json"  # noqa: SLF001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "configuration": {"mode": "manual"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:05:00+00:00",
                "status": status,
                "current_stage": "training",
                "events": [],
                "attempts": [],
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    return path


class TestAbandonedRunsAreNotReportedAsLive:
    """A run whose worker process exited must not read as still running.

    Runs execute in worker threads owned by the API process. After a restart or
    a crash the snapshot still says `running`, but nothing will ever advance it.
    Left alone that is a permanent lie with three compounding effects: the run
    list shows activity forever, a polling client never stops refreshing, and
    `delete_run` refuses to remove it as "active" — so the stale run cannot be
    cleared through the product at all.
    """

    def test_in_flight_snapshot_without_a_worker_reads_as_interrupted(self, tmp_path: Path) -> None:
        plane = _plane(tmp_path)
        _snapshot(plane, "abandoned-run", "running")
        client = TestClient(create_app(plane=plane))

        progress = client.get("/api/runs/abandoned-run/progress").json()

        assert progress["status"] == "interrupted"
        assert "exited before it finished" in progress["error"]

    def test_awaiting_human_survives_a_restart(self, tmp_path: Path) -> None:
        """The counter-case, and the reason this cannot key off "not terminal".

        `awaiting_human` is durable by design — it is what the human-resume
        feature depends on. Downgrading it would destroy a run that is
        legitimately parked waiting for a person.
        """
        plane = _plane(tmp_path)
        _snapshot(plane, "parked-run", "awaiting_human")
        client = TestClient(create_app(plane=plane))

        assert client.get("/api/runs/parked-run/progress").json()["status"] == "awaiting_human"

    def test_an_interrupted_run_can_be_deleted(self, tmp_path: Path) -> None:
        plane = _plane(tmp_path)
        snapshot = _snapshot(plane, "abandoned-run", "running")
        client = TestClient(create_app(plane=plane))

        deleted = client.post(
            "/api/runs/abandoned-run/delete", json={"confirmation": "abandoned-run"}
        )

        assert deleted.status_code == 200
        assert not snapshot.exists()

    def test_a_genuinely_live_run_is_still_protected(self, tmp_path: Path) -> None:
        """The guard must keep working for runs this process really is running."""
        plane = _plane(tmp_path)
        _snapshot(plane, "live-run", "running")
        plane._runtime_runs["live-run"] = SimpleNamespace(  # noqa: SLF001
            run_id="live-run",
            status="running",
            configuration={"mode": "manual"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:05:00+00:00",
            current_stage="training",
            events=[],
            error=None,
            source_id=None,
            state=SimpleNamespace(attempts=[]),
        )
        client = TestClient(create_app(plane=plane))

        refused = client.post("/api/runs/live-run/delete", json={"confirmation": "live-run"})

        assert refused.status_code == 409
        assert "active run" in refused.json()["detail"]

    def test_the_run_list_and_the_detail_view_agree(self, tmp_path: Path) -> None:
        """Both read through `progress`, so neither can report stale activity."""
        plane = _plane(tmp_path)
        _snapshot(plane, "abandoned-run", "queued")
        client = TestClient(create_app(plane=plane))

        listed = {r["run_id"]: r["status"] for r in client.get("/api/runs").json()}
        detail = client.get("/api/runs/abandoned-run/progress").json()["status"]

        assert listed["abandoned-run"] == "interrupted"
        assert detail == "interrupted"


class TestAParkedRunCanBeCleared:
    """A run waiting for an answer nobody will give must still be removable.

    `awaiting_human` is durable on purpose, and `progress` will never downgrade
    it. That made it the one status with no exit: the run sits in the list
    forever because the only thing that could end it is the answer nobody is
    going to type. A parked run holds no worker thread, so removing it races
    with nothing; the exact-id confirmation is what keeps it deliberate.
    """

    def test_a_parked_run_is_deleted(self, tmp_path: Path) -> None:
        plane = _plane(tmp_path)
        snapshot = _snapshot(plane, "parked-run", "awaiting_human")
        client = TestClient(create_app(plane=plane))

        deleted = client.post("/api/runs/parked-run/delete", json={"confirmation": "parked-run"})

        assert deleted.status_code == 200, deleted.text
        assert not snapshot.exists()

    def test_it_still_needs_the_id_back(self, tmp_path: Path) -> None:
        plane = _plane(tmp_path)
        snapshot = _snapshot(plane, "parked-run", "awaiting_human")
        client = TestClient(create_app(plane=plane))

        refused = client.post("/api/runs/parked-run/delete", json={"confirmation": ""})

        assert refused.status_code == 409
        assert snapshot.exists(), "an unconfirmed delete must not remove anything"

    def test_the_project_execution_list_offers_it(self, tmp_path: Path) -> None:
        """The project execution read model drives the delete control, so it
        must retain the summary behaviour after the global catalogue is gone."""
        plane = _plane(tmp_path)
        uploaded = plane.upload("customers.csv", b"customer_id\n1\n")
        project = plane.create_project("Retention")
        automation = plane.create_project_automation(project["project_id"], "Churn flow")
        plane.automation_store.attach_execution(
            automation["automation_id"],
            run_id="parked-run",
            source_id=uploaded["source_id"],
        )
        _snapshot(plane, "parked-run", "awaiting_human")

        contents = plane.project_contents(project["project_id"])
        entry = next(e for e in contents["executions"] if e["run_id"] == "parked-run")

        assert entry["deletable"] is True

    def test_a_running_run_is_still_refused(self, tmp_path: Path) -> None:
        """The relaxation must not reach a run a worker is advancing."""
        plane = _plane(tmp_path)
        snapshot = _snapshot(plane, "live-run", "running")
        plane._runtime_runs["live-run"] = SimpleNamespace(  # noqa: SLF001
            run_id="live-run",
            status="running",
            configuration={"mode": "manual"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:05:00+00:00",
            current_stage="training",
            events=[],
            error=None,
            source_id=None,
            state=SimpleNamespace(attempts=[]),
            outcome=None,
        )
        client = TestClient(create_app(plane=plane))

        refused = client.post("/api/runs/live-run/delete", json={"confirmation": "live-run"})

        assert refused.status_code == 409
        assert snapshot.exists()
