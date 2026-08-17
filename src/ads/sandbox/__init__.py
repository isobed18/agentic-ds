"""Isolated, persistent execution sessions for code-writing agents."""

from ads.sandbox.backend import ExecutionBackend
from ads.sandbox.manager import SandboxConfig, SandboxManager
from ads.sandbox.materialize import materialize_frame_copies
from ads.sandbox.models import (
    DataFrameOutput,
    ErrorOutput,
    ExecutionResult,
    FigureOutput,
    SandboxSession,
    TextOutput,
)

__all__ = [
    "DataFrameOutput",
    "ErrorOutput",
    "ExecutionBackend",
    "materialize_frame_copies",
    "ExecutionResult",
    "FigureOutput",
    "SandboxConfig",
    "SandboxManager",
    "SandboxSession",
    "TextOutput",
]
