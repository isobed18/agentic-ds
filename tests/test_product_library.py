"""Functional product-library endpoints and safe stale-run retention."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts.datacard import DataCard
from ads.pipeline import FinalReport
from ads.store import ArtifactStore


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


def test_experiments_models_reports_and_hardening_have_real_endpoints(
    tmp_path: Path,
) -> None:
    plane = _plane(tmp_path)
    report = FinalReport(
        evaluation_artifact_id="a" * 64,
        markdown="# Result\n\nThe measured model passed its holdout checks.",
    )
    plane.store.put(report, run_id="finished-run", stage_exec_id="report", name="final_report")
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

    experiments = client.get("/api/catalog/experiments").json()
    reports = client.get("/api/catalog/reports").json()
    hardening = client.get("/api/hardening").json()

    assert experiments[0]["deletable"] is True
    assert reports[0]["run_id"] == "finished-run"
    assert client.get(f"/api/reports/{reports[0]['artifact_id']}/download").text.startswith(
        "# Result"
    )
    assert hardening["agents"]["tool_allowlists"] is True
    assert hardening["sandbox"]["network"] == "none"
    assert hardening["sandbox"]["root_filesystem"] == "read-only"


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


WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"


def test_left_product_navigation_is_functional_not_decorative(tmp_path: Path) -> None:
    """Every sidebar destination must route somewhere and be backed by data.

    The brief calls out decorative navigation specifically. Since the UI is a
    React bundle, "the link exists" is asserted against the source that
    declares it, and "it leads to something real" is asserted against the API
    that fills it — a route to a page whose endpoint 404s would satisfy
    neither half alone.
    """
    shell = (WEB_SRC / "components" / "Shell.tsx").read_text(encoding="utf-8")
    app_routes = (WEB_SRC / "App.tsx").read_text(encoding="utf-8")

    destinations = (
        "/automation",
        "/datasets",
        "/experiments",
        "/models",
        "/reports",
        "/settings",
    )
    for destination in destinations:
        assert f'"{destination}"' in shell, f"{destination} is missing from the sidebar"
        assert f'path="{destination}"' in app_routes, f"{destination} has no route"

    client = TestClient(create_app(plane=_plane(tmp_path)))
    for endpoint in ("datasets", "experiments", "models", "reports"):
        assert client.get(f"/api/catalog/{endpoint}").status_code == 200
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
    workflows = (WEB_SRC / "pages" / "Workflows.tsx").read_text(encoding="utf-8")

    assert "confirmation: id" in api_client
    assert "setConfirming" in workflows


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

    def test_the_experiment_list_offers_it(self, tmp_path: Path) -> None:
        """`deletable` drives the UI control, so it has to agree with the API."""
        plane = _plane(tmp_path)
        _snapshot(plane, "parked-run", "awaiting_human")

        entry = next(e for e in plane.experiment_catalog() if e["run_id"] == "parked-run")

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
