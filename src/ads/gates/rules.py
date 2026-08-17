"""Gate rules, in strict precedence order.

Four tiers, evaluated highest-first, first match wins:

1. **HARD** — leakage, PII egress, destructive tools, exhausted retries.
   Never overridable. A user in ``full_auto`` is still stopped by these.
2. **RISK** — the stage's declared risk class.
3. **SIGNAL** — deterministic quality measurements.
4. **PROFILE** — the user's autonomy preference.

The ordering is the design. **User autonomy preference is the weakest input**,
not the strongest: on medical and financial data, "don't ask me anything" must
not be able to switch off leakage detection.

Every rule is a pure function of its inputs, so the whole gate is unit-testable
with no LLM, no I/O and no run state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import IntEnum

from ads.contracts.gates import (
    AutonomyProfile,
    CritiqueResult,
    GateVerdict,
    PermissionTier,
    QualitySignals,
)
from ads.gates.policy import GatePolicy, StageHistory, StageSpec


class Tier(IntEnum):
    HARD = 1
    RISK = 2
    SIGNAL = 3
    PROFILE = 4


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule may read. Deliberately closed."""

    stage: StageSpec
    critique: CritiqueResult | None
    signals: QualitySignals
    profile: AutonomyProfile
    history: StageHistory
    policy: GatePolicy

    failure_detected: bool = False
    """Set by the evaluator: did any substantive rule fire on this attempt?

    Derived from the rule outcomes themselves rather than a hand-written list of
    failure conditions. An earlier version enumerated four families and was
    already missing below-baseline, degenerate splits, weak support and unstable
    scores — so a stage burning its last attempt on any of those proceeded
    silently. A parallel list of "what counts as failure" drifts from the rules
    every time one is added; deriving it cannot.
    """

    @property
    def unmet_mandatory(self) -> frozenset[str]:
        if self.critique is None:
            return frozenset()
        return frozenset(self.critique.unmet_criteria) & self.stage.mandatory_criteria


@dataclass(frozen=True)
class RuleOutcome:
    verdict: GateVerdict
    reason_code: str
    message: str
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rule:
    id: str
    tier: Tier
    evaluate: Callable[[RuleContext], RuleOutcome | None]
    description: str

    def __call__(self, context: RuleContext) -> RuleOutcome | None:
        return self.evaluate(context)


# --------------------------------------------------------------------- tier 1


def _leakage(ctx: RuleContext) -> RuleOutcome | None:
    correlation = ctx.signals.max_target_correlation
    correlation_active = (
        correlation is not None
        and correlation > ctx.policy.thresholds.leakage_correlation
    )
    correlation_columns = (
        ctx.signals.target_relationship_suspect_columns
        if correlation_active
        else []
    )
    columns = sorted(
        set(correlation_columns) | set(ctx.signals.structural_leakage_suspect_columns)
    )
    # Compatibility for pre-v2 signal producers that only populated the original
    # aggregate field. A measured above-threshold correlation remains enforceable.
    if not columns and correlation_active:
        columns = ctx.signals.leakage_suspect_columns
    if not columns and not correlation_active:
        return None
    review = sorted(set(columns) & set(ctx.signals.leakage_challenge_review_columns))
    unresolved = sorted(set(columns) - set(review))
    if review and not unresolved:
        return RuleOutcome(
            verdict=GateVerdict.ESCALATE,
            reason_code="leakage_challenge_needs_confirmation",
            message=(
                f"A registered timestamp test produced reviewable evidence for {review}, "
                "but it cannot establish that the supplied recording timestamp truly "
                "belongs to the feature. A human must confirm that semantic link before "
                "the blocking leakage finding can be accepted as legitimate."
            ),
        )

    # Retry with a machine-generated instruction while budget remains; the fix is
    # mechanical (drop the column), so a human is only needed if it recurs.
    if ctx.history.attempts < ctx.stage.max_attempts:
        measured = (
            f"Feature correlates with the target at {correlation:.3f}, above the "
            f"{ctx.policy.thresholds.leakage_correlation} limit."
            if correlation is not None
            and correlation > ctx.policy.thresholds.leakage_correlation
            else "The deterministic audit found blocking leakage."
        )
        correction_columns = unresolved or columns
        return RuleOutcome(
            verdict=GateVerdict.RETRY,
            reason_code="leakage_detected",
            message=(
                f"{measured} Suspects requiring correction: "
                f"{correction_columns or 'unidentified'}."
            ),
            instructions=tuple(
                f"drop_feature: {column}  # "
                + (
                    "target leakage"
                    if column in correlation_columns
                    else "blocking leakage"
                )
                for column in correction_columns
            )
            or ("Remove the leaking feature(s) identified in the leakage report.",),
        )
    measured = (
        f"Leakage at correlation {correlation:.3f}"
        if correlation is not None
        else "Blocking leakage"
    )
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="leakage_unresolved",
        message=(
            f"{measured} persists after "
            f"{ctx.history.attempts} attempts. Human review required."
        ),
    )


