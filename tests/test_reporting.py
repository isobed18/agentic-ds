"""Evaluation artifact and conservative Markdown report tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.preprocessing import StandardScaler

from ads.contracts.base import ArtifactType
from ads.contracts.gates import GateDecision, GateVerdict
from ads.contracts.leakage import LeakageFinding, LeakageKind, LeakageReport
from ads.contracts.problem import Metric, ProblemDefinition, TaskType
from ads.contracts.reporting import DecisionAuthority, DecisionRecord
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.reporting import build_evaluation_report, render_markdown
from ads.store import ArtifactStore
from ads.training import default_candidates, train_candidates


@pytest.fixture(scope="module")
def reporting_inputs():
    rng = np.random.default_rng(441)
    x = rng.normal(size=220)
    frame = pd.DataFrame(
        {
            "x": x,
            "leaked_answer": 3.5 * x,
            "target": 3.5 * x + rng.normal(scale=0.05, size=len(x)),
        }
    )
    frame.loc[frame.index[::20], "target"] = np.nan
    strategy = ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        n_folds=3,
        test_size=0.2,
        rationale="Rows represent independent entities; seeded random validation is appropriate.",
    )
    training = train_candidates(
        frame.drop(columns=["leaked_answer"]),
        strategy,
        StandardScaler,
        default_candidates(TaskType.REGRESSION)[:2],
        target_column="target",
        task_type=TaskType.REGRESSION,
    )
    leakage = LeakageReport(
        target_column="target",
        n_features_checked=2,
        split_strategy=SplitStrategy.RANDOM,
        findings=[
            LeakageFinding(
                column="leaked_answer",
                kind=LeakageKind.TARGET_CORRELATION,
                score=0.999,
                threshold=0.95,
                blocking=True,
                detail="A direct target-derived feature.",
                suggested_action="Drop 'leaked_answer' from the feature set.",
            )
        ],
    )
    problem = ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="target",
        primary_metric=Metric.RMSE,
        title="Estimate the synthetic outcome",
        description=(
            "Estimate the continuous outcome from information available at prediction time."
        ),
        excluded_columns=["leaked_answer"],
        confirmed_by="human",
    )
    return training, leakage, strategy, problem


def test_build_evaluation_report_carries_measured_comparison_and_clearance(
    reporting_inputs,
) -> None:
    training, leakage, strategy, problem = reporting_inputs

    report = build_evaluation_report(training, leakage, strategy, problem)

    assert report.artifact_type is ArtifactType.EVALUATION_REPORT
    assert len(report.candidate_comparisons) == len(training.results)
    assert report.winner_id == training.winner_id
    assert report.winner_holdout_score == training.winner.evaluation_for(Metric.RMSE).holdout_score
    assert report.baseline_delta == pytest.approx(training.to_quality_signals().baseline_delta)
    assert report.target_null_rows_dropped == 11
    assert [item.column for item in report.cleared_leakage] == ["leaked_answer"]
    assert not report.unresolved_blocking_leakage
    assert report.persisted_model_matches_measured_model is None
    authorities = {item.stage: item.authority for item in report.decisions}
    assert authorities["problem_definition"] is DecisionAuthority.HUMAN
    assert authorities["model_selection"] is DecisionAuthority.AUTONOMOUS


def test_resolved_retry_keeps_prior_leakage_finding_and_human_authority(
    reporting_inputs,
) -> None:
    training, prior_leakage, strategy, problem = reporting_inputs
    clean = prior_leakage.model_copy(update={"n_features_checked": 1, "findings": []})
    report = build_evaluation_report(
        training,
        clean,
        strategy,
        problem,
        prior_leakage_reports=[prior_leakage],
        decisions=[
            DecisionRecord(
                stage="leakage_audit",
                decision="Human chose retry with a feature-drop correction.",
                authority=DecisionAuthority.HUMAN,
            )
        ],
        gate_decisions=[
            GateDecision(
                stage_id="leakage_audit",
                attempt=1,
                verdict=GateVerdict.ESCALATE,
                reason_code="leakage_unresolved",
            ),
            GateDecision(
                stage_id="leakage_audit",
                attempt=2,
                verdict=GateVerdict.AUTO_PROCEED,
                reason_code="no_rule_triggered",
            ),
        ],
    )

    assert [item.column for item in report.cleared_leakage] == ["leaked_answer"]
    assert report.gate_history[0].authority is DecisionAuthority.HUMAN
    assert "No audited leakage finding was recorded" not in render_markdown(report)


def test_evaluation_report_round_trips_through_artifact_store(reporting_inputs, tmp_path) -> None:
    training, leakage, strategy, problem = reporting_inputs
    report = build_evaluation_report(training, leakage, strategy, problem)
    store = ArtifactStore(tmp_path / "artifacts")

    reference = store.put(report, run_id="reporting-run")
    loaded = store.load(reference.artifact_id)

    assert loaded == report


def test_markdown_is_plain_about_baseline_validation_leakage_and_authority(
    reporting_inputs,
) -> None:
    training, leakage, strategy, problem = reporting_inputs
    report = build_evaluation_report(
        training,
        leakage,
        strategy,
        problem,
        decisions=[
            DecisionRecord(
                stage="validation_strategy",
                decision="Approved seeded random validation.",
                authority=DecisionAuthority.HUMAN,
            )
        ],
    )

    markdown = render_markdown(report)

    assert "Performance against the baseline" in markdown
    assert "naive baseline" in markdown
    assert "random" in markdown
    assert strategy.rationale in markdown
    assert "`leaked_answer`" in markdown
    assert "Human-approved" in markdown
    assert "Autonomous" in markdown
    assert "No persisted model blob" in markdown


@pytest.mark.parametrize("reason_code", ["lift_within_noise", "degenerate_split"])
def test_gate_escalations_are_prominent(reason_code: str, reporting_inputs) -> None:
    training, leakage, strategy, problem = reporting_inputs
    gate = GateDecision(
        stage_id="evaluation",
        attempt=1,
        verdict=GateVerdict.ESCALATE,
        reason_code=reason_code,
        triggered_rules=[reason_code],
    )

    report = build_evaluation_report(
        training,
        leakage,
        strategy,
        problem,
        gate_decisions=[gate],
    )
    markdown = render_markdown(report)

    assert report.prominent_alerts[0].reason_code == reason_code
    assert markdown.index(reason_code) < markdown.index("Performance against the baseline")
    assert "not clear to ship" in markdown


def test_stored_gate_history_cannot_render_as_a_clean_run(reporting_inputs, tmp_path) -> None:
    training, leakage, strategy, problem = reporting_inputs
    store = ArtifactStore(tmp_path / "artifacts")
    run_id = "escalated-run"
    stored = [
        GateDecision(
            stage_id="validation_strategy",
            attempt=1,
            verdict=GateVerdict.ESCALATE,
            reason_code="degenerate_split",
            triggered_rules=["degenerate_split", "risk_class_gate"],
        ),
        GateDecision(
            stage_id="feature_pipeline",
            attempt=1,
            verdict=GateVerdict.ESCALATE,
            reason_code="separator_needs_confirmation",
            triggered_rules=["separator_needs_confirmation"],
        ),
        GateDecision(
            stage_id="model_selection",
            attempt=1,
            verdict=GateVerdict.AUTO_PROCEED,
            reason_code="no_rule_triggered",
            triggered_rules=[],
        ),
    ]
    for decision in stored:
        store.put(decision, run_id=run_id)

    report = build_evaluation_report(
        training,
        leakage,
        strategy,
        problem,
        decisions=[
            DecisionRecord(
                stage="validation_strategy",
                decision="Approved the reduced validation population.",
                authority=DecisionAuthority.HUMAN,
            )
        ],
        artifact_store=store,
        run_id=run_id,
    )
    markdown = render_markdown(report)
    metrics_table = markdown.index("| Candidate |")

    assert [item.stage_id for item in report.gate_history] == [
        "validation_strategy",
        "feature_pipeline",
        "model_selection",
    ]
    assert markdown.index("Decisions and escalations") < metrics_table
    assert markdown.index("This evaluation is not clear to ship") < metrics_table
    assert markdown.index("Headline metric caveat") < metrics_table
    assert "`degenerate_split`" in markdown[:metrics_table]
    assert "`risk_class_gate`" in markdown[:metrics_table]
    assert "`separator_needs_confirmation`" in markdown[:metrics_table]
    assert "human-approved" in markdown[:metrics_table]
    assert "human approval not recorded" in markdown[:metrics_table]
    assert "autonomous" in markdown[:metrics_table]


def test_unconfirmed_separator_is_prominent_without_a_gate_projection(
    reporting_inputs,
) -> None:
    training, leakage, strategy, problem = reporting_inputs
    separator = LeakageFinding(
        column="pre_outcome_score",
        kind=LeakageKind.PERFECT_SEPARATOR,
        score=1.0,
        threshold=0.99,
        blocking=False,
        detail="The feature perfectly separates the target.",
        suggested_action="Confirm pre-outcome availability.",
    )
    leakage_with_separator = leakage.model_copy(update={"findings": [*leakage.findings, separator]})

    report = build_evaluation_report(training, leakage_with_separator, strategy, problem)
    markdown = render_markdown(report)

    assert [item.column for item in report.unresolved_separator_confirmation] == [
        "pre_outcome_score"
    ]
    assert markdown.index("Separator provenance remains unconfirmed") < markdown.index(
        "| Candidate |"
    )
    assert "not leakage-clean" in markdown


def test_unresolved_blocking_leakage_is_not_described_as_cleared(reporting_inputs) -> None:
    training, leakage, strategy, problem = reporting_inputs
    unresolved_problem = problem.model_copy(update={"excluded_columns": []})

    report = build_evaluation_report(training, leakage, strategy, unresolved_problem)
    markdown = render_markdown(report)

    assert report.unresolved_blocking_leakage
    assert "Blocking leakage remains unresolved" in markdown
    assert "not clear to ship" in markdown
