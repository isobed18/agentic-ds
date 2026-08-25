"""LLM-free acceptance coverage for the deterministic ADS pipeline."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ads.contracts import (
    IntegrationPlan,
    IntegrationPlanProposal,
    Metric,
    ProblemCandidate,
    ProblemCandidateProposal,
    ProblemCandidateSet,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
    ValidationStrategyProposal,
)
from ads.contracts.gates import BUILTIN_PROFILES, GateDecision, GateVerdict, QualitySignals
from ads.contracts.leakage import LeakageReport
from ads.contracts.training import TrainingReport
from ads.discovery import (
    audit_leakage,
    compute_support,
    detect_validation_signals,
)
from ads.ds_toolkit import build_preprocessor
from ads.eda import profile_for_eda
from ads.gates import GatePolicy, StageHistory, evaluate_gate
from ads.intake import (
    LoadedTable,
    detect_relationships,
    load_directory,
    profile_table,
    profile_tables,
)
from ads.integration import execute_plan
from ads.reporting import build_evaluation_report, render_markdown
from ads.splitting import SplitDiagnostics, describe_split
from ads.store import ArtifactStore
from ads.training import default_candidates, export_training_script, load_model, train_candidates


@dataclass(frozen=True)
class AcceptanceRun:
    abt: pd.DataFrame
    plan: IntegrationPlan
    problem: ProblemDefinition
    problem_candidates: ProblemCandidateSet
    strategy: ValidationStrategy
    diagnostics: SplitDiagnostics
    leakage_report: LeakageReport
    training_report: TrainingReport
    healthy_gate: GateDecision
    persisted_predictions: np.ndarray
    reloaded_predictions: np.ndarray
    exported_holdout_score: float
    markdown: str


def _subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, existing]))
    return environment


@pytest.fixture(scope="module")
def acceptance_run(sample_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> AcceptanceRun:
    """Run every deterministic stage once; agent-authored judgments are fixed contracts."""
    loaded_tables = load_directory(sample_dir)
    frames = {table.name: table.frame for table in loaded_tables}
    source_cards = profile_tables(loaded_tables)

    plan_proposal = IntegrationPlanProposal.model_validate(
        {
            "base_table": "physicians__physician_master",
            "base_grain": ["physician_id"],
            "grain_description": "One row per physician.",
            "joins": [
                {
                    "left_table": "physicians__physician_master",
                    "right_table": "physicians__compensation",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "Attach one-to-one compensation outcomes.",
                }
            ],
        }
    )
    plan = IntegrationPlan.from_proposal(
        plan_proposal,
        detect_relationships(source_cards, frames),
    )
    integration = execute_plan(plan, frames)
    abt = integration.frame
    abt_card = profile_table(
        LoadedTable(name="abt", frame=abt, source_uri="derived", source_format="duckdb")
    )

    problem_proposal = ProblemCandidateProposal(
        title="Predict annual physician compensation",
        title_tr="Yıllık hekim ücretini tahmin et",
        task_type=TaskType.REGRESSION,
        target_column="annual_comp",
        business_rationale="Estimate compensation from pre-outcome physician attributes.",
        business_rationale_tr="Sonuç öncesi hekim özelliklerinden ücreti tahmin et.",
        evidence_columns=["years_experience", "specialty", "city", "hire_date"],
        primary_metric=Metric.RMSE,
    )
    support = compute_support(
        abt_card,
        abt,
        target_column="annual_comp",
        task_type=TaskType.REGRESSION,
        excluded_columns=frozenset({"total_comp_ytd"}),
    )
    candidate = ProblemCandidate.from_proposal(
        problem_proposal,
        support,
        candidate_id="annual_comp_regression",
    )
    problem_candidates = ProblemCandidateSet(candidates=[candidate])
    problem = ProblemDefinition(
        task_type=candidate.task_type,
        target_column=candidate.target_column,
        primary_metric=candidate.primary_metric,
        title=candidate.title,
        description=candidate.business_rationale,
        excluded_columns=["total_comp_ytd"],
        confirmed_by="auto",
        source_candidate_id=candidate.candidate_id,
    )

    validation_signals = detect_validation_signals(
        abt_card,
        abt,
        target_column=problem.target_column,
        task_type=problem.task_type,
        n_folds=3,
    )
    strategy = ValidationStrategy.from_proposal(
        ValidationStrategyProposal(
            strategy=SplitStrategy.TEMPORAL,
            n_folds=3,
            test_size=0.2,
            time_column="hire_date",
            holdout_cutoff="2019-01-01",
            rationale=(
                "Hiring date defines a temporal deployment boundary; later hires form "
                "the untouched holdout."
            ),
            rationale_tr=(
                "İşe giriş tarihi zamansal dağıtım sınırını tanımlar; sonraki işe alımlar "
                "dokunulmamış test kümesini oluşturur."
            ),
        ),
        validation_signals,
    )

    leakage_report = audit_leakage(
        abt_card,
        abt,
        target_column=problem.target_column,
        task_type=problem.task_type,
        validation_strategy=strategy,
        integration_plan=plan_proposal,
    )
    post_exclusion_audit = audit_leakage(
        abt_card,
        abt,
        target_column=problem.target_column,
        task_type=problem.task_type,
        validation_strategy=strategy,
        integration_plan=plan_proposal,
        excluded_columns=frozenset(problem.excluded_columns),
    )

    labeled = abt.loc[abt[problem.target_column].notna()]
    diagnostics = describe_split(labeled, strategy)
    run_root = tmp_path_factory.mktemp("acceptance")
    store_root = run_root / "artifacts"
    store = ArtifactStore(store_root)
    training_report = train_candidates(
        abt,
        strategy,
        lambda: build_preprocessor(
            abt_card,
            target_column=problem.target_column,
            excluded_columns=problem.excluded_columns,
        ),
        default_candidates(problem.task_type)[:2],
        target_column=problem.target_column,
        task_type=problem.task_type,
        store=store,
        run_id="acceptance-run",
    )
    training_reference = store.put(
        training_report,
        run_id="acceptance-run",
        name="training-report",
    )
    stored_report = store.load(training_reference.artifact_id, type(training_report))
    assert stored_report == training_report
    assert training_report.model_blob is not None

    prediction_rows = labeled.drop(columns=[problem.target_column]).iloc[:25]
    persisted_model = load_model(store, training_report.model_blob.artifact_id)
    persisted_predictions = persisted_model.predict(prediction_rows)
    reloaded_model = load_model(
        ArtifactStore(store_root),
        training_report.model_blob.artifact_id,
    )
    reloaded_predictions = reloaded_model.predict(prediction_rows)

    quality_data = training_report.to_quality_signals().model_dump()
    quality_data.update(post_exclusion_audit.to_quality_signals().model_dump(exclude_none=True))
    quality_data.update(diagnostics.to_quality_signals().model_dump(exclude_none=True))
    policy = GatePolicy.load()
    healthy_gate = evaluate_gate(
        stage=policy.stage("training"),
        signals=QualitySignals.model_validate(quality_data),
        profile=BUILTIN_PROFILES["autonomous_with_guardrails"],
        policy=policy,
    )

    script_path = run_root / "train.py"
    script_path.write_text(
        export_training_script(
            training_report,
            strategy,
            plan,
            target_column=problem.target_column,
            task_type=problem.task_type,
            source_cards=source_cards,
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )
    assert completed.returncode == 0, completed.stderr
    metric_match = re.search(
        r"^rmse: .* holdout=([-+0-9.eE]+)$",
        completed.stdout,
        re.MULTILINE,
    )
    assert metric_match, completed.stdout

    eda_report = profile_for_eda(abt_card, abt, problem)
    assert all(eda_report.criterion_results().values())
    evaluation_report = build_evaluation_report(
        training_report,
        leakage_report,
        strategy,
        problem,
        gate_decisions=[healthy_gate],
    )
    markdown = render_markdown(evaluation_report)

    return AcceptanceRun(
        abt=abt,
        plan=plan,
        problem=problem,
        problem_candidates=problem_candidates,
        strategy=strategy,
        diagnostics=diagnostics,
        leakage_report=leakage_report,
        training_report=training_report,
        healthy_gate=healthy_gate,
        persisted_predictions=np.asarray(persisted_predictions),
        reloaded_predictions=np.asarray(reloaded_predictions),
        exported_holdout_score=float(metric_match.group(1)),
        markdown=markdown,
    )


def test_deterministic_pipeline_acceptance(acceptance_run: AcceptanceRun) -> None:
    run = acceptance_run
    base = run.abt[run.plan.base_grain]
    assert len(run.abt) == 800
    assert not base.duplicated().any()
    assert run.problem_candidates.viable()

    assert "total_comp_ytd" in run.leakage_report.suspect_columns
    assert "total_comp_ytd" in run.problem.excluded_columns

    policy = GatePolicy.load()
    assert run.diagnostics.retained_rate >= policy.thresholds.min_split_retained_rate
    assert run.diagnostics.min_validation_fold_size is not None
    assert run.diagnostics.min_validation_fold_size >= policy.thresholds.min_validation_fold_size

    training = run.training_report
    winner = training.winner.evaluation_for(training.primary_metric)
    baseline = training.baseline.evaluation_for(training.primary_metric)
    assert winner.holdout_score < baseline.holdout_score
    assert run.healthy_gate.verdict is GateVerdict.AUTO_PROCEED

    np.testing.assert_array_equal(run.reloaded_predictions, run.persisted_predictions)
    assert run.exported_holdout_score == pytest.approx(
        winner.holdout_score,
        rel=1e-9,
        abs=1e-12,
    )

    assert training.winner.display_name in run.markdown
    assert training.baseline.display_name in run.markdown
    assert "Performance against the baseline" in run.markdown
    assert run.strategy.strategy.value in run.markdown
    assert "`total_comp_ytd`" in run.markdown
    assert "cleared by exclusion" in run.markdown


def test_reintroducing_leak_escalates(acceptance_run: AcceptanceRun) -> None:
    run = acceptance_run
    unsafe_problem = run.problem.model_copy(update={"excluded_columns": []})
    assert "total_comp_ytd" not in unsafe_problem.excluded_columns

    policy = GatePolicy.load()
    stage = policy.stage("feature_pipeline")
    decision = evaluate_gate(
        stage=stage,
        signals=run.leakage_report.to_quality_signals(),
        profile=BUILTIN_PROFILES["full_auto"],
        history=StageHistory(attempts=stage.max_attempts),
        policy=policy,
    )

    assert decision.verdict is GateVerdict.ESCALATE
    assert decision.reason_code == "leakage_unresolved"
    assert "leakage_detected" in decision.triggered_rules