def _pii_egress(ctx: RuleContext) -> RuleOutcome | None:
    count = ctx.signals.pii_columns_in_context
    if count <= 0:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="pii_egress_requested",
        message=(
            f"{count} column(s) classified as PII would be sent to the model. "
            "Explicit human approval is required regardless of autonomy profile."
        ),
    )


def _destructive_tool(ctx: RuleContext) -> RuleOutcome | None:
    tier = ctx.signals.requested_tool_tier
    if tier is None or tier < PermissionTier.MUTATE_SOURCE:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="destructive_operation",
        message=(
            "The stage requested a tool that mutates a source system. This is denied "
            "for all agents in the MVP and requires human action."
        ),
    )


def _retry_budget(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.history.attempts < ctx.stage.max_attempts:
        return None
    if not ctx.failure_detected:
        return None  # last attempt, but nothing went wrong: no reason to stop
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="retry_budget_exhausted",
        message=(
            f"Stage {ctx.stage.id!r} has used {ctx.history.attempts} of "
            f"{ctx.stage.max_attempts} attempts without passing. Human input needed."
        ),
    )


def _stuck_loop(ctx: RuleContext) -> RuleOutcome | None:
    """Same failure twice means more attempts will not help."""
    unmet = ctx.unmet_mandatory
    if not unmet or not ctx.history.repeated_identical_failure(unmet):
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="repeated_identical_failure",
        message=(
            f"Attempt {ctx.history.attempts} failed on exactly the same criteria as the "
            f"previous attempt ({sorted(unmet)}). Escalating rather than retrying."
        ),
    )


# --------------------------------------------------------------------- tier 2


def _critical_stage(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.stage.risk_class not in ctx.profile.escalate_on_risk_class:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="risk_class_gate",
        message=(
            f"Stage {ctx.stage.id!r} is {ctx.stage.risk_class.value} risk and the "
            f"{ctx.profile.name!r} profile requires human confirmation for it."
        ),
    )


# --------------------------------------------------------------------- tier 3


def _unmet_mandatory(ctx: RuleContext) -> RuleOutcome | None:
    """Quality retries fire regardless of autonomy: a bad artifact is bad."""
    unmet = ctx.unmet_mandatory
    if not unmet:
        return None
    if ctx.history.attempts >= ctx.stage.max_attempts:
        return None  # handled by the hard retry-budget rule
    return RuleOutcome(
        verdict=GateVerdict.RETRY,
        reason_code="unmet_mandatory_criteria",
        message=f"Mandatory rubric criteria unmet: {sorted(unmet)}.",
        instructions=tuple(f"Address rubric criterion: {c}" for c in sorted(unmet)),
    )


def _critique_errors(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.critique is None or not ctx.critique.has_errors:
        return None
    if ctx.history.attempts >= ctx.stage.max_attempts:
        return None
    errors = [f.check_id for f in ctx.critique.findings if f.severity == "error"]
    return RuleOutcome(
        verdict=GateVerdict.RETRY,
        reason_code="critique_errors",
        message=f"Critique raised error-severity findings: {errors}.",
        instructions=tuple(
            f.suggested_fix or f"Fix finding {f.check_id}"
            for f in ctx.critique.findings
            if f.severity == "error"
        ),
    )


def _model_below_baseline(ctx: RuleContext) -> RuleOutcome | None:
    delta = ctx.signals.baseline_delta
    if delta is None or delta > ctx.policy.thresholds.min_baseline_delta:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="model_below_baseline",
        message=(
            f"Best model scores {ctx.signals.best_score} against a naive baseline of "
            f"{ctx.signals.naive_baseline_score} (delta {delta:+.4f}). The model adds "
            "no value over guessing."
        ),
    )


