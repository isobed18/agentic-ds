"""Who sees which project.

Every signed-in account saw every project: no owner was recorded at creation,
`ProjectStore.list()` globbed the whole directory, and not one project route
asked who was calling. The home screen fanned that out into other people's run
history and output labels (#206).

The rule this locks down is stricter than the one `teams.py` chose for uploaded
data: a project belongs to the account that created it, and nobody else can see
it until it is published. Every test that hides something is paired with one
that proves the hiding stops where it should -- a project created before
ownership existed must not vanish, and the owner must never be locked out of
their own work.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts.project import (
    PUBLIC,
    ProjectDefinition,
    may_view_project,
    may_write_project,
)
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


def _project(owner: str | None, *, visibility: str = "private") -> ProjectDefinition:
    return ProjectDefinition(
        project_id="project-0123456789ab",
        name="Retention",
        owner=owner,
        visibility=visibility,
    )


# ------------------------------------------------------------------- rules


def test_the_owner_always_sees_their_own_project() -> None:
    assert may_view_project(_project(OWNER), OWNER)
    assert may_write_project(_project(OWNER), OWNER)


def test_a_private_project_is_invisible_to_every_other_account() -> None:
    assert not may_view_project(_project(OWNER), STRANGER)
    assert not may_view_project(_project(OWNER), None)


def test_a_public_project_is_readable_and_writable_by_anyone_signed_in() -> None:
    """Public means every logged-in account, and a shared project is a
    workspace rather than an exhibit: a visitor can work in it. The one thing
    only the owner may do is publish or unpublish, which is checked at that
    route, not here."""
    published = _project(OWNER, visibility=PUBLIC)
    assert may_view_project(published, STRANGER)
    assert may_write_project(published, STRANGER)


def test_a_project_created_before_ownership_existed_stays_visible() -> None:
    """Retroactively hiding work is the worse mistake -- the same call
    `teams.may_view` makes for an unowned source."""
    assert may_view_project(_project(None), STRANGER)
    assert may_write_project(_project(None), STRANGER)


# --------------------------------------------------------------- the store


def test_the_store_records_the_creator_and_filters_the_listing(tmp_path: Path) -> None:
    from ads.projects import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    mine = store.create("Retention", owner=OWNER)
    theirs = store.create("Segments", owner=STRANGER)

    assert mine.owner == OWNER
    assert mine.visibility == "private"
    assert [p.project_id for p in store.list(viewer=OWNER)] == [mine.project_id]
    assert [p.project_id for p in store.list(viewer=STRANGER)] == [theirs.project_id]
    assert len(store.list(unfiltered=True)) == 2


def test_visibility_cannot_be_smuggled_through_a_general_update(tmp_path: Path) -> None:
    """`update` is the write any collaborator may make. Publishing is not."""
    from ads.projects import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    project = store.create("Retention", owner=OWNER)
    with pytest.raises(ValueError, match="unsupported project fields: visibility"):
        store.update(
            project.project_id,
            expected_revision=project.revision,
            changes={"visibility": PUBLIC},
        )


# --------------------------------------------------------- the control plane


def test_another_account_cannot_read_a_project_or_anything_under_it(
    plane: ControlPlane,
) -> None:
    """Not listed, not openable by id, and every project-scoped read answers as
    though it does not exist."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    plane.create_project_automation(project_id, "Churn", viewer=OWNER)

    assert [p["project_id"] for p in plane.list_projects(viewer=OWNER)] == [project_id]
    assert plane.list_projects(viewer=STRANGER) == []
    reads = (
        plane.project,
        plane.project_data,
        plane.project_automations,
        plane.project_contents,
    )
    for read in reads:
        with pytest.raises(KeyError):
            read(project_id, viewer=STRANGER)


def test_another_account_cannot_write_a_project_it_cannot_see(
    plane: ControlPlane,
) -> None:
    """Read hidden and write open would make the isolation cosmetic."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    source = plane.upload("customers.csv", b"customer_id,churned\n1,0\n", owner=OWNER)

    with pytest.raises(KeyError):
        plane.update_project(
            project_id,
            expected_revision=created["revision"],
            changes={"name": "Hijacked"},
            viewer=STRANGER,
        )
    with pytest.raises(KeyError):
        plane.add_project_source(project_id, source["source_id"], viewer=STRANGER)
    with pytest.raises(KeyError):
        plane.create_project_automation(project_id, "Theirs", viewer=STRANGER)
    assert plane.project(project_id, viewer=OWNER)["name"] == "Retention"


def test_the_owner_can_still_do_all_of_that(plane: ControlPlane) -> None:
    """The paired half: the check must not lock the owner out of their own work."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    source = plane.upload("customers.csv", b"customer_id,churned\n1,0\n", owner=OWNER)

    renamed = plane.update_project(
        project_id,
        expected_revision=created["revision"],
        changes={"name": "Churn"},
        viewer=OWNER,
    )
    attached = plane.add_project_source(project_id, source["source_id"], viewer=OWNER)
    automation = plane.create_project_automation(project_id, "Churn model", viewer=OWNER)

    assert renamed["name"] == "Churn"
    assert attached["source_ids"] == [source["source_id"]]
    assert [a["name"] for a in plane.project_automations(project_id, viewer=OWNER)] == [
        "Churn model"
    ]
    assert automation["automation_id"] in plane.project(project_id, viewer=OWNER)[
        "automation_ids"
    ]


