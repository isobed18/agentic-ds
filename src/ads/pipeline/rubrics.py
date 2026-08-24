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
                    check=_artifact_check(IntegrationPlan, lambda plan: bool(plan.base_grain)),
                ),
                Criterion(
                    id="schema.fan_out_aggregated",
                    description=(
                        "The plan passed the deterministic relationship and fan-out validators."
                    ),
                    check=_valid_contract,
                ),
                Criterion(
                    id="schema.plan_trial_passed",
                    description=(
                        "The exact executable plan passed a deterministic DuckDB grain trial."
                    ),
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
                    check=_artifact_check(
                        ProblemCandidateSet, lambda candidates: bool(candidates.viable())
                    ),
                ),
                Criterion(
                    id="problem.targets_exist",
                    description="All proposed targets passed deterministic schema validation.",
                    check=_valid_contract,
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
                    check=lambda context: (
                        context.artifact_of(ValidationStrategy) is not None
                        and _valid_contract(context)
                    ),
                ),
                Criterion(
                    id="validation.strategy_trial_passed",
                    description="The exact selected strategy passed an executor-owned trial.",
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
                    check=_artifact_check(EDAReport, predicate),
                )
                for criterion_id, description, predicate in (
                    (
                        "eda.target_distribution_reported",
                        "The configured target distribution is reported.",
                        lambda report: report.target_distribution_reported,
                    ),
                    (
                        "eda.all_features_covered",
                        "Every feature has deterministic EDA coverage.",
                        lambda report: report.all_features_covered,
                    ),
                    (
                        "eda.missingness_quantified",
                        "Missingness is quantified for every covered column.",
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
                    check=_artifact_check(
                        EvaluationReport, lambda report: bool(report.holdout_metrics)
                    ),
                ),
                Criterion(
                    id="evaluation.baseline_comparison_present",
                    description="The candidate comparison includes exactly one naive baseline.",
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
