"""Permissioned deterministic tools available to typed agents."""

from ads.tools.broker import PermissionBroker
from ads.tools.ds import register_ds_tools
from ads.tools.execute import register_execute_tool
from ads.tools.integration import register_integration_tools
from ads.tools.leakage import register_leakage_tools
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
from ads.tools.validation import register_validation_tools


def build_tool_registry() -> ToolRegistry:
    """Build the closed MVP registry; callers may not discover arbitrary callables."""
    registry = ToolRegistry()
    register_ds_tools(registry)
    register_integration_tools(registry)
    register_leakage_tools(registry)
    register_execute_tool(registry)
    register_validation_tools(registry)
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
    "register_integration_tools",
    "register_leakage_tools",
    "register_validation_tools",
]
