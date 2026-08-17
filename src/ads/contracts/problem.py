"""Problem discovery contracts.

Stage 2 is a CRITICAL-risk stage: choosing the wrong target silently invalidates
everything downstream, and no later stage can detect the mistake. So the agent
proposes :class:`ProblemCandidate` objects and a human confirms one into a
:class:`ProblemDefinition` at an always-on gate.

Each candidate carries deterministically-computed **support** figures alongside
the LLM's reasoning, so the human is choosing between options annotated with
facts rather than adjudicating prose.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, JsonValue, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class TaskType(StrEnum):
    BINARY_CLASSIFICATION = "binary_classification"
    MULTICLASS_CLASSIFICATION = "multiclass_classification"
    REGRESSION = "regression"
    ANOMALY_DETECTION = "anomaly_detection"


class Metric(StrEnum):
    """Closed vocabulary of primary metrics.

    An enum rather than a free string: a constrained choice is both far easier
    for a small local model to get right and directly executable by the
    evaluation stage without a parsing step.
    """

    ROC_AUC = "roc_auc"
    AVERAGE_PRECISION = "average_precision"
    F1 = "f1"
    BALANCED_ACCURACY = "balanced_accuracy"
    ACCURACY = "accuracy"
    RMSE = "rmse"
    MAE = "mae"
    R2 = "r2"
    MAPE = "mape"
    SILHOUETTE = "silhouette"


#: Which metrics are meaningful for which task. Enforced, not advisory —
#: "accuracy" on a 0.3%-positive fraud target is how a useless model gets shipped.
METRICS_BY_TASK: dict[TaskType, frozenset[Metric]] = {
    TaskType.BINARY_CLASSIFICATION: frozenset(
        {Metric.ROC_AUC, Metric.AVERAGE_PRECISION, Metric.F1,
         Metric.BALANCED_ACCURACY, Metric.ACCURACY}
    ),
    TaskType.MULTICLASS_CLASSIFICATION: frozenset(
        {Metric.F1, Metric.BALANCED_ACCURACY, Metric.ACCURACY}
    ),
    TaskType.REGRESSION: frozenset({Metric.RMSE, Metric.MAE, Metric.R2, Metric.MAPE}),
    TaskType.ANOMALY_DETECTION: frozenset({Metric.SILHOUETTE, Metric.AVERAGE_PRECISION}),
}

SUPERVISED_TASKS = frozenset(
    {
        TaskType.BINARY_CLASSIFICATION,
        TaskType.MULTICLASS_CLASSIFICATION,
        TaskType.REGRESSION,
    }
)


class ProblemSupport(FrozenModel):
    """Deterministic feasibility evidence for a proposed problem.

    Computed by ``ads.discovery.support``, never by the LLM. This is what turns
    "fraud detection sounds plausible" into "1,240 positives at a 0.3% rate,
    which is below the viability floor".
    """

    n_rows: int = Field(ge=0)
    target_null_rate: float = Field(ge=0.0, le=1.0)
    n_classes: int | None = None
    minority_class_count: int | None = None
    minority_class_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    n_usable_features: int = Field(ge=0)
    rows_per_feature: float = Field(ge=0.0)

    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Deterministic disqualifiers. Non-empty means not viable as-is.",
    )
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_viable(self) -> bool:
        return not self.blocking_reasons


class ProblemCandidateProposal(FrozenModel):
    """What the ProblemDiscoveryAgent emits — judgment only, no measurements.

    ``support`` is deliberately absent: feasibility is measured by
    :mod:`ads.discovery.support`, never asserted by the model. Asking an LLM for
    a positive-class count invites it to guess a plausible number, and that
    number would then gate a CRITICAL decision.

    ``candidate_id`` is likewise absent — it is bookkeeping, assigned by
    :func:`ads.agents.problem_discovery.attach_support`. Models reliably emit
    empty strings for fields that carry no meaning to them, and an id the system
    controls cannot collide.
    """

    title: str = Field(max_length=120)
    task_type: TaskType
    target_column: str | None = Field(
        default=None, description="Required for supervised tasks; null otherwise."
    )
    business_rationale: str = Field(max_length=800)
    evidence_columns: list[str] = Field(
        default_factory=list,
        description="Columns that justify this framing. Must exist in the ABT.",
    )
    primary_metric: Metric

    @model_validator(mode="after")
    def _consistent(self) -> ProblemCandidateProposal:
        if self.task_type in SUPERVISED_TASKS and not self.target_column:
            raise ValueError(f"target_column is required for {self.task_type}")
        allowed = METRICS_BY_TASK[self.task_type]
        if self.primary_metric not in allowed:
            raise ValueError(
                f"metric {self.primary_metric.value!r} is not valid for "
                f"{self.task_type.value!r}; choose one of "
                f"{sorted(m.value for m in allowed)}"
            )
        return self


class ProblemDiscoveryProposal(FrozenModel):
    """The agent's full response: a ranked list of candidate framings."""

    candidates: list[ProblemCandidateProposal] = Field(min_length=1, max_length=6)


