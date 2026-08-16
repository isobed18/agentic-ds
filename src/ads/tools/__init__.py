"""Permissioned deterministic tools available to typed agents."""

from ads.tools.broker import PermissionBroker
from ads.tools.ds import register_ds_tools
from ads.tools.execute import register_execute_tool
from ads.tools.models import (
    SandboxUnavailableError,
    ToolError,
    ToolExecutionError,
    ToolInvocation,
    ToolNotFoundError,
    ToolPayload,
    ToolPermissionError,
    ToolResult,
    ToolRuntime,
)
from ads.tools.registry import ToolDefinition, ToolRegistry


def build_tool_registry() -> ToolRegistry:
    """Build the closed MVP registry; callers may not discover arbitrary callables."""
    registry = ToolRegistry()
    register_ds_tools(registry)
    register_execute_tool(registry)
    return registry


__all__ = [
    "PermissionBroker",
    "SandboxUnavailableError",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionError",
    "ToolInvocation",
    "ToolNotFoundError",
    "ToolPayload",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolResult",
    "ToolRuntime",
    "build_tool_registry",
    "register_ds_tools",
    "register_execute_tool",
]
