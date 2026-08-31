"""Build the deterministic evaluation artifact from completed stage artifacts."""

from __future__ import annotations

from collections.abc import Sequence

from ads.contracts.base import ArtifactType
from ads.contracts.gates import GateDecision, GateVerdict
from ads.contracts.leakage import LeakageReport
from ads.contracts.problem import Metric, ProblemDefinition
from ads.contracts.reporting import (
    CandidateComparison,
    DecisionAuthority,
    DecisionRecord,
    EvaluationAlert,
    EvaluationReport,
    GateHistoryRecord,
    HoldoutMetric,
    LeakageDisposition,
)
from ads.contracts.training import TrainingReport
from ads.contracts.validation import ValidationStrategy
from ads.store import ArtifactStore

_LOWER_IS_BETTER = frozenset({Metric.RMSE, Metric.MAE, Metric.MAPE})
_PROMINENT_GATE_CODES = frozenset(
    {"lift_within_noise", "degenerate_split", "separator_needs_confirmation"}
)


def load_gate_decisions(store: ArtifactStore, run_id: str) -> list[GateDecision]:
    """Load a run's persisted gate history in chronological insertion order."""
    refs = store.list(run_id, artifact_type=ArtifactType.GATE_DECISION)
    return [store.load(reference.artifact_id, GateDecision) for reference in reversed(refs)]


def _gate_alerts(decisions: Sequence[GateDecision]) -> list[EvaluationAlert]:
    alerts: list[EvaluationAlert] = []
    for decision in decisions:
        fired = list(dict.fromkeys([decision.reason_code, *decision.triggered_rules]))
        for reason_code in fired:
            if (
                decision.verdict is GateVerdict.AUTO_PROCEED
                and reason_code not in _PROMINENT_GATE_CODES
            ):
                continue
            if reason_code == "lift_within_noise":
                detail = (
                    "Measured improvement over the baseline was not large relative to "
                    "cross-validation variation. Treat the claimed lift as uncertain."
                )
            elif reason_code == "degenerate_split":
                detail = (
                    "The validation split retained too little usable evaluation data. "
                    "Its metrics may be too noisy for a shipping decision."
                )
            elif reason_code == "separator_needs_confirmation":
                detail = (
                    "A feature nearly determines the outcome, but its pre-outcome "
                    "availability has not been confirmed. Do not treat this run as "
                    "leakage-clean until provenance is resolved."
                )
            else:
                detail = (
                    f"Gate {decision.stage_id!r} returned {decision.verdict.value!r} "
                    f"for {reason_code!r}."
                )
            alerts.append(
                EvaluationAlert(
                    stage_id=decision.stage_id,
                    reason_code=reason_code,
                    verdict=decision.verdict,
                    detail=detail,
                )
            )
    return alerts


