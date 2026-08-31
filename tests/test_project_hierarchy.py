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


def test_automation_snapshot_is_not_listed_as_a_second_data_source(
    tmp_path: Path,
) -> None:
    """#180: private frozen inputs must not re-enter the visible source pool."""
    data = tmp_path / "data"
    data.mkdir()
    plane = ControlPlane(
        store=ArtifactStore(data / "artifacts"),
        source_roots=(data,),
        upload_root=data / "uploads",
    )
    customers = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    project = plane.create_project("Retention")
    plane.add_project_source(project["project_id"], customers["source_id"])
    automation = plane.create_project_automation(project["project_id"], "Churn")

    before = plane.data_sources()
    selected = plane.select_automation_inputs(
        automation["automation_id"],
        expected_revision=automation["revision"],
        selections=[{"source_id": customers["source_id"], "path": "customers.csv"}],
    )

    assert selected["source_id"].startswith("automation-input:")
    assert plane.data_sources() == before
    project_data = plane.project_data(project["project_id"])
    assert len(project_data) == 1
    assert project_data[0]["source_id"] == customers["source_id"]
    assert project_data[0]["files"] == ["customers.csv"]


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
