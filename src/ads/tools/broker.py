"""Permission enforcement and audit logging for every deterministic tool call."""

from __future__ import annotations

from threading import Lock
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.tools.activity import publish as publish_activity
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
        # #411: every agent's every tool call passes through here, which makes
        # this the one place a live view of tool use can be fed from. The run
        # id comes off the runtime rather than being threaded through the nine
        # places a broker is constructed.
        run_id = runtime.run_id

        if definition.tier is PermissionTier.MUTATE_SOURCE:
            self._record(
                agent.id, tool_id, definition.tier, "denied", "source mutation barred", run_id
            )
            raise ToolPermissionError(
                f"Tool {tool_id!r} requests MUTATE_SOURCE, which is denied for every agent."
            )
        if tool_id not in agent.allowed_tools:
            self._record(agent.id, tool_id, definition.tier, "denied", "outside allowlist", run_id)
            raise ToolPermissionError(
                f"Agent {agent.id!r} is not allowed to call tool {tool_id!r}."
            )
        if definition.tier > agent.max_tool_tier:
            self._record(agent.id, tool_id, definition.tier, "denied", "tier exceeds grant", run_id)
            raise ToolPermissionError(
                f"Tool {tool_id!r} requires {definition.tier.name}; agent {agent.id!r} "
                f"is limited to {agent.max_tool_tier.name}."
            )

        self._record(agent.id, tool_id, definition.tier, "allowed", None, run_id)
        try:
            payload = definition.handler(runtime, arguments)
        except Exception as exc:
            self._record(agent.id, tool_id, definition.tier, "failed", type(exc).__name__, run_id)
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
        run_id: str = "",
    ) -> None:
        # The audit log is the durable half and stays exactly as it was; the
        # feed is the live half, and is allowed to be lossy and to disappear.
        publish_activity(run_id, agent_id, tool_id, decision, detail)
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
