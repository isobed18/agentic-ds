"""Contracts for the measured evaluation and human-facing report stage."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.gates import GateVerdict
from ads.contracts.leakage import LeakageKind
from ads.contracts.problem import Metric, TaskType
from ads.contracts.validation import SplitStrategy


class DecisionAuthority(StrEnum):
    """Who authorized a material evaluation decision."""

    HUMAN = "human"
    AUTONOMOUS = "autonomous"
    UNRECORDED = "unrecorded"


class DecisionRecord(FrozenModel):
    """One material choice and its recorded authority."""

    stage: str
    decision: str = Field(max_length=500)
    authority: DecisionAuthority
    rationale: str | None = Field(default=None, max_length=800)


class CandidateComparison(FrozenModel):
    """Primary-metric comparison for one trained candidate."""

    candidate_id: str
    display_name: str
    is_baseline: bool
    selected: bool
    cv_mean: float
    cv_std: float = Field(ge=0.0)
    holdout_score: float


class HoldoutMetric(FrozenModel):
    """One winner metric measured on the untouched outer holdout."""

    metric: Metric
    score: float


class LeakageDisposition(FrozenModel):
    """A leakage finding and whether it was excluded before training."""

    column: str
    kind: LeakageKind
    score: float
    blocking: bool
    cleared: bool
    action: str


class EvaluationAlert(FrozenModel):
    """A gate outcome that must remain visible in the final report."""

    stage_id: str
    reason_code: str
    verdict: GateVerdict
    detail: str = Field(max_length=1000)


class GateHistoryRecord(FrozenModel):
    """One persisted gate decision, projected with its approval authority."""

    stage_id: str
    attempt: int = Field(ge=1)
    verdict: GateVerdict
    reason_code: str
    triggered_rules: list[str] = Field(default_factory=list)
    authority: DecisionAuthority


class EvaluationReport(Artifact):
    """Auditable model comparison, validation rationale, and decision provenance."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.EVALUATION_REPORT
    schema_version: ClassVar[str] = "2"

    problem_title: str
    problem_description: str
    task_type: TaskType
    target_column: str | None = None
    primary_metric: Metric

    candidate_comparisons: list[CandidateComparison] = Field(min_length=1)
    winner_id: str
    winner_display_name: str
    holdout_metrics: list[HoldoutMetric] = Field(min_length=1)
    baseline_holdout_score: float
    winner_holdout_score: float
    baseline_delta: float
    lower_is_better: bool

    input_row_count: int = Field(ge=0)
    target_null_rows_dropped: int = Field(ge=0)
    training_row_count: int = Field(ge=1)

    validation_strategy: SplitStrategy
    validation_n_folds: int = Field(ge=2)
    validation_test_size: float = Field(gt=0.0, lt=0.9)
    validation_rationale: str
    validation_group_column: str | None = None
    validation_time_column: str | None = None
    validation_holdout_cutoff: str | None = None

    leakage_dispositions: list[LeakageDisposition] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    gate_history: list[GateHistoryRecord] = Field(default_factory=list)
    alerts: list[EvaluationAlert] = Field(default_factory=list)
    persisted_model_matches_measured_model: bool | None = None

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationReport:
        candidate_ids = [item.candidate_id for item in self.candidate_comparisons]
        if self.winner_id not in candidate_ids:
            raise ValueError("winner_id is not present in candidate_comparisons.")
        selected = [item for item in self.candidate_comparisons if item.selected]
        if len(selected) != 1 or selected[0].candidate_id != self.winner_id:
            raise ValueError("Exactly the winner must be marked selected.")
        if sum(item.is_baseline for item in self.candidate_comparisons) != 1:
            raise ValueError("Exactly one candidate comparison must be the baseline.")
        return self

    @property
    def cleared_leakage(self) -> list[LeakageDisposition]:
        return [item for item in self.leakage_dispositions if item.cleared]

    @property
    def unresolved_blocking_leakage(self) -> list[LeakageDisposition]:
        return [item for item in self.leakage_dispositions if item.blocking and not item.cleared]

    @property
    def unresolved_separator_confirmation(self) -> list[LeakageDisposition]:
        return [
            item
            for item in self.leakage_dispositions
            if item.kind is LeakageKind.PERFECT_SEPARATOR and not item.cleared
        ]

    @property
    def prominent_alerts(self) -> list[EvaluationAlert]:
        prominent_codes = {
            "lift_within_noise",
            "degenerate_split",
            "separator_needs_confirmation",
        }
        return [item for item in self.alerts if item.reason_code in prominent_codes]

    def summary(self) -> dict[str, Any]:
        return {
            "problem_title": self.problem_title,
            "winner_id": self.winner_id,
            "primary_metric": self.primary_metric.value,
            "winner_holdout_score": self.winner_holdout_score,
            "baseline_delta": self.baseline_delta,
            "n_alerts": len(self.alerts),
        }


__all__ = [
    "CandidateComparison",
    "DecisionAuthority",
    "DecisionRecord",
    "EvaluationAlert",
    "EvaluationReport",
    "GateHistoryRecord",
    "HoldoutMetric",
    "LeakageDisposition",
]
