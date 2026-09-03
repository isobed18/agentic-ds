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


def test_deleting_a_project_removes_its_automations_but_keeps_the_data(tmp_path: Path) -> None:
    """#181: deleting a project takes its automations with it -- nothing else
    can reach them -- while the uploaded data is reusable and stays in the
    library, exactly as the warned dialog promises."""
    plane = _plane(tmp_path)
    customers = plane.upload("customers.csv", b"customer_id,churned\n1,0\n")
    project = plane.create_project("Retention")
    plane.add_project_source(project["project_id"], customers["source_id"])
    first = plane.create_project_automation(project["project_id"], "Churn")
    second = plane.create_project_automation(project["project_id"], "Signups")

    client = TestClient(create_app(plane=plane))
    deleted = client.delete(f"/api/projects/{project['project_id']}")

    assert deleted.status_code == 200
    assert deleted.json()["project_id"] == project["project_id"]
    assert deleted.json()["automations"] == 2
    # The project and both of its automations are gone.
    assert client.get(f"/api/projects/{project['project_id']}").status_code == 404
    assert client.get(f"/api/automations/{first['automation_id']}").status_code == 404
    assert client.get(f"/api/automations/{second['automation_id']}").status_code == 404
    # The uploaded data source is reusable and survives in the data library.
    assert plane.source_path(customers["source_id"]).exists()
    # A second delete of the same project is a clean 404, not a 500.
    assert client.delete(f"/api/projects/{project['project_id']}").status_code == 404


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


def test_an_invalid_rename_is_reported_as_a_sentence_not_a_pydantic_dump(
    tmp_path: Path,
) -> None:
    """#425: `detail` is the sentence written for a person (#242).

    `AutomationDefinition.name` is `Field(min_length=1)`, and pydantic's
    `ValidationError` subclasses `ValueError`, so the route's catch-all handed
    `str(exc)` straight to the reader: the model name, the field path, the
    error code and an errors.pydantic.dev link, in English, rendered verbatim
    on a Turkish screen. The reader gets a sentence in their own language; the
    field paths stay in the log.
    """
    plane = _plane(tmp_path)
    client = TestClient(create_app(plane=plane))
    project = client.post("/api/projects", json={"name": "Retention"}).json()
    automation = client.post(
        f"/api/projects/{project['project_id']}/automations",
        json={"name": "Churn model"},
    ).json()

    rejected = client.put(
        f"/api/automations/{automation['automation_id']}",
        json={"expected_revision": automation["revision"], "changes": {"name": ""}},
        headers={"accept-language": "tr"},
    )

    assert rejected.status_code == 400
    detail = rejected.json()["detail"]
    assert detail == "Bu ayarlar geçerli değil, bu yüzden hiçbir şey değiştirilmedi."
    for leak in ("AutomationDefinition", "string_too_short", "pydantic.dev"):
        assert leak not in detail

    # And the automation is untouched, which is what the sentence promises.
    unchanged = client.get(f"/api/automations/{automation['automation_id']}").json()
    assert unchanged["name"] == "Churn model"
    assert unchanged["revision"] == automation["revision"]


def test_the_english_reader_gets_the_same_sentence_in_english(tmp_path: Path) -> None:
    """The message is translated at the edge like every other sentence the
    backend composes, so it follows the request's language rather than being
    pinned to the default."""
    plane = _plane(tmp_path)
    client = TestClient(create_app(plane=plane))
    project = client.post("/api/projects", json={"name": "Retention"}).json()
    automation = client.post(
        f"/api/projects/{project['project_id']}/automations",
        json={"name": "Churn model"},
    ).json()

    rejected = client.put(
        f"/api/automations/{automation['automation_id']}?lang=en",
        json={"expected_revision": automation["revision"], "changes": {"name": ""}},
    )

    assert rejected.json()["detail"] == "Those settings are not valid, so nothing was changed."
