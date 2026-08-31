"""Durable project containers above reusable automation definitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ads.contracts.base import FrozenModel

#: Only the account that created the project may see it. The default, because
#: a project nobody chose to share is nobody else's business (#206).
PRIVATE = "private"
#: Visible to every signed-in account. Deliberately *not* the `team` value used
#: for uploaded data (`ads.api.teams`): that means "people who share a team with
#: the owner", which is a narrower thing wearing a similar name.
PUBLIC = "public"

PROJECT_VISIBILITIES = (PRIVATE, PUBLIC)


def _now() -> datetime:
    return datetime.now(UTC)


class ProjectDefinition(FrozenModel):
    """One named container whose data pool remains open over its lifetime."""

    schema_version: Literal["1"] = "1"
    project_id: str = Field(pattern=r"^project-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=1, ge=1)
    #: The account that created it. `None` means "created before ownership
    #: existed": those stay visible to everyone rather than being orphaned, the
    #: same call `ads.api.teams.may_view` makes for unowned sources.
    owner: str | None = None
    visibility: Literal["private", "public"] = PRIVATE
    source_ids: tuple[str, ...] = ()
    automation_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


def may_view_project(project: ProjectDefinition, viewer: str | None) -> bool:
    """Who may read a project.

    Three rules, in order, mirroring the shape `teams.may_view` established for
    uploaded data -- but with the stricter default #206 asked for: a project is
    owner-only until someone publishes it.
    """
    if project.owner is None:
        return True
    if viewer is not None and viewer == project.owner:
        return True
    return project.visibility == PUBLIC


def may_write_project(project: ProjectDefinition, viewer: str | None) -> bool:
    """Who may change a project's name, sources and automations.

    A public project is a shared workspace, not a read-only exhibit: anyone
    signed in can work in it. Only the owner may change the visibility itself,
    which is checked at that route rather than here.
    """
    if not may_view_project(project, viewer):
        return False
    if project.owner is None or project.visibility == PUBLIC:
        return True
    return viewer is not None and viewer == project.owner


__all__ = [
    "PRIVATE",
    "PROJECT_VISIBILITIES",
    "PUBLIC",
    "ProjectDefinition",
    "may_view_project",
    "may_write_project",
]