def _lift_within_noise(ctx: RuleContext) -> RuleOutcome | None:
    """Positive lift that is smaller than run-to-run variance is not lift.

    ``model_below_baseline`` catches a model that fails to beat the baseline at
    all. It cannot catch one that beats it by a rounding error, because the
    threshold is absolute and metric scales differ by orders of magnitude:
    improving RMSE from 87,076 to 87,075 is a delta of +1, which passes.

    Measuring lift in units of the cross-validation standard deviation is
    scale-free. On the sample data real signal gives lift 61,178 against a CV
    spread of 604 — a ratio of 101 — while permuted-target noise gives 0.
    """
    delta = ctx.signals.baseline_delta
    cv_std = ctx.signals.cv_std
    if delta is None or cv_std is None or cv_std <= 0:
        return None
    if delta <= ctx.policy.thresholds.min_baseline_delta:
        return None  # handled by model_below_baseline, which is more specific

    ratio = delta / cv_std
    if ratio >= ctx.policy.thresholds.min_lift_to_noise_ratio:
        return None

    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="lift_within_noise",
        message=(
            f"The model beats the baseline by {delta:.4g}, but cross-validation varies by "
            f"{cv_std:.4g} between folds — a lift-to-noise ratio of {ratio:.2f}, below the "
            f"{ctx.policy.thresholds.min_lift_to_noise_ratio} floor. The improvement is "
            "indistinguishable from run-to-run variance and should not be reported as real."
        ),
    )


def _high_cv_variance(ctx: RuleContext) -> RuleOutcome | None:
    cov = ctx.signals.cv_coefficient_of_variation
    if cov is None or cov <= ctx.policy.thresholds.cv_coefficient_of_variation:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="high_cv_variance",
        message=(
            f"Cross-validation spread is {cov:.1%} of the mean score, above the "
            f"{ctx.policy.thresholds.cv_coefficient_of_variation:.0%} limit. "
            "The reported score is unstable and should not be trusted."
        ),
    )


def _candidate_disagreement(ctx: RuleContext) -> RuleOutcome | None:
    """Genuine ambiguity, measured by resampling the decision — not self-report.

    Agreement compares the decision-bearing fields only. Two samples that pick
    the same target and metric but word the rationale differently have not
    disagreed, and escalating on that would stop every run for nothing.
    """
    agreement = ctx.signals.self_consistency_agreement
    if agreement is None or agreement >= ctx.policy.thresholds.self_consistency_agreement:
        return None
    panel_size = ctx.signals.panel_size
    if not panel_size:
        return RuleOutcome(
            verdict=GateVerdict.ESCALATE,
            reason_code="candidate_disagreement",
            message=(
                f"Resampling this decision produced only {agreement:.0%} agreement, "
                "indicating genuine ambiguity rather than a clear answer."
            ),
        )

    # State the split, not just the percentage: "67%" hides whether that was
    # 2 of 3 samples or 4 of 6, and those warrant different scrutiny.
    agreed = round(agreement * panel_size)
    valid = ctx.signals.panel_valid_members
    # Falling short of unanimity has two distinct causes, and calling an
    # unstable generation "ambiguity" would send a human looking for a
    # disagreement that was never expressed.
    if valid is not None and valid < panel_size:
        detail = (
            f"{panel_size - valid} of {panel_size} samples produced no valid "
            f"contract, and {agreed} of {panel_size} reached the selected decision"
        )
    else:
        detail = f"only {agreed} of {panel_size} samples reached the same decision"

    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="candidate_disagreement",
        message=(
            f"Resampling this decision was not unanimous: {detail}. "
            "A split decision here is ambiguity, not a clear answer."
        ),
    )


def _statistical_support_low(ctx: RuleContext) -> RuleOutcome | None:
    minority = ctx.signals.minority_class_count
    rows_per_feature = ctx.signals.rows_per_feature
    thresholds = ctx.policy.thresholds

    reasons: list[str] = []
    if minority is not None and minority < thresholds.min_minority_class_count:
        reasons.append(
            f"minority class has {minority} examples "
            f"(floor {thresholds.min_minority_class_count})"
        )
    if rows_per_feature is not None and rows_per_feature < thresholds.min_rows_per_feature:
        reasons.append(
            f"{rows_per_feature:.1f} rows per feature "
            f"(floor {thresholds.min_rows_per_feature})"
        )
    if not reasons:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="statistical_support_low",
        message="Data does not statistically support this step: " + "; ".join(reasons) + ".",
    )


