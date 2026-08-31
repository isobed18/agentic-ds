"""Projects own open data pools and several input-frozen automations."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.store import ArtifactStore


def _plane(tmp_path: Path) -> ControlPlane:
    data = tmp_path / "data"
    data.mkdir()
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(data,),
        upload_root=tmp_path / "uploads",
    )


def test_project_routes_own_data_and_several_automations(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    source = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    client = TestClient(create_app(plane=plane))

    project = client.post("/api/projects", json={"name": "Retention"}).json()
    attached = client.post(
        f"/api/projects/{project['project_id']}/sources",
        json={"source_id": source["source_id"]},
    )
    first = client.post(
        f"/api/projects/{project['project_id']}/automations",
        json={"name": "Churn model"},
    )
    second = client.post(
        f"/api/projects/{project['project_id']}/automations",
        json={"name": "Segment report"},
    )

    assert attached.status_code == 200
    assert [item["name"] for item in client.get(
        f"/api/projects/{project['project_id']}/automations"
    ).json()] == ["Churn model", "Segment report"]
    assert client.get(f"/api/projects/{project['project_id']}/data").json()[0][
        "files"
    ] == ["customers.csv"]
    assert first.json()["automation_id"] != second.json()["automation_id"]


def test_automation_snapshot_freezes_while_project_pool_stays_open(tmp_path: Path) -> None:
    """#157: pool growth and automation immutability are opposite invariants;
    the snapshot proves adding pool data cannot change bytes behind an old run."""
    plane = _plane(tmp_path)
    customers = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    project = plane.create_project("Retention")
    plane.add_project_source(project["project_id"], customers["source_id"])
    automation = plane.create_project_automation(project["project_id"], "Churn")
    selected = plane.select_automation_inputs(
        automation["automation_id"],
        expected_revision=automation["revision"],
        selections=[{"source_id": customers["source_id"], "path": "customers.csv"}],
    )
    snapshot = plane.source_path(selected["source_id"])
    assert (snapshot / "0000-customers.csv").read_bytes() == b"customer_id,churned\n1,0\n"
    assert plane.source_profile(selected["source_id"])["tables"][0]["rows"] == 1

    plane.attach_automation_execution(
        automation["automation_id"], run_id="run-a1b2c3d4", source_id=selected["source_id"]
    )
    campaigns = plane.upload("campaigns.csv", b"campaign_id,cost\n1,10\n")
    expanded = plane.add_project_source(project["project_id"], campaigns["source_id"])
    assert expanded["source_ids"] == [customers["source_id"], campaigns["source_id"]]
    with pytest.raises(ValueError, match="cannot change after its first execution"):
        plane.select_automation_inputs(
            automation["automation_id"],
            expected_revision=plane.automation(automation["automation_id"])["revision"],
            selections=[{"source_id": campaigns["source_id"], "path": "campaigns.csv"}],
        )
    assert (snapshot / "0000-customers.csv").read_bytes() == b"customer_id,churned\n1,0\n"


def test_automation_rejects_files_outside_its_project_pool(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    outside = plane.upload("outside.csv", b"id\n1\n")
    project = plane.create_project("Retention")
    automation = plane.create_project_automation(project["project_id"], "Churn")

    with pytest.raises(ValueError, match="must come from its project data pool"):
        plane.select_automation_inputs(
            automation["automation_id"],
            expected_revision=automation["revision"],
            selections=[{"source_id": outside["source_id"], "path": "outside.csv"}],
        )


def test_deleting_an_automation_detaches_it_and_drops_its_snapshot(tmp_path: Path) -> None:
    """#185: the UI lost its delete control, so the leftovers went unnoticed.

    Both leftovers are only reachable through the definition being deleted: the
    parent's id list is what the project card counts, and the input snapshot is
    named by a token stored on the automation itself.
    """
    plane = _plane(tmp_path)
    customers = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    project = plane.create_project("Retention")
    plane.add_project_source(project["project_id"], customers["source_id"])
    kept = plane.create_project_automation(project["project_id"], "Churn")
    doomed = plane.create_project_automation(project["project_id"], "Untitled automation")
    selected = plane.select_automation_inputs(
        doomed["automation_id"],
        expected_revision=doomed["revision"],
        selections=[{"source_id": customers["source_id"], "path": "customers.csv"}],
    )
    snapshot = plane.source_path(selected["source_id"])
    assert snapshot.is_dir()

    client = TestClient(create_app(plane=plane))
    deleted = client.delete(f"/api/automations/{doomed['automation_id']}")

    assert deleted.status_code == 200
    assert deleted.json()["automation_id"] == doomed["automation_id"]
    assert not snapshot.exists()
    assert plane.project(project["project_id"])["automation_ids"] == [kept["automation_id"]]
    assert [item["name"] for item in client.get(
        f"/api/projects/{project['project_id']}/automations"
    ).json()] == ["Churn"]
    assert client.get(f"/api/automations/{doomed['automation_id']}").status_code == 404
    assert client.delete(f"/api/automations/{doomed['automation_id']}").status_code == 404


def test_deleting_an_executed_automation_keeps_the_bytes_its_runs_read(tmp_path: Path) -> None:
    """#185: the dialog promises the execution history survives the deletion.

    A run resolves its source through the snapshot path, so reclaiming that
    directory would leave the kept history unable to read its own inputs.
    """
    plane = _plane(tmp_path)
    customers = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    project = plane.create_project("Retention")
    plane.add_project_source(project["project_id"], customers["source_id"])
    automation = plane.create_project_automation(project["project_id"], "Churn")
    selected = plane.select_automation_inputs(
        automation["automation_id"],
        expected_revision=automation["revision"],
        selections=[{"source_id": customers["source_id"], "path": "customers.csv"}],
    )
    plane.attach_automation_execution(
        automation["automation_id"], run_id="run-a1b2c3d4", source_id=selected["source_id"]
    )
    snapshot = plane.source_path(selected["source_id"])

    plane.delete_automation(automation["automation_id"])

    assert (snapshot / "0000-customers.csv").read_bytes() == b"customer_id,churned\n1,0\n"
    assert plane.project(project["project_id"])["automation_ids"] == []
