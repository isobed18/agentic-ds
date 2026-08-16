"""Transport-neutral values returned by the sandbox boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class SandboxSession:
    run_id: str
    container_name: str
    connection_file: str


@dataclass(frozen=True)
class TextOutput:
    text: str
    stream: Literal["stdout", "stderr", "display"] = "display"
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class ErrorOutput:
    name: str
    value: str
    traceback: tuple[str, ...]
    kind: Literal["error"] = "error"


@dataclass(frozen=True)
class FigureOutput:
    png_base64: str
    kind: Literal["figure"] = "figure"


@dataclass(frozen=True)
class DataFrameOutput:
    columns: tuple[str, ...]
    index: tuple[Any, ...]
    data: tuple[tuple[Any, ...], ...]
    kind: Literal["dataframe"] = "dataframe"


SandboxOutput = TextOutput | ErrorOutput | FigureOutput | DataFrameOutput


@dataclass(frozen=True)
class ExecutionResult:
    execution_count: int | None
    outputs: tuple[SandboxOutput, ...] = field(default_factory=tuple)
    timed_out: bool = False

    @property
    def stdout(self) -> str:
        return "".join(
            output.text
            for output in self.outputs
            if isinstance(output, TextOutput) and output.stream == "stdout"
        )

    @property
    def errors(self) -> tuple[ErrorOutput, ...]:
        return tuple(output for output in self.outputs if isinstance(output, ErrorOutput))


__all__ = [
    "DataFrameOutput",
    "ErrorOutput",
    "ExecutionResult",
    "FigureOutput",
    "SandboxOutput",
    "SandboxSession",
    "TextOutput",
]
