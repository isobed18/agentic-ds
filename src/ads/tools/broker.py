"""Permission enforcement and audit logging for every deterministic tool call."""

from __future__ import annotations

from threading import Lock
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.tools.models import (
    AgentPermissions,
    ToolExecutionError,
    ToolInvocation,
    ToolPermissionError,
    ToolResult,
    ToolRuntime,
)
from ads.tools.registry import ToolRegistry


class PermissionBroker:
    """Admit a call only when both its id and permission tier are authorized."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self._audit_log: list[ToolInvocation] = []
        self._lock = Lock()

    @property
    def audit_log(self) -> tuple[ToolInvocation, ...]:
        with self._lock:
            return tuple(self._audit_log)

    def invoke(
        self,
        agent: AgentPermissions,
        tool_id: str,
        runtime: ToolRuntime,
        **arguments: Any,
    ) -> ToolResult:
        definition = self.registry.resolve(tool_id)

        if definition.tier is PermissionTier.MUTATE_SOURCE:
            self._record(agent.id, tool_id, definition.tier, "denied", "source mutation barred")
            raise ToolPermissionError(
                f"Tool {tool_id!r} requests MUTATE_SOURCE, which is denied for every agent."
            )
        if tool_id not in agent.allowed_tools:
            self._record(agent.id, tool_id, definition.tier, "denied", "outside allowlist")
            raise ToolPermissionError(
                f"Agent {agent.id!r} is not allowed to call tool {tool_id!r}."
            )
        if definition.tier > agent.max_tool_tier:
            self._record(agent.id, tool_id, definition.tier, "denied", "tier exceeds grant")
            raise ToolPermissionError(
                f"Tool {tool_id!r} requires {definition.tier.name}; agent {agent.id!r} "
                f"is limited to {agent.max_tool_tier.name}."
            )

        self._record(agent.id, tool_id, definition.tier, "allowed")
        try:
            payload = definition.handler(runtime, arguments)
        except Exception as exc:
            self._record(agent.id, tool_id, definition.tier, "failed", type(exc).__name__)
            if isinstance(exc, ToolExecutionError):
                raise
            raise ToolExecutionError(
                f"Tool {tool_id!r} failed deterministically: {type(exc).__name__}: {exc}"
            ) from exc
        return ToolResult(
            tool_id=tool_id,
            tier=definition.tier,
            summary=payload.summary,
            data=payload.data,
            artifact_refs=payload.artifact_refs,
        )

    def _record(
        self,
        agent_id: str,
        tool_id: str,
        tier: PermissionTier,
        decision: str,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            self._audit_log.append(
                ToolInvocation(
                    agent_id=agent_id,
                    tool_id=tool_id,
                    tier=tier,
                    decision=decision,
                    detail=detail,
                )
            )


__all__ = ["PermissionBroker"]