def build_evaluation_report(
    training_report: TrainingReport,
    leakage_report: LeakageReport,
    strategy: ValidationStrategy,
    problem: ProblemDefinition,
    *,
    decisions: Sequence[DecisionRecord] = (),
    gate_decisions: Sequence[GateDecision] = (),
    prior_leakage_reports: Sequence[LeakageReport] = (),
    artifact_store: ArtifactStore | None = None,
    run_id: str | None = None,
) -> EvaluationReport:
    """Combine measured artifacts without recomputing or embellishing their results."""
    if (artifact_store is None) != (run_id is None):
        raise ValueError("artifact_store and run_id must be supplied together.")
    if artifact_store is not None and gate_decisions:
        raise ValueError("Pass stored gate history or gate_decisions, not both.")
    if artifact_store is not None and run_id is not None:
        gate_decisions = load_gate_decisions(artifact_store, run_id)

    if training_report.task_type is not problem.task_type:
        raise ValueError("TrainingReport task_type does not match ProblemDefinition.")
    if training_report.primary_metric is not problem.primary_metric:
        raise ValueError("TrainingReport primary_metric does not match ProblemDefinition.")
    if (
        leakage_report.target_column is not None
        and leakage_report.target_column != problem.target_column
    ):
        raise ValueError("LeakageReport target_column does not match ProblemDefinition.")
    if (
        leakage_report.split_strategy is not None
        and leakage_report.split_strategy is not strategy.strategy
    ):
        raise ValueError("LeakageReport split_strategy does not match ValidationStrategy.")

    primary = training_report.primary_metric
    comparisons = [
        CandidateComparison(
            candidate_id=result.candidate_id,
            display_name=result.display_name,
            is_baseline=result.is_baseline,
            selected=result.candidate_id == training_report.winner_id,
            cv_mean=result.evaluation_for(primary).cv_mean,
            cv_std=result.evaluation_for(primary).cv_std,
            holdout_score=result.evaluation_for(primary).holdout_score,
        )
        for result in training_report.results
    ]
    winner_primary = training_report.winner.evaluation_for(primary)
    baseline_primary = training_report.baseline.evaluation_for(primary)
    baseline_delta = training_report.to_quality_signals().baseline_delta
    if baseline_delta is None:  # pragma: no cover - a valid report always supplies both scores
        raise ValueError("TrainingReport cannot compute a baseline delta.")
    row_counts = (
        training_report.input_row_count,
        training_report.target_null_rows_dropped,
        training_report.training_row_count,
    )
    if any(value is None for value in row_counts):
        raise ValueError("TrainingReport does not record schema-v3 training row counts.")
    input_row_count, target_null_rows_dropped, training_row_count = row_counts

    excluded = set(problem.excluded_columns)
    findings_by_key = {
        (finding.column, finding.kind): finding
        for report in [*prior_leakage_reports, leakage_report]
        for finding in report.findings
    }
    leakage_dispositions = [
        LeakageDisposition(
            column=finding.column,
            kind=finding.kind,
            score=finding.score,
            blocking=finding.blocking,
            cleared=finding.column in excluded,
            action=finding.suggested_action,
        )
        for finding in findings_by_key.values()
    ]
    authority = (
        DecisionAuthority.HUMAN if problem.confirmed_by == "human" else DecisionAuthority.AUTONOMOUS
    )
    default_decisions = [
        DecisionRecord(
            stage="problem_definition",
            decision=f"Selected {problem.title!r} as the modeling problem.",
            authority=authority,
            rationale=problem.description,
        ),
        DecisionRecord(
            stage="validation_strategy",
            decision=f"Used {strategy.strategy.value} validation with {strategy.n_folds} folds.",
            authority=DecisionAuthority.UNRECORDED,
            rationale=strategy.rationale,
        ),
        DecisionRecord(
            stage="model_selection",
            decision=(
                f"Selected {training_report.winner.display_name!r} by inner-CV {primary.value}."
            ),
            authority=DecisionAuthority.AUTONOMOUS,
            rationale="Candidate selection used recorded deterministic measurements.",
        ),
    ]
    supplied_stages = {item.stage for item in decisions}
    recorded_decisions = [
        item for item in default_decisions if item.stage not in supplied_stages
    ] + list(decisions)
    human_approved_stages = {
        item.stage for item in recorded_decisions if item.authority is DecisionAuthority.HUMAN
    }
    gate_history = [
        GateHistoryRecord(
            stage_id=decision.stage_id,
            attempt=decision.attempt,
            verdict=decision.verdict,
            reason_code=decision.reason_code,
            triggered_rules=decision.triggered_rules,
            authority=(
                DecisionAuthority.HUMAN
                if decision.stage_id in human_approved_stages
                else (
                    DecisionAuthority.UNRECORDED
                    if decision.verdict is GateVerdict.ESCALATE
                    else DecisionAuthority.AUTONOMOUS
                )
            ),
        )
        for decision in gate_decisions
    ]

    return EvaluationReport(
        problem_title=problem.title,
        problem_description=problem.description,
        # #200: carry the Turkish prose the problem was authored with so the
        # report reads in one language rather than an English title in an
        # otherwise Turkish document.
        problem_title_tr=problem.title_tr,
        problem_description_tr=problem.description_tr,
        task_type=problem.task_type,
        target_column=problem.target_column,
        primary_metric=primary,
        candidate_comparisons=comparisons,
        winner_id=training_report.winner_id,
        winner_display_name=training_report.winner.display_name,
        holdout_metrics=[
            HoldoutMetric(metric=item.metric, score=item.holdout_score)
            for item in training_report.winner.metrics
        ],
        baseline_holdout_score=baseline_primary.holdout_score,
        winner_holdout_score=winner_primary.holdout_score,
        baseline_delta=baseline_delta,
        lower_is_better=primary in _LOWER_IS_BETTER,
        input_row_count=input_row_count,
        target_null_rows_dropped=target_null_rows_dropped,
        training_row_count=training_row_count,
        validation_strategy=strategy.strategy,
        validation_n_folds=strategy.n_folds,
        validation_test_size=strategy.test_size,
        validation_rationale=strategy.rationale,
        validation_group_column=strategy.group_column,
        validation_time_column=strategy.time_column,
        validation_holdout_cutoff=strategy.holdout_cutoff,
        leakage_dispositions=leakage_dispositions,
        decisions=recorded_decisions,
        gate_history=gate_history,
        alerts=_gate_alerts(gate_decisions),
        persisted_model_matches_measured_model=(
            True if training_report.model_blob is not None else None
        ),
    )


__all__ = ["build_evaluation_report", "load_gate_decisions"]
