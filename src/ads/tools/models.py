"""Transport-neutral contracts for deterministic agent tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.sandbox import SandboxManager, SandboxSession


class AgentPermissions(Protocol):
    """The small part of an AgentSpec the broker is allowed to inspect."""

    id: str
    allowed_tools: frozenset[str]
    max_tool_tier: PermissionTier


@dataclass(frozen=True)
class ToolPayload:
    """Row-free value produced by a registered deterministic handler."""

    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolResult:
    """A broker-admitted result safe to project into agent context."""

    tool_id: str
    tier: PermissionTier
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolInvocation:
    """One permission decision, including failed executions after admission."""

    agent_id: str
    tool_id: str
    tier: PermissionTier
    decision: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = None

    def audit_tuple(self) -> tuple[str, str, PermissionTier, str, datetime]:
        return self.agent_id, self.tool_id, self.tier, self.decision, self.timestamp


@dataclass
class ToolRuntime:
    """Data-plane resources handlers may use; never rendered into a prompt."""

    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    cards: dict[str, DataCard] = field(default_factory=dict)
    run_id: str = "tool-run"
    sandbox_manager: SandboxManager | None = None
    sandbox_session: SandboxSession | None = None
    artifacts_dir: Path | None = None

    @classmethod
    def from_sources(
        cls,
        cards: list[DataCard],
        frames: dict[str, pd.DataFrame],
        **kwargs: Any,
    ) -> ToolRuntime:
        return cls(
            cards={card.table_name: card for card in cards},
            frames=frames,
            **kwargs,
        )

    def close(self) -> None:
        """Destroy a lazily-created sandbox session, if this runtime owns one."""
        if self.sandbox_manager is not None and self.sandbox_session is not None:
            self.sandbox_manager.destroy(self.sandbox_session)
            self.sandbox_session = None


class ToolError(RuntimeError):
    """Base class for loud tool-boundary failures."""


class ToolNotFoundError(ToolError):
    """Raised when an agent asks for an unregistered tool id."""


class ToolPermissionError(ToolError):
    """Raised when the broker rejects a call."""


class ToolExecutionError(ToolError):
    """Raised when an admitted deterministic handler fails."""


class SandboxUnavailableError(ToolExecutionError):
    """Raised when isolated execution cannot be provided safely."""


__all__ = [
    "AgentPermissions",
    "SandboxUnavailableError",
    "ToolError",
    "ToolExecutionError",
    "ToolInvocation",
    "ToolNotFoundError",
    "ToolPayload",
    "ToolPermissionError",
    "ToolResult",
    "ToolRuntime",
]