class ProblemInvestigationActionKind(StrEnum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"
    ABANDON = "abandon"


class ProblemInvestigationAction(FrozenModel):
    """One turn of active problem-framing investigation before proposal."""

    action: ProblemInvestigationActionKind
    tool_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def _action_shape(self) -> ProblemInvestigationAction:
        if self.action is ProblemInvestigationActionKind.CALL_TOOL:
            if self.tool_id is None:
                raise ValueError("call_tool requires tool_id.")
            if self.tool_id == "execute_python":
                code = self.arguments.get("code")
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("execute_python requires non-empty code.")
                if len(code) > 30_000:
                    raise ValueError("execute_python code exceeds 30,000 characters.")
        elif self.tool_id is not None or self.arguments:
            raise ValueError(f"{self.action.value} cannot carry a tool call.")
        return self


class ProblemCandidate(FrozenModel):
    """A proposed problem with its measured feasibility attached."""

    candidate_id: str
    title: str = Field(max_length=120)
    task_type: TaskType
    target_column: str | None = None
    business_rationale: str = Field(max_length=800)
    evidence_columns: list[str] = Field(default_factory=list)
    primary_metric: Metric
    support: ProblemSupport

    @classmethod
    def from_proposal(
        cls,
        proposal: ProblemCandidateProposal,
        support: ProblemSupport,
        *,
        candidate_id: str,
    ) -> ProblemCandidate:
        return cls(**proposal.model_dump(), support=support, candidate_id=candidate_id)


class ProblemCandidateSet(Artifact):
    """Ranked candidates with measured support, ready for the human gate."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.PROBLEM_CANDIDATES
    schema_version: ClassVar[str] = "2"

    candidates: list[ProblemCandidate] = Field(min_length=1)
    user_intent: str | None = None

    def viable(self) -> list[ProblemCandidate]:
        return [c for c in self.candidates if c.support.is_viable]

    def blocked(self) -> list[ProblemCandidate]:
        return [c for c in self.candidates if not c.support.is_viable]

    def summary(self) -> dict[str, Any]:
        return {
            "n_candidates": len(self.candidates),
            "n_viable": len(self.viable()),
            "titles": [c.title for c in self.candidates],
        }


class ProblemDefinition(Artifact):
    """The confirmed problem. Everything downstream reads this.

    ``confirmed_by`` records whether a human approved it, which the audit log and
    the final report both surface — a fully autonomous run should be visibly
    distinguishable from a human-confirmed one.
    """

    artifact_type: ClassVar[ArtifactType] = ArtifactType.PROBLEM_DEFINITION
    schema_version: ClassVar[str] = "1"

    task_type: TaskType
    target_column: str | None = None
    primary_metric: Metric
    title: str = Field(max_length=120)
    description: str = Field(max_length=1000)
    excluded_columns: list[str] = Field(
        default_factory=list,
        description="Columns barred from features (leakage, identifiers, policy).",
    )
    confirmed_by: str = Field(default="human", pattern="^(human|auto)$")
    source_candidate_id: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "task_type": self.task_type.value,
            "target_column": self.target_column,
            "primary_metric": self.primary_metric.value,
            "confirmed_by": self.confirmed_by,
        }
