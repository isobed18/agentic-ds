"""Gate, critique and autonomy contracts — the governance layer.

This module encodes the central design commitment of the system: **the LLM is a
sensor, the policy is the actuator.**

* :class:`CritiqueResult` is what the LLM produces. It carries findings and
  evidence. It deliberately has **no confidence field** — models are poorly
  calibrated and a self-assessment slot invites a constant ``0.95``.
* :class:`QualitySignals` is what Python measures.
* :class:`GateDecision` is the verdict, produced by a pure deterministic
  function of the two plus the run's autonomy profile.

The LLM is never asked whether to consult the human, and never sees the rules.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any, ClassVar

from pydantic import Field

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class RiskClass(StrEnum):
    """Static, per-stage classification declared in the WorkflowSpec."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class GateVerdict(StrEnum):
    AUTO_PROCEED = "auto_proceed"
    RETRY = "retry"
    ESCALATE = "escalate"
    ABORT = "abort"


class PermissionTier(IntEnum):
    """Tool permission levels enforced by the permission broker.

    ``MUTATE_SOURCE`` is denied unconditionally for every agent in the MVP: the
    system reads enterprise data and writes only to its own artifact store.
    """

    READ_META = 10
    READ_DATA = 20
    EXECUTE = 30
    WRITE_ARTIFACT = 40
    MUTATE_SOURCE = 90


class Finding(FrozenModel):
    """One issue raised by the Orchestrator's critique of a stage artifact."""

    check_id: str = Field(description="Must come from the stage rubric's closed vocabulary.")
    severity: Severity
    evidence: str = Field(max_length=500, description="Must cite the artifact under review.")
    suggested_fix: str | None = Field(default=None, max_length=300)


class CritiqueResult(Artifact):
    """LLM assessment of a completed stage. Evidence, not verdict."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.CRITIQUE
    schema_version: ClassVar[str] = "1"

    stage_id: str
    rubric_version: str
    findings: list[Finding] = Field(default_factory=list)
    unmet_criteria: list[str] = Field(
        default_factory=list, description="Subset of the rubric's criterion ids."
    )
    # NOTE: no `confidence` field. This is deliberate — see module docstring.

    @property
    def has_errors(self) -> bool:
        return any(f.severity == Severity.ERROR for f in self.findings)

    def summary(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "n_findings": len(self.findings),
            "unmet_criteria": self.unmet_criteria,
            "has_errors": self.has_errors,
        }


class QualitySignals(FrozenModel):
    """Deterministic measurements consumed by the Gate Evaluator.

    Every field is computed by Python. None is self-reported by a model. Fields
    are optional because not every signal is meaningful at every stage; rules
    must handle ``None`` by not firing.
    """

    # Leakage / correctness
    max_target_correlation: float | None = Field(default=None, ge=0.0, le=1.0)
    leakage_suspect_columns: list[str] = Field(default_factory=list)
    target_relationship_suspect_columns: list[str] = Field(default_factory=list)
    structural_leakage_suspect_columns: list[str] = Field(default_factory=list)
    leakage_challenge_review_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Blocking leakage findings for which a registered deterministic test "
            "produced evidence worth human review. This never auto-clears a finding."
        ),
    )
    separator_suspect_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Features that determine the target but may legitimately be available "
            "before it. Statistical strength cannot establish provenance, so these "
            "need a human to confirm rather than being dropped automatically."
        ),
    )

    # Model quality
    best_score: float | None = None
    naive_baseline_score: float | None = None
    cv_mean: float | None = None
    cv_std: float | None = Field(default=None, ge=0.0)

    # Statistical support
    n_rows: int | None = Field(default=None, ge=0)
    minority_class_count: int | None = Field(default=None, ge=0)
    rows_per_feature: float | None = Field(default=None, ge=0.0)

    # Split viability — measured after the split is built, not before.
    # A strategy can be perfectly correct and still leave too little data to
    # learn from: enforcing entity isolation and strict time ordering together
    # purges every row of an entity that straddles the cutoff.
    split_retained_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of ABT rows surviving the split after purging.",
    )
    min_validation_fold_size: int | None = Field(
        default=None,
        ge=0,
        description="Smallest validation fold. Tiny folds make metrics meaningless.",
    )

    # Agreement / ambiguity — from resampling the decision, not from self-report
    self_consistency_agreement: float | None = Field(default=None, ge=0.0, le=1.0)
    panel_size: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description=(
            "Members sampled to produce self_consistency_agreement. Carried "
            "because agreement is a coarse fraction, not a continuous score: at "
            "panel_size 3 the only reachable values are 1/3, 2/3 and 1, so a "
            "threshold means a different policy at each size. A rule that reads "
            "the fraction without the denominator cannot say what it enforced."
        ),
    )
    panel_valid_members: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Members that produced a valid contract. Agreement is measured over "
            "the whole panel, so a member that validated nothing lowers it just "
            "as a dissenting member does. Those are different problems — an "
            "unstable generation versus a genuinely ambiguous decision — and "
            "the gate message should not describe one as the other."
        ),
    )

    # Process
    validation_failures: int = Field(default=0, ge=0)
    tool_errors: int = Field(default=0, ge=0)
    pii_columns_in_context: int = Field(default=0, ge=0)
    requested_tool_tier: PermissionTier | None = None

    @property
    def cv_coefficient_of_variation(self) -> float | None:
        """Relative CV spread. High values mean the score is unstable."""
        if self.cv_mean is None or self.cv_std is None:
            return None
        if self.cv_mean == 0:
            return None
        return abs(self.cv_std / self.cv_mean)

    @property
    def baseline_delta(self) -> float | None:
        """How much the model beats a naive baseline. Negative or zero is bad."""
        if self.best_score is None or self.naive_baseline_score is None:
            return None
        return self.best_score - self.naive_baseline_score


class DecisionOption(FrozenModel):
    """A pre-computed choice offered to the human.

    Pre-computing options is what lets a domain expert who is not an ML expert
    participate: they can choose between annotated consequences, but cannot
    usefully answer "what validation strategy should I use?".
    """

    option_id: str
    label: str = Field(max_length=200)
    consequence: str = Field(max_length=400)
    downstream_effect: str | None = Field(default=None, max_length=300)
    recommended: bool = False


class HumanPrompt(FrozenModel):
    """A typed escalation to the user."""

    stage_id: str
    question: str = Field(max_length=500)
    # Sorunun HANGI turden oldugu, metnin kendisinden ayri. Metin backend'de
    # Ingilizce uretiliyor ve kart onu ham basiyordu; arayuz Turkcelestirmek
    # icin cumleyi degil TURU bilmek zorunda (#192). Varsayilan, alani
    # gondermeyen eski cagiranlar icin.
    question_kind: str = Field(default="problem", pattern="^(problem|checkpoint|no_output)$")
    context_summary: str = Field(max_length=1500)
    # #262: the exact suspect columns behind a leakage escalation, already
    # computed by the rule that fired. Lets the card offer checkboxes built
    # from the same data the automatic retry already uses, instead of a human
    # having to hand-type the `drop_feature: <column>` machine syntax that
    # `leakage_audit_stage` alone understands. Empty for every non-leakage
    # escalation. `leakage_target_columns` is the subset that are
    # target-correlation suspects rather than structural ones -- the client
    # needs this to label each checkbox the same way the auto-retry
    # instruction comment already does ("target leakage" vs "blocking
    # leakage").
    leakage_suspect_columns: list[str] = Field(default_factory=list)
    leakage_target_columns: list[str] = Field(default_factory=list)
    options: list[DecisionOption] = Field(default_factory=list)
    allows_free_text: bool = True
    artifacts_to_review: list[str] = Field(default_factory=list)
    default_option: str | None = None
    timeout_behavior: str = Field(default="wait", pattern="^(wait|use_default|abort)$")


class GateDecision(Artifact):
    """The verdict. Deterministic, auditable, and unbypassable by the LLM."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.GATE_DECISION
    schema_version: ClassVar[str] = "1"

    stage_id: str
    attempt: int = Field(ge=1)
    verdict: GateVerdict
    reason_code: str = Field(description="Closed vocabulary; the audit log groups on this.")
    triggered_rules: list[str] = Field(
        default_factory=list, description="Exact rule ids that fired, in precedence order."
    )
    human_prompt: HumanPrompt | None = None
    correction_instructions: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "attempt": self.attempt,
            "verdict": self.verdict.value,
            "reason_code": self.reason_code,
            "triggered_rules": self.triggered_rules,
        }


