"""Closed registry of deterministic tools exposed to agents."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.tools.models import ToolNotFoundError, ToolPayload, ToolRuntime

ToolHandler = Callable[[ToolRuntime, Mapping[str, Any]], ToolPayload]


@dataclass(frozen=True)
class ToolDefinition:
    """A callable measurement, described well enough for an agent to call it.

    ``arguments`` exists because its absence was expensive. A tool advertised
    only a one-line prose description, so an agent had to guess the argument
    names and shapes from it. Measured on a real run: the validation
    investigator called ``trial_validation_strategy`` eight times in a row and
    every single call raised, then the turn budget ran out having produced no
    evidence at all — 476 seconds spent on a tool it could not learn to call.
    Other agents invented tool names outright (``python_interpreter``,
    ``missingness``) that no registry entry ever had.
    """

    tool_id: str
    tier: PermissionTier
    description: str
    handler: ToolHandler
    #: ``name -> what to pass``. Rendered into the agent's prompt verbatim.
    arguments: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_id or not self.tool_id.replace("_", "").isalnum():
            raise ValueError("tool_id must contain only letters, digits, and underscores")

    def signature(self) -> str:
        """One line an agent can copy: the id, its arguments, and what it does."""
        if not self.arguments:
            return f"{self.tool_id}() — {self.description}"
        args = ", ".join(self.arguments)
        detail = "; ".join(f"{name}: {what}" for name, what in self.arguments.items())
        return f"{self.tool_id}({args}) — {self.description} Arguments: {detail}"


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
