"""Orchestrator-owned acceptance rubrics for pipeline stage outputs.

Stages emit artifacts and measurements.  This module decides whether those
facts satisfy the workflow's standard, keeping the judged code from also
owning its acceptance decision.
"""

from __future__ import annotations

from collections.abc import Callable

from ads.contracts.base import Artifact
from ads.contracts.eda import EDAReport
from ads.contracts.integration import IntegrationPlan
from ads.contracts.problem import ProblemCandidateSet
from ads.contracts.reporting import EvaluationReport
from ads.contracts.validation import ValidationStrategy
from ads.orchestration import Criterion, CritiqueContext, Rubric, RubricRegistry
from ads.pipeline.stages import INTEGRATION_GRAIN_PRESERVED_KEY


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
                            comparison.is_baseline
                            for comparison in report.candidate_comparisons
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
