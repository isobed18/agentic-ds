"""Durable saved workflow definitions, distinct from their executions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ads.contracts.base import FrozenModel
from ads.contracts.staging import PipelineBlueprint, PipelineLayout


def _now() -> datetime:
    return datetime.now(UTC)


class AutomationDefinition(FrozenModel):
    """The current saved editor state for one reusable automation.

    Execution ids are references to historical runs. They are deliberately not
    embedded run state: editing this definition never rewrites prior evidence.
    """

    schema_version: Literal["1"] = "1"
    automation_id: str = Field(pattern=r"^automation-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120)
    # `error` distinguishes an automation whose run failed before it ever saved
    # a workspace from one that was never started (#82) -- both were `draft`.
    status: Literal["draft", "saved", "error"] = "draft"
    revision: int = Field(default=1, ge=1)
    source_id: str | None = None
    pipeline_blueprint: PipelineBlueprint | None = None
    pipeline_layout: PipelineLayout = Field(default_factory=PipelineLayout)
    workspace_artifact_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


__all__ = ["AutomationDefinition"]
