"""Gate Evaluator tests.

These are the most important tests in the codebase. The gate decides when a
human is consulted on medical and financial data, so its behaviour must be
pinned exactly — especially the precedence ordering that keeps ``full_auto``
from switching off leakage detection.

Every test runs with no LLM, no I/O and no run state: the gate is a pure
function.
"""

from __future__ import annotations

import pytest

from ads.contracts.gates import (
    BUILTIN_PROFILES,
    AutonomyProfile,
    CritiqueResult,
    Finding,
    GateVerdict,
    PermissionTier,
    QualitySignals,
    RiskClass,
    Severity,
)
from ads.gates import (
    GatePolicy,
    StageHistory,
    StageSpec,
    applicable_rules,
    evaluate_gate,
)

CHECKPOINTED = BUILTIN_PROFILES["checkpointed"]
FULL_AUTO = BUILTIN_PROFILES["full_auto"]
SUPERVISED = BUILTIN_PROFILES["supervised"]
GUARDRAILS = BUILTIN_PROFILES["autonomous_with_guardrails"]


def _stage(**overrides) -> StageSpec:
    base = dict(id="eda", risk_class=RiskClass.LOW, max_attempts=3)
    return StageSpec(**{**base, **overrides})


def _decide(**kwargs):
    kwargs.setdefault("stage", _stage())
    kwargs.setdefault("signals", QualitySignals())
    kwargs.setdefault("profile", FULL_AUTO)
    return evaluate_gate(**kwargs)


class TestDefaultBehaviour:
    def test_clean_run_auto_proceeds(self) -> None:
        decision = _decide()
        assert decision.verdict is GateVerdict.AUTO_PROCEED
        assert decision.reason_code == "no_rule_triggered"
        assert decision.triggered_rules == []

    def test_decision_is_deterministic(self) -> None:
        signals = QualitySignals(max_target_correlation=0.99)
        first = _decide(signals=signals)
        second = _decide(signals=signals)
        assert first.verdict is second.verdict
        assert first.triggered_rules == second.triggered_rules

    def test_decision_names_the_rules_that_fired(self) -> None:
        decision = _decide(
            signals=QualitySignals(pii_columns_in_context=2),
            profile=CHECKPOINTED,
        )
        assert "pii_egress_requested" in decision.triggered_rules
        assert decision.reason_code == "pii_egress_requested"


