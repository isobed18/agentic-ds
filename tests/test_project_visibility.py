"""Publishing a project, and taking it back (#207).

#206 left every project owner-only, which is a floor rather than a policy: a
person who wants colleagues in a project had no way to say so. This adds the one
value that opens it -- `public`, meaning every signed-in account, deliberately
not `teams.py`'s `team`, which means only people who share a team with the owner.

The asymmetry is the part worth pinning down. A public project is a workspace,
so a visitor can work in it; the single thing only the owner may do is flip the
switch. And unpublishing has to bite immediately, or "private" is a suggestion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.store import ArtifactStore

OWNER = "ishak-ads"
STRANGER = "gonenc-ads"


@pytest.fixture
def plane(tmp_path: Path) -> ControlPlane:
    data = tmp_path / "data"
    data.mkdir()
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(data,),
        upload_root=tmp_path / "uploads",
    )


def _client(plane: ControlPlane, username: list[str | None]) -> TestClient:
    """A signed-in client, standing in for the auth middleware."""
    app = create_app(plane=plane)

    @app.middleware("http")
    async def _sign_in(request, call_next):  # type: ignore[no-untyped-def]
        request.state.username = username[0]
        return await call_next(request)

    return TestClient(app)


def test_a_new_project_starts_private(plane: ControlPlane) -> None:
    created = plane.create_project("Retention", owner=OWNER)
    assert created["visibility"] == "private"
    assert plane.list_projects(viewer=STRANGER) == []


def test_publishing_opens_every_read_the_owner_check_gates(plane: ControlPlane) -> None:
    """A project that is visible in the list but 404s when opened would be
    worse than no toggle, so every gated read is asserted, not just the list."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.create_project_automation(project_id, "Churn", viewer=OWNER)

    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)

    assert [p["name"] for p in plane.list_projects(viewer=STRANGER)] == ["Retention"]
    assert plane.project(project_id, viewer=STRANGER)["name"] == "Retention"
    assert plane.project_data(project_id, viewer=STRANGER) == []
    assert [a["name"] for a in plane.project_automations(project_id, viewer=STRANGER)] == [
        "Churn"
    ]
    assert plane.project_contents(project_id, viewer=STRANGER)["project"]["name"] == (
        "Retention"
    )
    assert [p["name"] for p in plane.home_overview(viewer=STRANGER)["projects"]] == [
        "Retention"
    ]


def test_a_public_project_is_writable_by_anyone_signed_in(plane: ControlPlane) -> None:
    """Confirmed in the issue: public means colleagues can work in it, not just
    look at it. So the write gate is visibility, not ownership."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)

    renamed = plane.update_project(
        project_id,
        expected_revision=plane.project(project_id, viewer=STRANGER)["revision"],
        changes={"name": "Churn"},
        viewer=STRANGER,
    )
    plane.create_project_automation(project_id, "Theirs", viewer=STRANGER)

    assert renamed["name"] == "Churn"
    assert [a["name"] for a in plane.project_automations(project_id, viewer=OWNER)] == [
        "Theirs"
    ]


def test_only_the_owner_may_flip_the_switch(plane: ControlPlane) -> None:
    """The one asymmetry in an otherwise shared project."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)

    with pytest.raises(PermissionError, match=OWNER):
        plane.set_project_visibility(project_id, visibility="private", viewer=STRANGER)
    assert plane.project(project_id, viewer=STRANGER)["visibility"] == "public"


