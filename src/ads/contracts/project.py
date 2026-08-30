"""Durable project containers above reusable automation definitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ads.contracts.base import FrozenModel


def _now() -> datetime:
    return datetime.now(UTC)


class ProjectDefinition(FrozenModel):
    """One named container whose data pool remains open over its lifetime."""

    schema_version: Literal["1"] = "1"
    project_id: str = Field(pattern=r"^project-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=1, ge=1)
    source_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


__all__ = ["ProjectDefinition"]