def _separator_needs_confirmation(ctx: RuleContext) -> RuleOutcome | None:
    """A feature that determines the target may still be legitimate.

    Codex FINDING 6: `years_experience >= 20` defining `senior_physician` scores
    ROC AUC 1.0 and is exactly the right explanatory feature. Statistical
    strength identifies a suspect; only a human knows whether a column is
    recorded before the outcome. So this escalates for confirmation instead of
    dropping the feature, and a confirmed column is recorded so re-runs do not
    re-ask.
    """
    columns = ctx.signals.separator_suspect_columns
    if not columns:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="separator_needs_confirmation",
        message=(
            f"{columns} each determine the target almost perfectly on their own. That is "
            "either leakage or a genuine decision rule. Confirm whether each value is "
            "recorded BEFORE the outcome is known: if yes it is a legitimate feature, if "
            "no it must be dropped. This cannot be decided from the data alone."
        ),
    )


def _degenerate_split(ctx: RuleContext) -> RuleOutcome | None:
    """A correct split that leaves too little data is still unusable.

    Measured on the real sample data: `grouped_temporal` on transactions keeps
    only 20.6% of rows and yields 5-row validation folds, because physicians
    transact continuously across the cutoff so entity isolation and strict time
    ordering purge nearly everything. The split is *correct*; the resulting
    metrics would be noise. Without this rule the pipeline reports them as fact.
    """
    thresholds = ctx.policy.thresholds
    reasons: list[str] = []

    retained = ctx.signals.split_retained_rate
    if (
        thresholds.min_split_retained_rate > 0.0
        and retained is not None
        and retained < thresholds.min_split_retained_rate
    ):
        # Phrased as coverage, not precision: a narrow population is a different
        # problem from a noisy estimate, and conflating them misleads the reader.
        reasons.append(
            f"the split covers only {retained:.1%} of rows, so the model describes a "
            f"narrower population than the data (configured floor "
            f"{thresholds.min_split_retained_rate:.0%})"
        )

    fold_size = ctx.signals.min_validation_fold_size
    if fold_size is not None and fold_size < thresholds.min_validation_fold_size:
        reasons.append(
            f"smallest validation fold is {fold_size} rows "
            f"(floor {thresholds.min_validation_fold_size})"
        )

    if not reasons:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="degenerate_split",
        message=(
            "The chosen validation strategy is correct but the split it produces is not "
            "usable: " + "; ".join(reasons) + ". Consider a strategy that drops a "
            "guarantee the data does not actually require, a different grain, or a later "
            "cutoff — and record which guarantee you traded away."
        ),
    )


def _repeated_validation_failures(ctx: RuleContext) -> RuleOutcome | None:
    failures = ctx.signals.validation_failures
    if failures <= ctx.policy.thresholds.max_validation_failures:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="repeated_validation_failures",
        message=(
            f"{failures} output validation failures in this stage. The task is likely "
            "ill-posed rather than the model unlucky."
        ),
    )


# --------------------------------------------------------------------- tier 4


def _profile_checkpoint(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.stage.id not in ctx.profile.checkpoint_stages:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="profile_checkpoint",
        message=(
            f"The {ctx.profile.name!r} profile defines {ctx.stage.id!r} as a checkpoint "
            "requiring human confirmation."
        ),
    )


def _profile_requires_clean_critique(ctx: RuleContext) -> RuleOutcome | None:
    if ctx.profile.auto_proceed_on_clean_critique:
        return None
    return RuleOutcome(
        verdict=GateVerdict.ESCALATE,
        reason_code="profile_requires_confirmation",
        message=(
            f"The {ctx.profile.name!r} profile requires confirmation at every stage, "
            "even when no problems were found."
        ),
    )


#: Signal rules that escalate only when the profile opts in. Rules producing
#: RETRY are quality gates and always apply.
OPT_IN_SIGNAL_RULES = frozenset(
    {
        "model_below_baseline",
        "high_cv_variance",
        "candidate_disagreement",
        "statistical_support_low",
        "degenerate_split",
        "separator_needs_confirmation",
        "lift_within_noise",
        "repeated_validation_failures",
    }
)

