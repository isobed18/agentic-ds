"""Who sees which uploaded source.

With one account, "everyone sees everything" was a description rather than a
policy. With four it is a decision, and the decision is: a team shares its data
by default, and a person may hold something back.

The rules that matter most here are the ones about *not* hiding things. A
source you cannot see is a source you cannot delete, and data that was shared
before this existed must not vanish because a registry file is missing. Every
test that hides something is paired with one that proves the hiding stops where
it should.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ads.api.service import ControlPlane
from ads.api.teams import (
    OWNERSHIP_FILE,
    PRIVATE,
    TEAM,
    OwnershipStore,
    load_teams,
    may_view,
)
from ads.store.artifacts import ArtifactStore

CORE = "core:ishak-ads,gonenc-ads,emre-ads,berkin-ads"
SPLIT = "core:ishak-ads,gonenc-ads;ml:emre-ads,berkin-ads"


# ------------------------------------------------------------ configuration


def test_teams_parse_from_one_string() -> None:
    teams = load_teams(SPLIT)
    assert teams.teams_of("ishak-ads") == {"core"}
    assert teams.teams_of("emre-ads") == {"ml"}
    assert teams.teams_of("nobody") == frozenset()


def test_a_person_can_belong_to_several_teams() -> None:
    teams = load_teams("core:ishak-ads,gonenc-ads;ml:ishak-ads,emre-ads")
    assert teams.teams_of("ishak-ads") == {"core", "ml"}
    assert teams.share_a_team("gonenc-ads", "emre-ads") is False, "only ishak bridges them"
    assert teams.share_a_team("ishak-ads", "emre-ads") is True


@pytest.mark.parametrize("raw", ["", "nonsense", "no-colon-here", "  ", ":,;", "team:"])
def test_a_malformed_configuration_falls_back_instead_of_failing(raw: str) -> None:
    """A typo in an environment variable must not stop the server booting.

    Refusing to start is a worse failure than falling back to the behaviour
    that existed before teams, which is what an empty result produces.
    """
    teams = load_teams(raw)
    assert teams.configured is False
    assert teams.share_a_team("anyone", "anyone-else") is True


def test_unconfigured_means_everyone_shares_one_team() -> None:
    """A deployment that never sets ADS_TEAMS must behave exactly as before."""
    assert load_teams("").share_a_team("a", "b") is True


# ------------------------------------------------------------------- rules


def test_the_owner_always_sees_their_own_data() -> None:
    """No configuration may hide your own upload from you -- a source you
    cannot see is a source you cannot delete."""
    teams = load_teams(SPLIT)
    assert may_view(viewer="emre-ads", owner="emre-ads", visibility=PRIVATE, teams=teams)


def test_a_teammate_sees_team_visible_data() -> None:
    teams = load_teams(CORE)
    assert may_view(viewer="gonenc-ads", owner="ishak-ads", visibility=TEAM, teams=teams)


def test_another_team_does_not() -> None:
    teams = load_teams(SPLIT)
    assert not may_view(viewer="emre-ads", owner="ishak-ads", visibility=TEAM, teams=teams)


def test_private_hides_from_teammates_too() -> None:
    teams = load_teams(CORE)
    assert not may_view(viewer="gonenc-ads", owner="ishak-ads", visibility=PRIVATE, teams=teams)


def test_data_that_predates_ownership_stays_visible() -> None:
    """Retroactively hiding data someone uploaded before this existed is the
    worse mistake. Unowned means shared, which is what it already was."""
    teams = load_teams(SPLIT)
    assert may_view(viewer="emre-ads", owner=None, visibility=TEAM, teams=teams)


# ----------------------------------------------------------------- storage


def test_appending_a_file_does_not_transfer_the_source(tmp_path: Path) -> None:
    """Groups are built one file at a time and a colleague may add to yours.
    Whoever added the latest file is not the owner."""
    store = OwnershipStore(tmp_path / OWNERSHIP_FILE)
    store.record("upload:abc", owner="ishak-ads")
    store.record("upload:abc", owner="emre-ads")
    assert store.owner_of("upload:abc") == "ishak-ads"


def test_a_corrupt_registry_costs_rules_not_access(tmp_path: Path) -> None:
    """The failure has to degrade toward visible. A registry that cannot be read
    must not lock people out of data they own."""
    path = tmp_path / OWNERSHIP_FILE
    store = OwnershipStore(path)
    store.record("upload:abc", owner="ishak-ads")
    path.write_text("{not json", encoding="utf-8")

    assert store.owner_of("upload:abc") is None
    assert store.visibility_of("upload:abc") == TEAM
    assert may_view(viewer="anyone", owner=None, visibility=TEAM, teams=load_teams(SPLIT))


def test_visibility_round_trips(tmp_path: Path) -> None:
    store = OwnershipStore(tmp_path / OWNERSHIP_FILE)
    store.record("upload:abc", owner="ishak-ads")
    assert store.visibility_of("upload:abc") == TEAM
    store.set_visibility("upload:abc", PRIVATE)
    assert store.visibility_of("upload:abc") == PRIVATE


def test_an_unknown_visibility_is_rejected(tmp_path: Path) -> None:
    store = OwnershipStore(tmp_path / OWNERSHIP_FILE)
    store.record("upload:abc", owner="ishak-ads")
    with pytest.raises(ValueError):
        store.set_visibility("upload:abc", "public-to-the-internet")


# ------------------------------------------------------------- the listing


@pytest.fixture
def plane(tmp_path: Path) -> ControlPlane:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "data",),
        upload_root=uploads,
    )


def _upload(plane: ControlPlane, owner: str, name: str = "a.csv") -> str:
    frame = pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode("utf-8")
    return plane.upload(name, frame, owner=owner)["source_id"]


def test_a_stray_file_no_longer_breaks_the_whole_listing(plane: ControlPlane) -> None:
    """`files` was computed from `child.iterdir()` before the `is_dir()` check,
    so a .DS_Store, a Thumbs.db or any stray download in the upload root raised
    NotADirectoryError and took the entire Data library down with it."""
    _upload(plane, "ishak-ads")
    (plane.upload_root / "Thumbs.db").write_text("junk", encoding="utf-8")

    listed = plane.data_sources(viewer="ishak-ads")
    assert [s["source_id"] for s in listed if s["source_id"].startswith("upload:")]


def test_the_registry_itself_is_not_listed_as_a_source(plane: ControlPlane) -> None:
    _upload(plane, "ishak-ads")
    assert not any(
        OWNERSHIP_FILE in s["source_id"] for s in plane.data_sources(viewer="ishak-ads")
    )


def test_single_file_upload_is_labelled_with_its_file_name(plane: ControlPlane) -> None:
    """A one-file source used to read "upload (1 file)" everywhere, so several
    singletons side by side were indistinguishable without opening each (#71).
    Its own file name is the one fact that tells them apart."""
    frame = pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode("utf-8")
    result = plane.upload("customers.csv", frame, owner="ishak-ads")
    assert result["label"] == "customers.csv"

    listed = plane.data_sources(viewer="ishak-ads")
    labels = [s["label"] for s in listed if s["source_id"].startswith("upload:")]
    assert "customers.csv" in labels


def test_multi_file_upload_keeps_the_generic_count_label(plane: ControlPlane) -> None:
    """Two or more files have no single name to show, so they keep the count."""
    frame = pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode("utf-8")
    first = plane.upload("a.csv", frame, owner="ishak-ads")
    result = plane.upload("b.csv", frame, owner="ishak-ads", source_id=first["source_id"])
    assert result["label"] == "upload (2 files)"


def test_removing_a_file_keeps_the_rest_of_the_source(plane: ControlPlane) -> None:
    """There was no way to take a file back out of a source (#85)."""
    frame = pd.DataFrame({"id": [1, 2]}).to_csv(index=False).encode("utf-8")
    first = plane.upload("a.csv", frame, owner="ishak-ads")
    plane.upload("b.csv", frame, owner="ishak-ads", source_id=first["source_id"])

    result = plane.remove_upload_file(first["source_id"], "a.csv", owner="ishak-ads")

    assert result["deleted"] is False
    assert result["files"] == ["b.csv"]
    listed = plane.data_sources(viewer="ishak-ads")
    files = next(s["files"] for s in listed if s["source_id"] == first["source_id"])
    assert files == ["b.csv"]


def test_removing_the_last_file_deletes_the_group_and_its_ownership(
    plane: ControlPlane,
) -> None:
    source_id = _upload(plane, "ishak-ads", name="only.csv")

    result = plane.remove_upload_file(source_id, "only.csv", owner="ishak-ads")

    assert result["deleted"] is True
    assert not any(
        s["source_id"] == source_id for s in plane.data_sources(viewer="ishak-ads")
    )
    assert plane._ownership().owner_of(source_id) is None


def test_only_the_owner_may_remove_a_file(plane: ControlPlane) -> None:
    source_id = _upload(plane, "ishak-ads")

    with pytest.raises(PermissionError):
        plane.remove_upload_file(source_id, "a.csv", owner="someone-else")

    # The file is still there for its owner.
    assert plane.remove_upload_file(source_id, "a.csv", owner="ishak-ads")["deleted"] is True


def test_teammates_see_each_others_uploads(
    plane: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADS_TEAMS", CORE)
    mine = _upload(plane, "ishak-ads")
    assert mine in {s["source_id"] for s in plane.data_sources(viewer="gonenc-ads")}


def test_another_team_does_not_see_it(
    plane: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADS_TEAMS", SPLIT)
    mine = _upload(plane, "ishak-ads")
    assert mine not in {s["source_id"] for s in plane.data_sources(viewer="emre-ads")}
    assert mine in {s["source_id"] for s in plane.data_sources(viewer="gonenc-ads")}


def test_a_private_source_disappears_for_everyone_but_its_owner(
    plane: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADS_TEAMS", CORE)
    mine = _upload(plane, "ishak-ads")
    plane._ownership().set_visibility(mine, PRIVATE)

    assert mine not in {s["source_id"] for s in plane.data_sources(viewer="gonenc-ads")}
    assert mine in {s["source_id"] for s in plane.data_sources(viewer="ishak-ads")}


def test_an_unfiltered_listing_still_works_for_internal_callers(
    plane: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callers with no request behind them pass no viewer and must see
    everything, or background work starts silently skipping sources."""
    monkeypatch.setenv("ADS_TEAMS", SPLIT)
    mine = _upload(plane, "ishak-ads")
    assert mine in {s["source_id"] for s in plane.data_sources()}


def test_the_listing_says_who_owns_what(
    plane: ControlPlane, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADS_TEAMS", CORE)
    mine = _upload(plane, "ishak-ads")
    entry = next(s for s in plane.data_sources(viewer="gonenc-ads") if s["source_id"] == mine)
    assert entry["owner"] == "ishak-ads"
    assert entry["mine"] is False
    assert entry["visibility"] == TEAM

    own = next(s for s in plane.data_sources(viewer="ishak-ads") if s["source_id"] == mine)
    assert own["mine"] is True


def test_ownership_survives_a_restart(plane: ControlPlane, tmp_path: Path) -> None:
    """It is a file, not process state -- a restart must not orphan every
    source and make the whole library unowned."""
    mine = _upload(plane, "ishak-ads")
    reopened = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "data",),
        upload_root=tmp_path / "uploads",
    )
    assert reopened._ownership().owner_of(mine) == "ishak-ads"
    recorded = json.loads((tmp_path / "uploads" / OWNERSHIP_FILE).read_text(encoding="utf-8"))
    assert recorded[mine]["owner"] == "ishak-ads"
