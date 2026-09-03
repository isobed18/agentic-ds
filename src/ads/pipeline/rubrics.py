"""Orchestrator-owned acceptance rubrics for pipeline stage outputs.

Stages emit artifacts and measurements.  This module decides whether those
facts satisfy the workflow's standard, keeping the judged code from also
owning its acceptance decision.
"""

from __future__ import annotations

from collections.abc import Callable

from ads.contracts.base import Artifact
from ads.contracts.eda import EDAReport
from ads.contracts.features import FeatureSpec
from ads.contracts.integration import (
    IntegrationPlan,
    IntegrationTrial,
    integration_plan_fingerprint,
)
from ads.contracts.problem import ProblemCandidateSet
from ads.contracts.reporting import EvaluationReport
from ads.contracts.training import TrainingReport
from ads.contracts.validation import (
    ValidationStrategy,
    ValidationTrial,
    validation_strategy_fingerprint,
)
from ads.orchestration import Criterion, CritiqueContext, Rubric, RubricRegistry
from ads.pipeline.stages import INTEGRATION_GRAIN_PRESERVED_KEY
from ads.store import compute_artifact_id


def _artifact_check[A: Artifact](
    model: type[A], predicate: Callable[[A], bool]
) -> Callable[[CritiqueContext], bool]:
    def check(context: CritiqueContext) -> bool:
        artifact = context.artifact_of(model)
        return artifact is not None and predicate(artifact)

    return check


def _blocked_candidate_reasons(context: CritiqueContext) -> list[str]:
    """Why each proposed framing was rejected, from the measurement itself.

    #427: `compute_support` exists to turn "this framing is not viable" into
    facts, and writes them to `ProblemSupport.blocking_reasons` on every
    candidate -- `too_many_classes: 'claim_description' has 97219 levels
    (limit 50)`, `task_target_mismatch: regression needs a numeric target`,
    `unknown_target: column 'X' is not present in the ABT`. Nothing surfaced
    them, so the panel showed a tautology while the actual explanation sat in
    the artifact. One line per rejected candidate, naming the framing it was.
    """
    candidates = context.artifact_of(ProblemCandidateSet)
    if candidates is None:
        return ["No problem candidates were produced at all."]
    lines: list[str] = []
    for candidate in candidates.candidates:
        if candidate.support.is_viable:
            continue
        framing = f"{candidate.task_type.value} on {candidate.target_column or 'no target'}"
        for reason in candidate.support.blocking_reasons:
            lines.append(f"{framing} — {reason}")
    return lines or ["The candidates carry no measured blocking reason."]


def _contract_validation_details(context: CritiqueContext) -> list[str]:
    """The deterministic validator failures the stage recorded.

    `_valid_contract` is `validation_failures == 0`, a count -- so a reader was
    told a contract check failed and never which validator fired or on what
    (#427). The failures name the layer, the code and the column.
    """
    signals = context.facts["signals"]
    details = list(getattr(signals, "validation_failure_details", None) or [])
    if details:
        return [str(item) for item in details]
    count = signals.validation_failures
    return [f"{count} deterministic validator failure(s), with no detail recorded."]


def _valid_contract(context: CritiqueContext) -> bool:
    signals = context.facts["signals"]
    return signals.validation_failures == 0


def _integration_grain_preserved(context: CritiqueContext) -> bool:
    state = context.facts["run_state"]
    return state.blackboard.get(INTEGRATION_GRAIN_PRESERVED_KEY) is True


def _plan_trial_passed(context: CritiqueContext) -> bool:
    plan = context.artifact_of(IntegrationPlan)
    trial = context.artifact_of(IntegrationTrial)
    if plan is None or trial is None or plan.trial_artifact_id is None:
        return False
    proposal = plan.to_proposal()
    return (
        trial.grain_preserved
        and compute_artifact_id(trial) == plan.trial_artifact_id
        and trial.plan_fingerprint == integration_plan_fingerprint(proposal)
    )


