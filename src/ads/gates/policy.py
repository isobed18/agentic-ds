"""Gate policy: thresholds, stage metadata and run history.

The split here is deliberate. **Thresholds and rule enablement are data** —
loadable from YAML, tunable per deployment without a code change. **Predicate
logic is typed Python** — because a rule like ``cv_std / cv_mean > 0.25`` is
awkward to express in a policy DSL and trivial to express and unit-test in
Python.

That is the concrete form of the report's conclusion that a custom rules engine
beats OPA/Rego here: our gating is numeric and stage-scoped, not
resource-hierarchical, and it must run in-process on an air-gapped machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ads.contracts.gates import RiskClass

DEFAULT_POLICY_PATH = Path(__file__).with_name("default_policy.yaml")


@dataclass(frozen=True)
class GateThresholds:
    """Numeric limits. Every one is a product decision, so none are inline."""

    leakage_correlation: float = 0.95
    """Above this correlation with the target, a feature is leakage."""
    cv_coefficient_of_variation: float = 0.25
    """Above this relative spread across folds, a score is unstable."""
    self_consistency_agreement: float = 1.0
    """Below this agreement across resampled decisions, the choice is ambiguous.

    Unanimity. Any dissent among the panel escalates.

    This is not a knob that can be tuned continuously, because agreement is a
    coarse fraction whose reachable values depend on the panel size. At
    ``panel_size`` 3 the only outcomes are 1/3, 2/3 and 1, so the previous 0.60
    admitted a clean 2-1 split — one sample choosing a different target column
    from the other two passed silently, on the stage whose whole purpose is to
    surface an ambiguous decision to a human. The same 0.60 at ``panel_size`` 2
    meant "escalate unless unanimous". One number, two policies.

    The cost is real and deliberate: a panel is now strictly more interrupting
    than a single agent, so ``panel_size`` 1 remains the default and asking for
    a panel is asking for the extra scrutiny. Because a member that produced no
    valid contract also fails unanimity, ``panel_valid_members`` travels with
    the signal so the gate can say which of the two happened.
    """
    min_baseline_delta: float = 0.0
    """A model must beat a naive baseline by more than this (absolute)."""
    min_lift_to_noise_ratio: float = 1.0
    """Lift over baseline must exceed this multiple of the CV standard deviation.

    An absolute delta cannot serve every metric: RMSE lift is in target units
    (tens of thousands on the sample data) while ROC AUC lift is bounded by 1.0.
    Scaling by observed fold spread at least makes the comparison dimensionless.

    **This is a stability heuristic, not a statistical test, and 1.0 is not a
    calibrated value.** The numerator is the winner-vs-baseline delta on the
    outer holdout while the denominator is the winner's inner-fold spread — two
    different populations. It also ignores baseline variance and discards the
    paired fold structure. Do not divide by ``sqrt(n_folds)`` to make it look
    like a standard error: CV folds share most of their training data, so they
    are not independent observations and that would manufacture false precision.

    The defensible replacement is per-fold paired lift
    (``winner_score[i] - baseline_score[i]``) with a documented uncertainty
    procedure. Tracked as an open calibration item.
    """
    min_minority_class_count: int = 50
    min_rows_per_feature: float = 5.0
    min_split_retained_rate: float = 0.0
    """Coverage floor for rows surviving the split. **Disabled by default.**

    Retained rate measures *coverage* — which population the model was fit on —
    not metric precision. A split retaining 49% of ten million rows is not noisy;
    it just describes a narrower population. The observed failure (20.6%
    retained) is caught by ``min_validation_fold_size``, which is the threshold
    that actually speaks to precision.

    Left at 0.0 until calibrated against real deployments. Set it when you have a
    defined minimum coverage for your domain.
    """
    min_validation_fold_size: int = 30
    """A metric computed on fewer rows than this is noise, not an estimate."""
    max_validation_failures: int = 2
    """Repeated validation failures mean the task is ill-posed, not unlucky."""


@dataclass(frozen=True)
class StageSpec:
    """Static, declared properties of a pipeline stage.

    ``risk_class`` and ``mandatory_criteria`` are authored by us in the
    WorkflowSpec, not inferred at runtime — that is what makes the gate's
    behaviour predictable and reviewable before a run starts.
    """

    id: str
    risk_class: RiskClass = RiskClass.LOW
    max_attempts: int = 3
    mandatory_criteria: frozenset[str] = frozenset()
    """Rubric criteria that must be met; unmet ones force a retry."""
    irreversible: bool = False
    """Whether proceeding past this stage discards work that cannot be rebuilt."""


@dataclass(frozen=True)
class StageHistory:
    """What has already happened for this stage in this run."""

    attempts: int = 1
    prior_unmet_criteria: tuple[frozenset[str], ...] = ()
    """Unmet criteria per previous attempt, used to detect a stuck loop."""

    @property
    def is_first_attempt(self) -> bool:
        return self.attempts <= 1

    def repeated_identical_failure(self, current: frozenset[str]) -> bool:
        """Whether this attempt failed on exactly the same criteria as the last.

        A different failure each time suggests progress; the same failure twice
        means more attempts will not help, so escalate early rather than burning
        the budget.
        """
        if not self.prior_unmet_criteria or not current:
            return False
        return self.prior_unmet_criteria[-1] == current


@dataclass(frozen=True)
class GatePolicy:
    """The full policy: thresholds plus per-stage specs."""

    thresholds: GateThresholds = field(default_factory=GateThresholds)
    stages: dict[str, StageSpec] = field(default_factory=dict)

    def stage(self, stage_id: str) -> StageSpec:
        """Return the declared spec, defaulting to a conservative unknown stage."""
        return self.stages.get(stage_id, StageSpec(id=stage_id, risk_class=RiskClass.MEDIUM))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GatePolicy:
        thresholds = GateThresholds(**(raw.get("thresholds") or {}))
        stages: dict[str, StageSpec] = {}
        for stage_id, spec in (raw.get("stages") or {}).items():
            spec = spec or {}
            stages[stage_id] = StageSpec(
                id=stage_id,
                risk_class=RiskClass(spec.get("risk_class", "low")),
                max_attempts=int(spec.get("max_attempts", 3)),
                mandatory_criteria=frozenset(spec.get("mandatory_criteria", ())),
                irreversible=bool(spec.get("irreversible", False)),
            )
        return cls(thresholds=thresholds, stages=stages)

    @classmethod
    def load(cls, path: str | Path | None = None) -> GatePolicy:
        path = Path(path) if path else DEFAULT_POLICY_PATH
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(raw)


__all__ = [
    "DEFAULT_POLICY_PATH",
    "GatePolicy",
    "GateThresholds",
    "StageHistory",
    "StageSpec",
]
