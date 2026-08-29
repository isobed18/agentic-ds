"""Who may see which uploaded data.

Until now every signed-in person saw every uploaded source. With one account
that was not a policy, it was a description. With four it is a decision, and the
decision this makes is: **a team shares its data by default, and a person may
keep something to themselves.**

Deliberately not a permissions system. There are no roles, no grants, no
inheritance, and no per-file rules -- those are the parts of such systems that
get misconfigured, and none of them is the thing being asked for. What is being
asked for is a workspace: colleagues working on the same data without seeing
another team's, and a way to hold something back.

Two rules, in this order:

1. **The owner always sees their own data.** No configuration can hide your own
   upload from you, because a source you cannot see is a source you cannot
   delete.
2. **Everything else is visible to your team**, unless its owner marked it
   private.

Configured through the environment so adding a teammate is not a deploy:

    ADS_TEAMS=core:ishak-ads,gonenc-ads,emre-ads,berkin-ads;ml:ishak-ads,emre-ads

Semicolons separate teams, a colon separates the name from its members, commas
separate members. A person may belong to several teams. **Unset means one
implicit team containing everyone**, which is exactly the behaviour that existed
before this module, so a deployment that never configures teams does not change.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Recorded on a source whose owner wants it to themselves.
PRIVATE = "private"
#: The default. Visible to everyone sharing a team with the owner.
TEAM = "team"

VISIBILITIES = (TEAM, PRIVATE)

_ENV_TEAMS = "ADS_TEAMS"
#: The file name is dotted so `data_sources` skips it, and it lives beside the
#: upload directories rather than inside one, so it is never mistaken for data.
OWNERSHIP_FILE = ".ownership.json"


@dataclass(frozen=True)
class Teams:
    """A resolved team configuration. Immutable; rebuilt when the env changes."""

    #: team name -> members. Empty means "no teams configured".
    members: dict[str, frozenset[str]]

    @property
    def configured(self) -> bool:
        return bool(self.members)

    def teams_of(self, username: str | None) -> frozenset[str]:
        if not username:
            return frozenset()
        return frozenset(
            name for name, people in self.members.items() if username in people
        )

    def share_a_team(self, left: str | None, right: str | None) -> bool:
        """True when two people can see each other's team-visible data.

        With no configuration everyone shares the one implicit team, which keeps
        an unconfigured deployment behaving exactly as it did before.
        """
        if not self.configured:
            return True
        if not left or not right:
            return False
        if left == right:
            return True
        return bool(self.teams_of(left) & self.teams_of(right))


def load_teams(raw: str | None = None) -> Teams:
    """Parse `ADS_TEAMS`. Malformed entries are skipped, never fatal.

    A typo in this variable must not stop the server booting: the failure mode
    of refusing to start is worse than the failure mode of falling back to the
    previous everyone-sees-everything behaviour, which is what an empty result
    produces.
    """
    text = raw if raw is not None else os.environ.get(_ENV_TEAMS, "")
    members: dict[str, frozenset[str]] = {}
    for chunk in (text or "").split(";"):
        name, separator, people = chunk.partition(":")
        if not separator:
            continue
        team = name.strip()
        names = frozenset(p.strip() for p in people.split(",") if p.strip())
        if team and names:
            members[team] = names
    return Teams(members=members)


class OwnershipStore:
    """Who uploaded each source, and who may see it.

    One JSON file rather than a sidecar per upload, so a listing reads one file
    instead of one per source -- the listing is on the critical path of the Data
    library screen.

    An unknown source is not an error. Data uploaded before this existed has no
    owner, and hiding it retroactively would be a worse answer than treating it
    as team-visible, which is what it effectively was.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt registry costs visibility rules, never access to your
            # own data: every record falls back to unowned and team-visible.
            return {}
        return payload if isinstance(payload, dict) else {}

    def record(self, source_id: str, *, owner: str | None, visibility: str = TEAM) -> None:
        if not owner:
            return
        if visibility not in VISIBILITIES:
            visibility = TEAM
        with self._lock:
            data = self._read()
            existing = data.get(source_id)
            if isinstance(existing, dict) and existing.get("owner"):
                # Appending a file to someone else's group must not transfer it.
                return
            data[source_id] = {"owner": owner, "visibility": visibility}
            self._write(data)

    def set_visibility(self, source_id: str, visibility: str) -> None:
        if visibility not in VISIBILITIES:
            raise ValueError(f"visibility must be one of {VISIBILITIES}")
        with self._lock:
            data = self._read()
            record = data.get(source_id)
            if not isinstance(record, dict):
                raise ValueError("source has no recorded owner")
            record["visibility"] = visibility
            data[source_id] = record
            self._write(data)

    def forget(self, source_id: str) -> None:
        """Drop a source's ownership record when its upload group is deleted."""
        with self._lock:
            data = self._read()
            if data.pop(source_id, None) is not None:
                self._write(data)

    def owner_of(self, source_id: str) -> str | None:
        record = self._read().get(source_id)
        return record.get("owner") if isinstance(record, dict) else None

    def visibility_of(self, source_id: str) -> str:
        record = self._read().get(source_id)
        if isinstance(record, dict):
            value = record.get("visibility")
            if value in VISIBILITIES:
                return str(value)
        return TEAM

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            # Losing an ownership record degrades the source to team-visible.
            # Failing the upload over it would be the wrong trade.
            pass


def may_view(
    *,
    viewer: str | None,
    owner: str | None,
    visibility: str,
    teams: Teams,
) -> bool:
    """Apply the two rules, in order."""
    if owner is None:
        # Predates ownership, or its record was lost. It was shared before and
        # stays shared; retroactively hiding data is the worse mistake.
        return True
    if viewer and viewer == owner:
        return True
    if visibility == PRIVATE:
        return False
    return teams.share_a_team(viewer, owner)


__all__ = [
    "OWNERSHIP_FILE",
    "PRIVATE",
    "TEAM",
    "VISIBILITIES",
    "OwnershipStore",
    "Teams",
    "load_teams",
    "may_view",
]