def test_going_back_to_private_takes_effect_immediately(plane: ControlPlane) -> None:
    """No grandfathering: the next request from anyone still holding it open
    answers as though the project does not exist."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)
    assert plane.project(project_id, viewer=STRANGER)["name"] == "Retention"

    plane.set_project_visibility(project_id, visibility="private", viewer=OWNER)

    with pytest.raises(KeyError):
        plane.project(project_id, viewer=STRANGER)
    assert plane.list_projects(viewer=STRANGER) == []
    assert plane.home_overview(viewer=STRANGER)["projects"] == []


def test_a_private_source_stays_private_inside_a_public_project(
    plane: ControlPlane,
) -> None:
    """Publishing a project does not override a file its owner held back, which
    is why the Overview tab says so."""
    from ads.api.teams import PRIVATE

    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    source = plane.upload("customers.csv", b"customer_id,churned\n1,0\n", owner=OWNER)
    plane.add_project_source(project_id, source["source_id"], viewer=OWNER)
    plane._ownership().set_visibility(source["source_id"], PRIVATE)
    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)

    assert plane.project_data(project_id, viewer=OWNER)[0]["files"] == ["customers.csv"]
    assert plane.project_data(project_id, viewer=STRANGER) == []


def test_visibility_is_rejected_when_it_has_no_owner_to_check(
    plane: ControlPlane,
) -> None:
    """Same call the data-source route makes: with nobody recorded there is no
    owner check to pass, so the honest answer is a refusal rather than letting
    the first caller claim it."""
    created = plane.create_project("Legacy", owner=None)
    with pytest.raises(ValueError, match="no recorded owner"):
        plane.set_project_visibility(
            created["project_id"], visibility="public", viewer=STRANGER
        )


def test_an_unknown_value_is_refused(plane: ControlPlane) -> None:
    created = plane.create_project("Retention", owner=OWNER)
    with pytest.raises(ValueError, match="visibility must be one of"):
        plane.set_project_visibility(
            created["project_id"], visibility="team", viewer=OWNER
        )


def test_mine_says_who_may_render_the_toggle(plane: ControlPlane) -> None:
    """The UI needs to know whether to draw a control or a read-only mark. It is
    computed per request rather than stored: it is a fact about the reader."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.set_project_visibility(project_id, visibility="public", viewer=OWNER)

    assert plane.project(project_id, viewer=OWNER)["mine"] is True
    assert plane.project(project_id, viewer=STRANGER)["mine"] is False
    assert plane.project_contents(project_id, viewer=OWNER)["project"]["mine"] is True
    theirs = plane.home_overview(viewer=STRANGER)["projects"][0]
    assert theirs["mine"] is False
    assert theirs["visibility"] == "public"


# --------------------------------------------------------------- the route


def test_the_route_publishes_for_the_owner_and_403s_for_everyone_else(
    plane: ControlPlane,
) -> None:
    """403, not 404: a non-owner asking this can already see the project, so
    pretending it does not exist would be a lie they can disprove."""
    who: list[str | None] = [OWNER]
    client = _client(plane, who)
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    published = client.post(
        f"/api/projects/{project_id}/visibility", json={"visibility": "public"}
    )
    assert published.status_code == 200
    assert published.json()["visibility"] == "public"

    who[0] = STRANGER
    assert client.get(f"/api/projects/{project_id}").json()["mine"] is False
    refused = client.post(
        f"/api/projects/{project_id}/visibility", json={"visibility": "private"}
    )
    assert refused.status_code == 403
    assert OWNER in refused.json()["detail"]
    assert client.get(f"/api/projects/{project_id}").json()["visibility"] == "public"


def test_the_route_hides_a_project_the_caller_may_not_see_at_all(
    plane: ControlPlane,
) -> None:
    """A private project owes a stranger a 404 even here -- the 403 above is
    only honest because a public project's id is already known to them."""
    who: list[str | None] = [OWNER]
    client = _client(plane, who)
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    who[0] = STRANGER
    response = client.post(
        f"/api/projects/{project_id}/visibility", json={"visibility": "public"}
    )
    assert response.status_code == 404


def test_a_put_cannot_smuggle_a_publish_past_the_owner_check(
    plane: ControlPlane,
) -> None:
    """`PUT /api/projects/{id}` is the write any collaborator may make, so
    visibility must not be reachable through it."""
    who: list[str | None] = [OWNER]
    client = _client(plane, who)
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    response = client.put(
        f"/api/projects/{project_id}",
        json={"expected_revision": 1, "changes": {"visibility": "public"}},
    )
    assert response.status_code == 400
    assert client.get(f"/api/projects/{project_id}").json()["visibility"] == "private"
