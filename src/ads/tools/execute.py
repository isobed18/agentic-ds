"""Isolated Python execution tool; there is deliberately no host fallback."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.sandbox import DataFrameOutput, FigureOutput
from ads.tools.models import SandboxUnavailableError, ToolPayload, ToolRuntime
from ads.tools.registry import ToolDefinition, ToolRegistry


def _artifact_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


#: stdout is a text channel the executing code controls completely, so it is
#: bounded rather than trusted. The cap is what stops an unbounded print from
#: flooding a prompt; it is not what stops row disclosure -- see below.
STDOUT_CHAR_LIMIT = 4000


def execute_python(runtime: ToolRuntime, arguments: Mapping[str, Any]) -> ToolPayload:
    """Run code in the persistent locked-down Jupyter container.

    Structured DataFrame and inline figure *displays* are suppressed, so the
    implicit repr of a frame does not reach the caller and neither do base64
    image bodies.

    This is not the same as a guarantee that source rows cannot come back.
    ``/data`` is mounted into the container read-only, and code that runs there
    can write anything it likes to stdout -- ``print(df.head().to_string())``
    returns real rows past the display suppression. stdout is therefore capped
    below, but a cap only bounds the volume.

    Row access is permitted for this local exploratory agent. The load-bearing
    boundary is mutation: source data is mounted read-only, execution has no
    host fallback, and only derived artifacts may be written.
    """
    backend = runtime.backend()
    if backend is None or not runtime.execution_available():
        raise SandboxUnavailableError(
            "Isolated Python execution is unavailable; refusing to execute on the host."
        )

    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("execute_python requires non-empty code.")
    timeout = float(arguments.get("timeout", 30.0))
    if timeout <= 0 or timeout > 120:
        raise ValueError("execute_python timeout must be in (0, 120] seconds.")

    artifacts_dir = runtime.execution_artifacts_dir()
    before = _artifact_snapshot(artifacts_dir)
    session = runtime.session()
    if session is None:
        session = backend.create_session(runtime.run_id)
        runtime.set_session(session)

    result = backend.execute(session, code, timeout=timeout)
    after = _artifact_snapshot(artifacts_dir)
    changed = sorted(name for name, fingerprint in after.items() if before.get(name) != fingerprint)
    artifact_refs = tuple(f"artifact://{name}" for name in changed)

    errors = [
        {
            "name": error.name,
            "value": error.value,
            "traceback": list(error.traceback),
        }
        for error in result.errors
    ]
    suppressed_frames = sum(isinstance(output, DataFrameOutput) for output in result.outputs)
    suppressed_figures = sum(isinstance(output, FigureOutput) for output in result.outputs)

    raw_stdout = result.stdout
    truncated = len(raw_stdout) > STDOUT_CHAR_LIMIT
    stdout = (
        raw_stdout[:STDOUT_CHAR_LIMIT]
        + f"\n… truncated at {STDOUT_CHAR_LIMIT} characters "
        f"({len(raw_stdout) - STDOUT_CHAR_LIMIT} more). "
        "Write large output to /artifacts and reference the file instead."
        if truncated
        else raw_stdout
    )

    data = {
        "execution_count": result.execution_count,
        "stdout": stdout,
        "stdout_truncated": truncated,
        "stdout_chars": len(raw_stdout),
        "errors": errors,
        "timed_out": result.timed_out,
        "artifact_refs": list(artifact_refs),
        "suppressed_dataframe_outputs": suppressed_frames,
        "suppressed_inline_figures": suppressed_figures,
    }
    status = "timed out" if result.timed_out else "completed"
    summary = (
        f"execute_python {status}; stdout_chars={len(raw_stdout)}"
        f"{' (truncated)' if truncated else ''}, "
        f"errors={len(errors)}, artifacts={len(artifact_refs)}, "
        f"suppressed_dataframes={suppressed_frames}, suppressed_figures={suppressed_figures}"
    )
    return ToolPayload(
        summary=summary,
        data=data,
        artifact_refs=artifact_refs,
    )


def register_execute_tool(registry: ToolRegistry) -> ToolRegistry:
    registry.register(
        ToolDefinition(
            tool_id="execute_python",
            arguments={
                'code': 'Python source to run in the sandbox',
                'timeout': 'optional seconds, 0-120, default 30',
            },
            tier=PermissionTier.EXECUTE,
            description="Execute Python in the isolated persistent Jupyter sandbox.",
            handler=execute_python,
        )
    )
    return registry


__all__ = ["execute_python", "register_execute_tool"]