def test_the_home_screen_carries_only_the_viewers_projects(plane: ControlPlane) -> None:
    """The leak was never just a name in a list: home assembles every project's
    automations, runs, models and reports for whoever asks."""
    plane.create_project("Retention", owner=OWNER)
    plane.create_project("Segments", owner=STRANGER)

    mine = plane.home_overview(viewer=OWNER)
    assert [p["name"] for p in mine["projects"]] == ["Retention"]
    assert mine["totals"]["projects"] == 1
    assert [p["name"] for p in plane.home_overview(viewer=STRANGER)["projects"]] == [
        "Segments"
    ]


def test_an_automations_contents_hide_with_its_project(plane: ControlPlane) -> None:
    """`automation_contents` embeds the parent project record, so it has to
    honour the same rule or the project leaks through its child."""
    created = plane.create_project("Retention", owner=OWNER)
    automation = plane.create_project_automation(
        created["project_id"], "Churn", viewer=OWNER
    )
    automation_id = automation["automation_id"]

    assert plane.automation_contents(automation_id, viewer=OWNER)["project"]["name"] == (
        "Retention"
    )
    with pytest.raises(KeyError):
        plane.automation_contents(automation_id, viewer=STRANGER)


def test_deleting_an_automation_still_detaches_it_from_a_hidden_parent(
    plane: ControlPlane,
) -> None:
    """Reconciliation runs with no request behind it and must see every project,
    or #185's orphaned-id bug comes back the moment ownership is recorded."""
    created = plane.create_project("Retention", owner=OWNER)
    project_id = created["project_id"]
    automation = plane.create_project_automation(project_id, "Draft", viewer=OWNER)

    plane.delete_automation(automation["automation_id"])

    assert plane.project(project_id, viewer=OWNER)["automation_ids"] == []


# --------------------------------------------------------------- the routes


def _client(plane: ControlPlane, username: list[str | None]) -> TestClient:
    """A signed-in client. Stands in for the auth middleware, which is what
    normally puts the username on the request."""
    app = create_app(plane=plane)

    @app.middleware("http")
    async def _sign_in(request, call_next):  # type: ignore[no-untyped-def]
        request.state.username = username[0]
        return await call_next(request)

    return TestClient(app)


def test_the_routes_pass_the_viewer_through(plane: ControlPlane) -> None:
    """The routes took no `Request` at all, so any session with a project id
    could read or modify another account's project."""
    who: list[str | None] = [OWNER]
    client = _client(plane, who)
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    assert client.get("/api/projects").json()[0]["owner"] == OWNER
    who[0] = STRANGER
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get(f"/api/projects/{project_id}/data").status_code == 404
    assert client.get(f"/api/projects/{project_id}/automations").status_code == 404
    assert client.get(f"/api/projects/{project_id}/contents").status_code == 404
    assert client.get("/api/home").json()["projects"] == []
    assert (
        client.post(
            f"/api/projects/{project_id}/automations", json={"name": "Theirs"}
        ).status_code
        == 404
    )
    who[0] = OWNER
    assert client.get(f"/api/projects/{project_id}").status_code == 200


def test_private_source_catalog_profile_and_blueprint_hide_from_other_accounts(
    plane: ControlPlane,
) -> None:
    """#331: project routes were isolated, but the global dataset catalog and
    direct source endpoints still exposed another owner's private schema."""
    uploaded = plane.upload(
        "private-customers.csv",
        b"customer_id,secret_segment\n1,private\n",
        owner=OWNER,
    )
    source_id = uploaded["source_id"]
    plane._ownership().set_visibility(source_id, "private")  # noqa: SLF001
    who: list[str | None] = [STRANGER]
    client = _client(plane, who)

    hidden_catalog = client.get("/api/catalog/datasets")
    hidden_profile = client.get(f"/api/data-sources/{source_id}/profile")
    hidden_blueprint = client.get(f"/api/data-sources/{source_id}/pipeline-blueprint")

    assert hidden_catalog.status_code == 200
    assert hidden_catalog.json()["items"] == []
    assert hidden_profile.status_code == 404
    assert hidden_blueprint.status_code == 404
    assert "secret_segment" not in hidden_profile.text

    who[0] = OWNER
    assert [item["source_id"] for item in client.get("/api/catalog/datasets").json()["items"]] == [
        source_id
    ]
    assert client.get(f"/api/data-sources/{source_id}/profile").status_code == 200
    assert client.get(f"/api/data-sources/{source_id}/pipeline-blueprint").status_code == 200


def test_a_hidden_project_answers_404_rather_than_403(plane: ControlPlane) -> None:
    """A 403 confirms the id exists, which is the one thing a project nobody
    may see must not reveal."""
    who: list[str | None] = [OWNER]
    client = _client(plane, who)
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    who[0] = STRANGER
    response = client.put(
        f"/api/projects/{project_id}",
        json={"expected_revision": 1, "changes": {"name": "Hijacked"}},
    )
    assert response.status_code == 404
    assert "Hijacked" not in response.text


def test_an_unauthenticated_deployment_behaves_exactly_as_before(
    plane: ControlPlane,
) -> None:
    """No auth means no username on the request, so nothing gets an owner and
    nothing is hidden -- the loopback launcher and every existing test."""
    client = TestClient(create_app(plane=plane))
    project_id = client.post("/api/projects", json={"name": "Retention"}).json()[
        "project_id"
    ]

    assert client.get("/api/projects").json()[0]["owner"] is None
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert len(client.get("/api/home").json()["projects"]) == 1