class TestHardConstraintsAreUnbypassable:
    """The load-bearing property: full_auto cannot switch off safety rules."""

    def test_leakage_stops_even_in_full_auto(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                max_target_correlation=0.98,
                leakage_suspect_columns=["total_comp_ytd"],
            ),
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.RETRY
        assert decision.reason_code == "leakage_detected"
        assert any("total_comp_ytd" in i for i in decision.correction_instructions)

    def test_structural_leakage_stops_without_any_correlation_signal(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                leakage_suspect_columns=["followup_missing"],
                structural_leakage_suspect_columns=["followup_missing"],
            ),
            profile=FULL_AUTO,
        )

        assert decision.verdict is GateVerdict.RETRY
        assert decision.reason_code == "leakage_detected"
        assert "followup_missing" in decision.correction_instructions[0]

    def test_registered_challenge_requires_human_and_never_auto_clears(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                max_target_correlation=0.99,
                leakage_suspect_columns=["legitimate_rule"],
                target_relationship_suspect_columns=["legitimate_rule"],
                leakage_challenge_review_columns=["legitimate_rule"],
            ),
            profile=FULL_AUTO,
        )

        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "leakage_challenge_needs_confirmation"
        assert decision.human_prompt is not None

    def test_pii_egress_stops_even_in_full_auto(self) -> None:
        decision = _decide(signals=QualitySignals(pii_columns_in_context=1), profile=FULL_AUTO)
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "pii_egress_requested"

    def test_destructive_tool_stops_even_in_full_auto(self) -> None:
        decision = _decide(
            signals=QualitySignals(requested_tool_tier=PermissionTier.MUTATE_SOURCE),
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "destructive_operation"

    def test_non_destructive_tool_does_not_stop(self) -> None:
        decision = _decide(
            signals=QualitySignals(requested_tool_tier=PermissionTier.EXECUTE),
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_leakage_below_threshold_does_not_fire(self) -> None:
        decision = _decide(signals=QualitySignals(max_target_correlation=0.90))
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_leakage_escalates_once_retries_are_gone(self) -> None:
        decision = _decide(
            signals=QualitySignals(max_target_correlation=0.99),
            history=StageHistory(attempts=3),
            stage=_stage(max_attempts=3),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.human_prompt is not None


class TestPrecedence:
    def test_hard_rule_determines_the_verdict(self) -> None:
        """A tier-4 escalation must not pre-empt a tier-1 automatic fix."""
        decision = _decide(
            stage=_stage(id="problem_discovery"),
            signals=QualitySignals(max_target_correlation=0.99, leakage_suspect_columns=["x"]),
            profile=SUPERVISED,
        )
        assert decision.verdict is GateVerdict.RETRY
        assert decision.reason_code == "leakage_detected"

    def test_procedural_stop_does_not_mask_a_substantive_finding(self) -> None:
        """`evaluation` is HIGH risk, but 'below baseline' is what matters."""
        decision = _decide(
            stage=_stage(id="evaluation", risk_class=RiskClass.HIGH),
            signals=QualitySignals(best_score=0.61, naive_baseline_score=0.63),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "model_below_baseline"
        assert "risk_class_gate" in decision.triggered_rules, "audit keeps both"

    def test_audit_trail_records_every_rule_that_fired(self) -> None:
        decision = _decide(
            stage=_stage(id="evaluation", risk_class=RiskClass.HIGH),
            signals=QualitySignals(
                best_score=0.5, naive_baseline_score=0.9, cv_mean=0.5, cv_std=0.4
            ),
            profile=CHECKPOINTED,
        )
        assert {"model_below_baseline", "high_cv_variance", "risk_class_gate"} <= set(
            decision.triggered_rules
        )

    def test_risk_class_and_signals_both_recorded(self) -> None:
        """Tier decides the verdict; the substantive finding is the headline."""
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            signals=QualitySignals(best_score=0.5, naive_baseline_score=0.5),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "model_below_baseline"
        assert "risk_class_gate" in decision.triggered_rules

    def test_pure_policy_stop_reports_the_policy_reason(self) -> None:
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            profile=CHECKPOINTED,
        )
        assert decision.reason_code == "risk_class_gate"

    def test_most_severe_verdict_wins_within_a_tier(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                pii_columns_in_context=1,
                max_target_correlation=0.99,
            ),
            profile=FULL_AUTO,
        )
        # Both fire in tier 1; ESCALATE outranks RETRY.
        assert decision.verdict is GateVerdict.ESCALATE
        assert len(decision.triggered_rules) >= 2

    def test_retry_budget_exhaustion_escalates(self) -> None:
        """Exhaustion escalates only when the attempt actually failed."""
        decision = _decide(
            stage=_stage(max_attempts=3),
            signals=QualitySignals(validation_failures=3),
            history=StageHistory(attempts=3),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "retry_budget_exhausted"


class TestRiskClass:
    def test_critical_stage_escalates_under_default_profile(self) -> None:
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "risk_class_gate"

    def test_critical_stage_proceeds_under_full_auto(self) -> None:
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_medium_risk_escalates_only_under_supervised(self) -> None:
        stage = _stage(id="feature_pipeline", risk_class=RiskClass.MEDIUM)
        assert _decide(stage=stage, profile=SUPERVISED).verdict is GateVerdict.ESCALATE
        assert _decide(stage=stage, profile=GUARDRAILS).verdict is GateVerdict.AUTO_PROCEED


class TestQualitySignals:
    def test_model_below_baseline_escalates(self) -> None:
        decision = _decide(
            signals=QualitySignals(best_score=0.61, naive_baseline_score=0.63),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "model_below_baseline"

    def test_model_above_baseline_proceeds(self) -> None:
        decision = _decide(
            signals=QualitySignals(best_score=0.81, naive_baseline_score=0.63),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_unstable_cv_escalates(self) -> None:
        decision = _decide(signals=QualitySignals(cv_mean=0.80, cv_std=0.30), profile=CHECKPOINTED)
        assert decision.reason_code == "high_cv_variance"

    def test_stable_cv_proceeds(self) -> None:
        decision = _decide(signals=QualitySignals(cv_mean=0.80, cv_std=0.02), profile=CHECKPOINTED)
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_disagreement_escalates(self) -> None:
        """Ambiguity measured by resampling, not by self-report."""
        decision = _decide(
            signals=QualitySignals(self_consistency_agreement=0.4), profile=CHECKPOINTED
        )
        assert decision.reason_code == "candidate_disagreement"

    def test_perfect_agreement_proceeds(self) -> None:
        decision = _decide(
            signals=QualitySignals(self_consistency_agreement=1.0), profile=CHECKPOINTED
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_weak_statistical_support_escalates(self) -> None:
        decision = _decide(signals=QualitySignals(minority_class_count=12), profile=CHECKPOINTED)
        assert decision.reason_code == "statistical_support_low"

    def test_signal_rules_are_opt_in(self) -> None:
        """full_auto does not opt into signal escalations, but keeps hard rules."""
        signals = QualitySignals(best_score=0.5, naive_baseline_score=0.9)
        assert _decide(signals=signals, profile=FULL_AUTO).verdict is GateVerdict.AUTO_PROCEED
        assert _decide(signals=signals, profile=CHECKPOINTED).verdict is GateVerdict.ESCALATE


class TestCritiqueDrivenRetries:
    def test_unmet_mandatory_criteria_retries(self) -> None:
        critique = CritiqueResult(
            stage_id="eda",
            rubric_version="eda.v1",
            unmet_criteria=["eda.all_features_covered"],
        )
        decision = _decide(
            stage=_stage(mandatory_criteria=frozenset({"eda.all_features_covered"})),
            critique=critique,
        )
        assert decision.verdict is GateVerdict.RETRY
        assert decision.reason_code == "unmet_mandatory_criteria"
        assert decision.correction_instructions

    def test_non_mandatory_unmet_criteria_do_not_retry(self) -> None:
        critique = CritiqueResult(
            stage_id="eda", rubric_version="eda.v1", unmet_criteria=["eda.nice_to_have"]
        )
        decision = _decide(
            stage=_stage(mandatory_criteria=frozenset({"eda.all_features_covered"})),
            critique=critique,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_quality_retries_apply_even_in_full_auto(self) -> None:
        critique = CritiqueResult(
            stage_id="eda", rubric_version="eda.v1", unmet_criteria=["eda.covered"]
        )
        decision = _decide(
            stage=_stage(mandatory_criteria=frozenset({"eda.covered"})),
            critique=critique,
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.RETRY

    def test_error_findings_trigger_retry(self) -> None:
        critique = CritiqueResult(
            stage_id="eda",
            rubric_version="eda.v1",
            findings=[
                Finding(
                    check_id="eda.missing_target_plot",
                    severity=Severity.ERROR,
                    evidence="No target distribution in the report.",
                    suggested_fix="Add a target distribution plot.",
                )
            ],
        )
        decision = _decide(critique=critique)
        assert decision.verdict is GateVerdict.RETRY
        assert "Add a target distribution plot." in decision.correction_instructions

    def test_repeated_identical_failure_escalates_early(self) -> None:
        """Same failure twice means more attempts will not help."""
        unmet = frozenset({"eda.all_features_covered"})
        critique = CritiqueResult(
            stage_id="eda", rubric_version="eda.v1", unmet_criteria=sorted(unmet)
        )
        decision = _decide(
            stage=_stage(max_attempts=5, mandatory_criteria=unmet),
            critique=critique,
            history=StageHistory(attempts=2, prior_unmet_criteria=(unmet,)),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "repeated_identical_failure"

    def test_different_failure_keeps_retrying(self) -> None:
        critique = CritiqueResult(stage_id="eda", rubric_version="eda.v1", unmet_criteria=["eda.b"])
        decision = _decide(
            stage=_stage(max_attempts=5, mandatory_criteria=frozenset({"eda.a", "eda.b"})),
            critique=critique,
            history=StageHistory(attempts=2, prior_unmet_criteria=(frozenset({"eda.a"}),)),
        )
        assert decision.verdict is GateVerdict.RETRY


class TestAutonomyProfiles:
    def test_checkpoint_stage_escalates(self) -> None:
        decision = _decide(stage=_stage(id="evaluation"), profile=CHECKPOINTED)
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "profile_checkpoint"

    def test_non_checkpoint_stage_proceeds(self) -> None:
        decision = _decide(stage=_stage(id="eda"), profile=CHECKPOINTED)
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_supervised_confirms_every_stage(self) -> None:
        decision = _decide(stage=_stage(id="unlisted_stage"), profile=SUPERVISED)
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "profile_requires_confirmation"

    def test_stricter_profiles_never_escalate_on_fewer_signals(self) -> None:
        """A stricter profile must be a superset; otherwise 'strict' is a lie."""
        assert set(GUARDRAILS.escalate_on_signals) <= set(CHECKPOINTED.escalate_on_signals)
        assert set(CHECKPOINTED.escalate_on_signals) <= set(SUPERVISED.escalate_on_signals)

    def test_full_auto_only_keeps_hard_rules(self) -> None:
        rule_ids = {r.id for r in applicable_rules(FULL_AUTO)}
        assert "leakage_detected" in rule_ids
        assert "pii_egress_requested" in rule_ids
        assert "model_below_baseline" not in rule_ids


class TestHumanPrompt:
    def test_escalation_carries_a_typed_prompt(self) -> None:
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            profile=CHECKPOINTED,
            artifact_ids=["abc123"],
            context_summary="Three candidate problems were proposed.",
        )
        prompt = decision.human_prompt
        assert prompt is not None
        assert prompt.stage_id == "problem_discovery"
        assert prompt.artifacts_to_review == ["abc123"]
        assert "Three candidate problems" in prompt.context_summary

    def test_prompt_offers_precomputed_options(self) -> None:
        decision = _decide(stage=_stage(id="evaluation"), profile=CHECKPOINTED)
        options = {o.option_id for o in decision.human_prompt.options}
        assert {"approve", "retry", "abort"} <= options

    def test_escalation_never_auto_decides(self) -> None:
        decision = _decide(stage=_stage(id="evaluation"), profile=CHECKPOINTED)
        assert decision.human_prompt.default_option is None
        assert decision.human_prompt.timeout_behavior == "wait"

    def test_irreversible_stage_offers_inspection_first(self) -> None:
        decision = _decide(
            stage=_stage(id="report", risk_class=RiskClass.CRITICAL, irreversible=True),
            profile=CHECKPOINTED,
        )
        assert decision.human_prompt.options[0].option_id == "review_first"

    def test_retry_verdict_has_no_human_prompt(self) -> None:
        decision = _decide(signals=QualitySignals(max_target_correlation=0.99))
        assert decision.verdict is GateVerdict.RETRY
        assert decision.human_prompt is None

    def test_procedural_stop_does_not_recommend_rework(self) -> None:
        """Nothing is wrong here — recommending rework would mislead the user."""
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL),
            profile=CHECKPOINTED,
        )
        recommended = [o.option_id for o in decision.human_prompt.options if o.recommended]
        assert recommended == []
        assert "required checkpoint" in decision.human_prompt.question

    def test_problem_driven_stop_recommends_rework(self) -> None:
        decision = _decide(
            signals=QualitySignals(best_score=0.5, naive_baseline_score=0.9),
            profile=CHECKPOINTED,
        )
        recommended = [o.option_id for o in decision.human_prompt.options if o.recommended]
        assert recommended == ["retry"]
        assert "problem was detected" in decision.human_prompt.question


class TestPolicyLoading:
    def test_default_policy_loads(self) -> None:
        policy = GatePolicy.load()
        assert policy.thresholds.leakage_correlation == 0.95
        assert policy.thresholds.self_consistency_agreement == 1.0
        assert policy.stage("problem_discovery").risk_class is RiskClass.CRITICAL
        assert policy.stage("validation_strategy").risk_class is RiskClass.CRITICAL

    def test_unknown_stage_defaults_to_medium_risk(self) -> None:
        """Unknown stages must not silently default to the most permissive setting."""
        assert GatePolicy.load().stage("never_declared").risk_class is RiskClass.MEDIUM

    def test_thresholds_are_configurable(self) -> None:
        policy = GatePolicy.from_dict({"thresholds": {"leakage_correlation": 0.5}})
        decision = _decide(signals=QualitySignals(max_target_correlation=0.6), policy=policy)
        assert decision.reason_code == "leakage_detected"

    def test_declared_mandatory_criteria_are_enforced(self) -> None:
        policy = GatePolicy.load()
        stage = policy.stage("eda")
        critique = CritiqueResult(
            stage_id="eda",
            rubric_version="eda.v1",
            unmet_criteria=["eda.all_features_covered"],
        )
        decision = _decide(stage=stage, critique=critique, profile=FULL_AUTO)
        assert decision.verdict is GateVerdict.RETRY


class TestRealWorldScenarios:
    """Scenarios taken from actual pipeline runs on the sample data."""

    def test_problem_discovery_gate_stops_for_the_human(self) -> None:
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("problem_discovery"),
            signals=QualitySignals(self_consistency_agreement=1.0),
            profile=CHECKPOINTED,
            policy=policy,
            context_summary="3 viable candidates proposed.",
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.human_prompt is not None

    def test_schema_discovery_auto_proceeds_when_consistent(self) -> None:
        """5/5 identical runs measured on the real data -> no ambiguity."""
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("schema_discovery"),
            signals=QualitySignals(self_consistency_agreement=1.0, validation_failures=0),
            profile=CHECKPOINTED,
            policy=policy,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_total_comp_ytd_leakage_is_caught(self) -> None:
        """The planted trap: 0.98 correlation with the target."""
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("leakage_audit"),
            signals=QualitySignals(
                max_target_correlation=0.98,
                leakage_suspect_columns=["total_comp_ytd"],
            ),
            profile=FULL_AUTO,
            policy=GatePolicy.load(),
        )
        # max_attempts is 1 for leakage_audit, so this escalates immediately.
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.human_prompt is not None

    def test_rare_fraud_label_blocks_on_support(self) -> None:
        """39 positives at 0.26% -> statistically unsupported, not a modelling problem."""
        decision = evaluate_gate(
            stage=_stage(id="model_selection"),
            signals=QualitySignals(minority_class_count=39, n_rows=15_000),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "statistical_support_low"


@pytest.mark.parametrize("profile_name", sorted(BUILTIN_PROFILES))
def test_every_profile_stops_for_leakage(profile_name: str) -> None:
    """Parametrised because this must hold for every profile that ever exists."""
    profile: AutonomyProfile = BUILTIN_PROFILES[profile_name]
    decision = evaluate_gate(
        stage=_stage(max_attempts=1),
        signals=QualitySignals(max_target_correlation=0.99),
        profile=profile,
        history=StageHistory(attempts=1),
    )
    assert decision.verdict is not GateVerdict.AUTO_PROCEED, (
        f"profile {profile_name!r} allowed a leaking model through"
    )


class TestLiftWithinNoise:
    """Absolute deltas cannot serve metrics whose scales differ by orders of magnitude.

    `model_below_baseline` catches zero or negative lift. It cannot catch lift of
    +1 RMSE on a target measured in tens of thousands, because the threshold is
    absolute. Measuring lift in units of CV standard deviation is scale-free.
    """

    def test_trivial_lift_is_escalated(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                best_score=-87_075.0,
                naive_baseline_score=-87_076.0,
                cv_mean=-87_000.0,
                cv_std=3_259.0,
            ),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "lift_within_noise"

    def test_real_lift_passes(self) -> None:
        """Measured on the sample data: lift 61,178 against a CV spread of 604."""
        decision = _decide(
            signals=QualitySignals(
                best_score=-25_898.0,
                naive_baseline_score=-87_076.0,
                cv_mean=-25_258.0,
                cv_std=604.0,
            ),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_scale_free_across_metrics(self) -> None:
        """The same lift-to-noise ratio must decide identically on a bounded metric."""
        auc = _decide(
            signals=QualitySignals(
                best_score=0.505, naive_baseline_score=0.500, cv_mean=0.5, cv_std=0.04
            ),
            profile=CHECKPOINTED,
        )
        assert auc.reason_code == "lift_within_noise"

    def test_defers_to_model_below_baseline_when_no_lift(self) -> None:
        """The more specific rule should own the zero-lift case."""
        decision = _decide(
            signals=QualitySignals(
                best_score=-90_308.0,
                naive_baseline_score=-90_308.0,
                cv_mean=-87_068.0,
                cv_std=3_259.0,
            ),
            profile=CHECKPOINTED,
        )
        assert decision.reason_code == "model_below_baseline"

    def test_absent_cv_std_does_not_fire(self) -> None:
        decision = _decide(
            signals=QualitySignals(best_score=0.9, naive_baseline_score=0.5),
            profile=CHECKPOINTED,
        )
        assert "lift_within_noise" not in decision.triggered_rules

    def test_zero_cv_std_does_not_divide_by_zero(self) -> None:
        decision = _decide(
            signals=QualitySignals(
                best_score=0.9, naive_baseline_score=0.5, cv_mean=0.9, cv_std=0.0
            ),
            profile=CHECKPOINTED,
        )
        assert "lift_within_noise" not in decision.triggered_rules


class TestRetryBudgetRequiresAFailure:
    """A used-up budget only matters if the attempt actually failed.

    Found while testing the separator rule: `leakage_audit` declares
    max_attempts: 1, so `attempts >= max_attempts` was true on the very first
    run and every single-attempt stage escalated unconditionally — including a
    completely clean leakage audit.
    """

    def test_clean_last_attempt_proceeds(self) -> None:
        decision = _decide(stage=_stage(max_attempts=1), history=StageHistory(attempts=1))
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_failed_last_attempt_still_escalates(self) -> None:
        critique = CritiqueResult(
            stage_id="eda", rubric_version="eda.v1", unmet_criteria=["eda.covered"]
        )
        decision = _decide(
            stage=_stage(max_attempts=1, mandatory_criteria=frozenset({"eda.covered"})),
            critique=critique,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "retry_budget_exhausted"

    def test_validation_failures_count_as_a_failure(self) -> None:
        decision = _decide(
            stage=_stage(max_attempts=2),
            signals=QualitySignals(validation_failures=5),
            history=StageHistory(attempts=2),
        )
        assert decision.verdict is GateVerdict.ESCALATE

    def test_tolerated_validation_failures_are_not_a_failure(self) -> None:
        """Failure is whatever the rules say it is, including their tolerances."""
        decision = _decide(
            stage=_stage(max_attempts=2),
            signals=QualitySignals(validation_failures=2),
            history=StageHistory(attempts=2),
            profile=FULL_AUTO,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_clean_leakage_audit_does_not_trip_the_budget_rule(self) -> None:
        """The concrete regression: a clean audit at a single-attempt stage.

        `leakage_audit` is HIGH risk, so `checkpointed` still escalates on risk
        class — that is intended. What must not happen is the budget rule firing
        and reporting exhaustion when nothing failed.
        """
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("leakage_audit"),
            signals=QualitySignals(max_target_correlation=0.10),
            profile=CHECKPOINTED,
            policy=policy,
            history=StageHistory(attempts=1),
        )
        assert "retry_budget_exhausted" not in decision.triggered_rules

    def test_clean_single_attempt_stage_proceeds_in_full_auto(self) -> None:
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("leakage_audit"),
            signals=QualitySignals(max_target_correlation=0.10),
            profile=FULL_AUTO,
            policy=policy,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED


class TestFailureDetectionIsDerived:
    """Codex FINDING 10: the failure predicate must not be a parallel list.

    The first fix for the single-attempt bug enumerated four failure families and
    was already missing below-baseline, degenerate splits, weak support and
    unstable scores — so a stage burning its last attempt on any of those
    proceeded silently. Failure is now derived from the rule outcomes themselves,
    over the full rule set, so a profile opting out of a signal cannot disable
    the hard budget rule.
    """

    @pytest.mark.parametrize(
        "signals",
        [
            QualitySignals(best_score=0.5, naive_baseline_score=0.9),
            QualitySignals(split_retained_rate=1.0, min_validation_fold_size=3),
            QualitySignals(minority_class_count=5),
            QualitySignals(cv_mean=0.8, cv_std=0.5),
            QualitySignals(self_consistency_agreement=0.2),
        ],
        ids=[
            "below_baseline",
            "degenerate_split",
            "weak_support",
            "unstable_cv",
            "disagreement",
        ],
    )
    def test_last_attempt_failure_stops_even_in_full_auto(self, signals) -> None:
        """full_auto opts out of *escalating* on these, not out of them mattering."""
        decision = _decide(
            stage=_stage(max_attempts=1),
            signals=signals,
            profile=FULL_AUTO,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "retry_budget_exhausted"

    def test_clean_last_attempt_still_proceeds(self) -> None:
        decision = _decide(
            stage=_stage(max_attempts=1),
            signals=QualitySignals(best_score=0.9, naive_baseline_score=0.5),
            profile=FULL_AUTO,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED

    def test_policy_checkpoints_are_not_failures(self) -> None:
        """A checkpoint means "a human should look", not "the artifact is bad"."""
        decision = _decide(
            stage=_stage(id="problem_discovery", risk_class=RiskClass.CRITICAL, max_attempts=1),
            profile=CHECKPOINTED,
            history=StageHistory(attempts=1),
        )
        assert "retry_budget_exhausted" not in decision.triggered_rules


class TestSeparatorIsNotFailure:
    """Codex FINDING 13: an open provenance question is not a failed attempt.

    A separator suspect is deliberately non-blocking, and no number of retries
    can establish whether a column is recorded before the outcome. Counting it as
    failure made the attempt boundary convert uncertainty into a hard budget
    stop — including for profiles that had opted out of asking about it.
    """

    def test_unconfirmed_separator_does_not_exhaust_the_budget(self) -> None:
        decision = _decide(
            stage=_stage(max_attempts=1),
            signals=QualitySignals(separator_suspect_columns=["years_experience"]),
            profile=FULL_AUTO,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED
        assert "retry_budget_exhausted" not in decision.triggered_rules

    def test_opted_in_profile_still_asks_for_confirmation(self) -> None:
        decision = _decide(
            stage=_stage(max_attempts=1),
            signals=QualitySignals(separator_suspect_columns=["years_experience"]),
            profile=CHECKPOINTED,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "separator_needs_confirmation"

    def test_a_real_failure_alongside_a_separator_still_stops(self) -> None:
        """The exemption must not launder an actual defect."""
        decision = _decide(
            stage=_stage(max_attempts=1),
            signals=QualitySignals(
                separator_suspect_columns=["x"],
                best_score=0.4,
                naive_baseline_score=0.9,
            ),
            profile=FULL_AUTO,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "retry_budget_exhausted"
