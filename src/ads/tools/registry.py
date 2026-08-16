"""Closed registry of deterministic tools exposed to agents."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.tools.models import ToolNotFoundError, ToolPayload, ToolRuntime

ToolHandler = Callable[[ToolRuntime, Mapping[str, Any]], ToolPayload]


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    tier: PermissionTier
    description: str
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not self.tool_id or not self.tool_id.replace("_", "").isalnum():
            raise ValueError("tool_id must contain only letters, digits, and underscores")


class ToolRegistry:
    """Explicit registry; handlers cannot be discovered through arbitrary imports."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.tool_id in self._tools:
            raise ValueError(f"Tool {definition.tool_id!r} is already registered.")
        self._tools[definition.tool_id] = definition

    def resolve(self, tool_id: str) -> ToolDefinition:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown deterministic tool {tool_id!r}.") from exc

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._tools.values())

    def ids(self) -> frozenset[str]:
        return frozenset(self._tools)


__all__ = ["ToolDefinition", "ToolHandler", "ToolRegistry"]