def build_pipeline_rubrics() -> RubricRegistry:
    """Return the versioned deterministic standards used by the pipeline."""
    registry = RubricRegistry()
    rubrics = (
        Rubric(
            stage_id="schema_discovery",
            version="v1",
            criteria=(
                Criterion(
                    id="schema.base_grain_declared",
                    description="The integration plan declares a non-empty base grain.",
                    failure="The plan declared no base grain.",
                    check=_artifact_check(IntegrationPlan, lambda plan: bool(plan.base_grain)),
                ),
                Criterion(
                    id="schema.fan_out_aggregated",
                    description=(
                        "The plan passed the deterministic relationship and fan-out validators."
                    ),
                    failure="The plan did not pass the relationship and fan-out validators.",
                    check=_valid_contract,
                ),
                Criterion(
                    id="schema.plan_trial_passed",
                    description=(
                        "The exact executable plan passed a deterministic DuckDB grain trial."
                    ),
                    failure="The plan failed a trial execution against the real tables.",
                    check=_plan_trial_passed,
                ),
            ),
        ),
        Rubric(
            stage_id="integration",
            version="v1",
            criteria=(
                Criterion(
                    id="integration.grain_preserved",
                    description="The executed ABT has exactly one row per base-grain row.",
                    failure=(
                        "The executed table has more than one row per base-grain row, so the "
                        "join fanned out."
                    ),
                    check=_integration_grain_preserved,
                ),
            ),
        ),
        Rubric(
            stage_id="problem_discovery",
            version="v1",
            criteria=(
                Criterion(
                    id="problem.at_least_one_viable_candidate",
                    description="At least one proposed problem has measured viable support.",
                    failure="None of the proposed ML problems is viable against this data.",
                    check=_artifact_check(
                        ProblemCandidateSet, lambda candidates: bool(candidates.viable())
                    ),
                    # #427: why each framing was rejected is measured by
                    # `compute_support` and written onto the candidate, and it
                    # used to stay there while the panel showed only the
                    # sentence above. These are the answer.
                    evidence=_blocked_candidate_reasons,
                ),
                Criterion(
                    id="problem.targets_exist",
                    description="All proposed targets passed deterministic schema validation.",
                    failure=(
                        "A proposed problem named a column the analytical base table does not "
                        "have, or a task its target's shape contradicts."
                    ),
                    check=_valid_contract,
                    evidence=_contract_validation_details,
                ),
            ),
        ),
        Rubric(
            stage_id="validation_strategy",
            version="v1",
            criteria=(
                Criterion(
                    id="validation.strategy_matches_detected_signals",
                    description=(
                        "A strategy artifact exists and passed the measured-signal validators."
                    ),
                    failure=(
                        "The proposed split strategy contradicts the measured signals, or no "
                        "strategy was produced."
                    ),
                    evidence=_contract_validation_details,
                    check=lambda context: (
                        context.artifact_of(ValidationStrategy) is not None
                        and _valid_contract(context)
                    ),
                ),
                Criterion(
                    id="validation.strategy_trial_passed",
                    description="The exact selected strategy passed an executor-owned trial.",
                    failure="The selected split strategy failed its trial execution.",
                    check=lambda context: (
                        (strategy := context.artifact_of(ValidationStrategy)) is not None
                        and (trial := context.artifact_of(ValidationTrial)) is not None
                        and strategy.trial_artifact_id == compute_artifact_id(trial)
                        and trial.proposal_fingerprint
                        == validation_strategy_fingerprint(strategy.to_proposal())
                        and trial.passed
                    ),
                ),
            ),
        ),
        Rubric(
            stage_id="eda",
            version="v1",
            criteria=tuple(
                Criterion(
                    id=criterion_id,
                    description=description,
                    failure=failure,
                    check=_artifact_check(EDAReport, predicate),
                )
                for criterion_id, description, failure, predicate in (
                    (
                        "eda.target_distribution_reported",
                        "The configured target distribution is reported.",
                        "The analysis did not report the target's distribution.",
                        lambda report: report.target_distribution_reported,
                    ),
                    (
                        "eda.all_features_covered",
                        "Every feature has deterministic EDA coverage.",
                        "Some features were left out of the analysis.",
                        lambda report: report.all_features_covered,
                    ),
                    (
                        "eda.missingness_quantified",
                        "Missingness is quantified for every covered column.",
                        "Missing values were not quantified for every column analysed.",
                        lambda report: report.missingness_quantified,
                    ),
                )
            ),
        ),
        Rubric(
            stage_id="feature_pipeline",
            version="v1",
            criteria=(
                Criterion(
                    id="features.columns_accounted_for",
                    description="Every non-target input has exactly one feature route.",
                    failure="Some input columns have no feature route, or more than one.",
                    check=_artifact_check(
                        FeatureSpec,
                        lambda spec: (
                            len(spec.input_columns) - 1
                            == len(spec.numeric_columns)
                            + len(spec.categorical_columns)
                            + len(spec.datetime_columns)
                            + len(spec.dropped_columns)
                        ),
                    ),
                ),
                Criterion(
                    id="features.preprocessing_is_fold_local",
                    description="Learned preprocessing statistics are fitted per fold.",
                    failure=(
                        "Preprocessing statistics were learned outside the training fold, which "
                        "leaks the evaluation data."
                    ),
                    check=_artifact_check(
                        FeatureSpec,
                        lambda spec: (
                            spec.preprocessing_scope == "fit_per_training_fold"
                            and not spec.source_mutation_allowed
                        ),
                    ),
                ),
            ),
        ),
        Rubric(
            stage_id="training",
            version="v1",
            criteria=(
                Criterion(
                    id="features.pipeline_is_fitted_object",
                    description=(
                        "The selected sklearn pipeline was verified fitted before persistence."
                    ),
                    failure="The saved model was never verified as fitted.",
                    check=_artifact_check(
                        TrainingReport,
                        lambda report: report.fitted_pipeline_verified,
                    ),
                ),
                Criterion(
                    id="features.no_test_fold_statistics",
                    description=(
                        "Preprocessing and estimator fitting used outer-training rows only."
                    ),
                    failure=(
                        "Fitting touched holdout rows, so the reported scores are optimistic."
                    ),
                    check=_artifact_check(
                        TrainingReport,
                        lambda report: (
                            report.fit_scope == "outer_train_only"
                            and report.holdout_rows_used_for_fit == 0
                            and report.inner_fold_fit_count is not None
                        ),
                    ),
                ),
                Criterion(
                    id="model_selection.baseline_included",
                    description="Model selection included exactly one mandatory naive baseline.",
                    failure=(
                        "Model selection ran without exactly one naive baseline to compare "
                        "against."
                    ),
                    check=_artifact_check(
                        TrainingReport,
                        lambda report: (
                            len([result for result in report.results if result.is_baseline]) == 1
                        ),
                    ),
                ),
            ),
        ),
        Rubric(
            stage_id="evaluation",
            version="v1",
            criteria=(
                Criterion(
                    id="evaluation.holdout_metrics_reported",
                    description="At least one untouched-holdout metric is reported.",
                    failure="No metric was reported on the untouched holdout.",
                    check=_artifact_check(
                        EvaluationReport, lambda report: bool(report.holdout_metrics)
                    ),
                ),
                Criterion(
                    id="evaluation.baseline_comparison_present",
                    description="The candidate comparison includes exactly one naive baseline.",
                    failure="The comparison has no naive baseline in it.",
                    check=_artifact_check(
                        EvaluationReport,
                        lambda report: any(
                            comparison.is_baseline for comparison in report.candidate_comparisons
                        ),
                    ),
                ),
            ),
        ),
    )
    for rubric in rubrics:
        registry.register(rubric)
    return registry


__all__ = ["build_pipeline_rubrics"]