RULES: tuple[Rule, ...] = (
    # Tier 1 — hard constraints, never overridable.
    Rule("pii_egress_requested", Tier.HARD, _pii_egress, "PII would enter model context."),
    Rule("destructive_operation", Tier.HARD, _destructive_tool, "Source-mutating tool requested."),
    Rule("leakage_detected", Tier.HARD, _leakage, "Feature leaks the target."),
    Rule("repeated_identical_failure", Tier.HARD, _stuck_loop, "Retry loop is not converging."),
    Rule("retry_budget_exhausted", Tier.HARD, _retry_budget, "Attempts exhausted."),
    # Tier 2 — declared stage risk.
    Rule("risk_class_gate", Tier.RISK, _critical_stage, "Stage risk class requires a human."),
    # Tier 3 — deterministic quality signals.
    Rule("unmet_mandatory_criteria", Tier.SIGNAL, _unmet_mandatory, "Rubric criteria unmet."),
    Rule("critique_errors", Tier.SIGNAL, _critique_errors, "Critique found errors."),
    Rule("model_below_baseline", Tier.SIGNAL, _model_below_baseline, "No lift over baseline."),
    Rule("lift_within_noise", Tier.SIGNAL, _lift_within_noise, "Lift is inside CV variance."),
    Rule("high_cv_variance", Tier.SIGNAL, _high_cv_variance, "Unstable score."),
    Rule("candidate_disagreement", Tier.SIGNAL, _candidate_disagreement, "Ambiguous decision."),
    Rule("statistical_support_low", Tier.SIGNAL, _statistical_support_low, "Weak support."),
    Rule("degenerate_split", Tier.SIGNAL, _degenerate_split, "Split leaves too little data."),
    Rule(
        "separator_needs_confirmation",
        Tier.SIGNAL,
        _separator_needs_confirmation,
        "A feature determines the target; provenance needs confirming.",
    ),
    Rule(
        "repeated_validation_failures",
        Tier.SIGNAL,
        _repeated_validation_failures,
        "Output repeatedly invalid.",
    ),
    # Tier 4 — user autonomy preference, the weakest input.
    Rule("profile_checkpoint", Tier.PROFILE, _profile_checkpoint, "Profile checkpoint stage."),
    Rule(
        "profile_requires_confirmation",
        Tier.PROFILE,
        _profile_requires_clean_critique,
        "Profile confirms every stage.",
    ),
)


#: Rules that describe process or open questions rather than a defect in the
#: artifact. A stage is not "failing" because policy says a human should look at
#: it, nor because a question exists that only a human can answer.
#:
#: ``separator_needs_confirmation`` belongs here for the second reason: a
#: separator is deliberately a non-blocking *suspect* — `years_experience`
#: determining `senior_physician` may be a perfectly legitimate decision rule —
#: and **no number of retries can establish provenance**. Counting it as failure
#: made the attempt boundary convert uncertainty into a hard budget stop, even
#: for profiles that had opted out of asking about it (Codex FINDING 13).
NON_SUBSTANTIVE_RULES = frozenset(
    {
        "risk_class_gate",
        "profile_checkpoint",
        "profile_requires_confirmation",
        "separator_needs_confirmation",
        "retry_budget_exhausted",
    }
)


def detect_failure(context: RuleContext) -> bool:
    """Whether any substantive rule fires, ignoring profile opt-ins.

    Deliberately evaluated over the **full** rule set rather than the profile's
    filtered one. Opting out of escalating on weak support is a statement about
    when to interrupt a human, not a claim that the result is fine — so a run
    that exhausts its attempts on a below-baseline model must still stop, even
    in ``full_auto``.

    Evaluated against a context with one attempt of headroom. Retry-producing
    rules such as :func:`_unmet_mandatory` deliberately stand down at the budget
    boundary so they do not compete with the budget rule — but the budget rule
    now asks *them* whether anything failed. Without the headroom the two defer
    to each other and a stage that failed its final attempt proceeds.

    **Only reads whether a rule fired, never its verdict.** The headroom changes
    some verdicts: :func:`_leakage` returns RETRY under the probe where it would
    return ESCALATE at the real boundary. That is harmless precisely because the
    verdict is discarded here. Making this function inspect verdicts would turn
    that into a live bug.
    """
    probe = replace(
        context,
        stage=replace(context.stage, max_attempts=context.history.attempts + 1),
    )
    return any(
        rule(probe) is not None
        for rule in RULES
        if rule.id not in NON_SUBSTANTIVE_RULES
    )


def applicable_rules(profile: AutonomyProfile) -> tuple[Rule, ...]:
    """Filter rules the profile has not opted into.

    Only tier-3 escalations are opt-in. Hard constraints and quality retries
    always apply, which is what keeps ``full_auto`` safe.
    """
    return tuple(
        rule
        for rule in RULES
        if rule.id not in OPT_IN_SIGNAL_RULES or rule.id in profile.escalate_on_signals
    )


__all__ = [
    "OPT_IN_SIGNAL_RULES",
    "RULES",
    "Rule",
    "RuleContext",
    "RuleOutcome",
    "Tier",
    "applicable_rules",
]