class AutonomyProfile(FrozenModel):
    """User-configurable autonomy. The *weakest* input to the gate.

    Precedence is intentional: hard constraints (leakage, PII egress,
    destructive tools, retry exhaustion) outrank risk class, which outranks
    quality signals, which outrank this. A user in ``full_auto`` is still
    stopped for leakage.
    """

    name: str
    checkpoint_stages: list[str] = Field(default_factory=list)
    escalate_on_risk_class: list[RiskClass] = Field(default_factory=list)
    escalate_on_signals: list[str] = Field(default_factory=list)
    max_auto_retries: int = Field(default=3, ge=0, le=10)
    auto_proceed_on_clean_critique: bool = True


#: Quality signals every non-``full_auto`` profile escalates on. A stricter
#: profile must never escalate on *fewer* signals than a looser one, so these are
#: shared rather than repeated per profile.
STANDARD_ESCALATION_SIGNALS = [
    "candidate_disagreement",
    "model_below_baseline",
    "high_cv_variance",
    "statistical_support_low",
    "degenerate_split",
    "separator_needs_confirmation",
    "lift_within_noise",
    "repeated_validation_failures",
]

BUILTIN_PROFILES: dict[str, AutonomyProfile] = {
    "supervised": AutonomyProfile(
        name="supervised",
        checkpoint_stages=[
            "schema_discovery",
            "problem_discovery",
            "integration",
            "validation_strategy",
            "eda",
            "feature_pipeline",
            "model_selection",
            "evaluation",
        ],
        escalate_on_risk_class=[RiskClass.MEDIUM, RiskClass.HIGH, RiskClass.CRITICAL],
        escalate_on_signals=list(STANDARD_ESCALATION_SIGNALS),
        max_auto_retries=1,
        auto_proceed_on_clean_critique=False,
    ),
    "checkpointed": AutonomyProfile(
        name="checkpointed",
        checkpoint_stages=["problem_discovery", "validation_strategy", "evaluation"],
        escalate_on_risk_class=[RiskClass.HIGH, RiskClass.CRITICAL],
        escalate_on_signals=list(STANDARD_ESCALATION_SIGNALS),
        max_auto_retries=3,
    ),
    "autonomous_with_guardrails": AutonomyProfile(
        name="autonomous_with_guardrails",
        checkpoint_stages=[],
        escalate_on_risk_class=[RiskClass.CRITICAL],
        escalate_on_signals=list(STANDARD_ESCALATION_SIGNALS),
        max_auto_retries=5,
    ),
    "full_auto": AutonomyProfile(
        name="full_auto",
        checkpoint_stages=[],
        escalate_on_risk_class=[],
        escalate_on_signals=[],
        max_auto_retries=5,
    ),
}

DEFAULT_PROFILE = "checkpointed"
