"""Runtime-neutral execution boundary for containers today and VMs later."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ads.sandbox.models import ExecutionResult


@runtime_checkable
class ExecutionBackend(Protocol):
    """Backend interface consumed by agent tools, independent of isolation technology."""

    @property
    def data_dir(self) -> Path: ...

    @property
    def artifacts_dir(self) -> Path: ...

    def available(self) -> bool: ...

    def create_session(self, run_id: str) -> Any: ...

    def execute(self, session: Any, code: str, timeout: float = 30.0) -> ExecutionResult: ...

    def destroy(self, session: Any) -> None: ...


__all__ = ["ExecutionBackend"]
