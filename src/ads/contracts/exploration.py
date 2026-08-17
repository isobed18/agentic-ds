"""Typed contracts for agent-authored, non-gating exploratory analyses.

The agent may write and execute code, but the browser never renders stdout,
notebook displays, or arbitrary files. Code must write a small JSON manifest
matching :class:`ExploratoryAnalysisManifest`; the host validates it and wraps
it in an immutable artifact. These results are explicitly exploratory and can
never satisfy or clear a gate.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, JsonValue, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.comprehension import InterpretationKind, ModelConfidence
from ads.contracts.evidence import SubjectRef


class InvestigationActionKind(StrEnum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"
    ABANDON = "abandon"


class InvestigationAction(FrozenModel):
    """One grammar-constrained turn in the investigator's tool loop."""

    action: InvestigationActionKind
    tool_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    artifact_ref: str | None = Field(default=None, pattern=r"^artifact://")
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def _action_shape(self) -> InvestigationAction:
        if self.action is InvestigationActionKind.CALL_TOOL:
            if self.tool_id is None:
                raise ValueError("call_tool requires tool_id.")
            if self.artifact_ref is not None:
                raise ValueError("call_tool cannot carry artifact_ref.")
            if self.tool_id == "execute_python":
                code = self.arguments.get("code")
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("execute_python requires non-empty code.")
                if len(code) > 30_000:
                    raise ValueError("execute_python code exceeds 30,000 characters.")
        else:
            if self.tool_id is not None or self.arguments:
                raise ValueError(f"{self.action.value} cannot carry a tool call.")
            if self.action is InvestigationActionKind.FINISH:
                if self.artifact_ref is None:
                    raise ValueError("finish requires artifact_ref.")
            elif self.artifact_ref is not None:
                raise ValueError("abandon cannot carry artifact_ref.")
        return self


class ExploratoryChartKind(StrEnum):
    BAR = "bar"
    HBAR = "hbar"
    HISTOGRAM = "histogram"


class ExploratorySeriesPoint(FrozenModel):
    label: str = Field(min_length=1, max_length=80)
    value: float
    secondary: float | None = None
    count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _finite(self) -> ExploratorySeriesPoint:
        values = [self.value, *([self.secondary] if self.secondary is not None else [])]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Chart values must be finite.")
        return self


class ExploratoryHistogramBin(FrozenModel):
    lower: float
    upper: float
    count: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered_and_finite(self) -> ExploratoryHistogramBin:
        if not math.isfinite(self.lower) or not math.isfinite(self.upper):
            raise ValueError("Histogram bounds must be finite.")
        if self.lower >= self.upper:
            raise ValueError("Histogram lower bound must be below upper bound.")
        return self


class ExploratoryChart(FrozenModel):
    """Closed chart vocabulary rendered by the existing analysis card."""

    kind: ExploratoryChartKind
    x_label: str = Field(min_length=1, max_length=100)
    y_label: str = Field(min_length=1, max_length=100)
    unit: Literal["count", "value", "percent", "ratio"] = "value"
    signed: bool = False
    series: list[ExploratorySeriesPoint] = Field(default_factory=list, max_length=24)
    bins: list[ExploratoryHistogramBin] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _one_supported_shape(self) -> ExploratoryChart:
        if self.kind is ExploratoryChartKind.HISTOGRAM:
            if not self.bins or self.series:
                raise ValueError("A histogram requires bins and cannot carry series.")
            for left, right in zip(self.bins, self.bins[1:], strict=False):
                if left.upper > right.lower:
                    raise ValueError("Histogram bins must be ordered and non-overlapping.")
        elif not self.series or self.bins:
            raise ValueError("Bar charts require series and cannot carry bins.")
        return self


TableText = Annotated[str, Field(max_length=120)]
TableCell = TableText | int | float


class ExploratoryTable(FrozenModel):
    columns: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1,
        max_length=8,
    )
    rows: list[list[TableCell]] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _rectangular_and_finite(self) -> ExploratoryTable:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Table columns must be unique.")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("Every table row must match the declared columns.")
            if any(isinstance(value, float) and not math.isfinite(value) for value in row):
                raise ValueError("Table values must be finite.")
        return self


class ExploratoryAnalysisManifest(FrozenModel):
    """The only agent-authored file eligible for promotion to the EDA UI."""

    schema_version: Literal["1"] = "1"
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=500)
    subjects: list[SubjectRef] = Field(min_length=1, max_length=6)
    chart: ExploratoryChart
    table: ExploratoryTable | None = None
    interpretation_kind: InterpretationKind
    interpretation: str = Field(min_length=10, max_length=800)
    why_it_matters: str = Field(min_length=10, max_length=800)
    verification_question: str = Field(min_length=10, max_length=500)
    confidence: ModelConfidence

    @model_validator(mode="after")
    def _supported_interpretation(self) -> ExploratoryAnalysisManifest:
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("Exploratory analysis subjects must be unique.")
        if self.interpretation_kind not in {
            InterpretationKind.DISTRIBUTION_PATTERN,
            InterpretationKind.COLUMN_SEMANTICS,
            InterpretationKind.OPEN_QUESTION,
        }:
            raise ValueError(
                "Exploratory EDA can propose only distribution, column, or open-question "
                "interpretations in this first slice."
            )
        return self


class ExploratoryAnalysis(Artifact):
    """Persisted code result; useful for comprehension, never gate evidence."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.EXPLORATORY_ANALYSIS
    schema_version: ClassVar[str] = "1"

    source_eda_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_class: Literal["exploratory"] = "exploratory"
    manifest: ExploratoryAnalysisManifest
    code: str = Field(min_length=1, max_length=30_000)
    code_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_count: int | None = Field(default=None, ge=1)
    tool_calls: list[str] = Field(min_length=1, max_length=12)
    output_ref: str = Field(pattern=r"^artifact://")

    def summary(self) -> dict[str, Any]:
        return {
            "title": self.manifest.title,
            "chart_kind": self.manifest.chart.kind.value,
            "n_subjects": len(self.manifest.subjects),
            "evidence_class": self.evidence_class,
            "tool_calls": len(self.tool_calls),
        }


__all__ = [
    "ExploratoryAnalysis",
    "ExploratoryAnalysisManifest",
    "ExploratoryChart",
    "ExploratoryChartKind",
    "ExploratoryHistogramBin",
    "ExploratorySeriesPoint",
    "ExploratoryTable",
    "InvestigationAction",
    "InvestigationActionKind",
]
