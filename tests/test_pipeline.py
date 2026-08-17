"""Real-data coverage for the deterministic orchestration adapters."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ads.contracts import (
    IntegrationPlan,
    Metric,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)
from ads.contracts.base import ArtifactType
from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict
from ads.contracts.leakage import LeakageReport
from ads.contracts.problem import ProblemDefinition as StoredProblemDefinition
from ads.gates import GatePolicy
from ads.orchestration import RunState, linear_spec, run_workflow
from ads.pipeline import (
    DROPPED_FEATURES_KEY,
    FINAL_MARKDOWN_KEY,
    MODEL_FRAME_KEY,
    TRAINING_FRAME_COLUMNS_KEY,
    FinalReport,
    build_default_registry,
    build_default_spec,
    build_pipeline_rubrics,
    configure_pipeline_state,
)
from ads.store import ArtifactStore

FULL_AUTO = BUILTIN_PROFILES["full_auto"]


@pytest.fixture
def approved_inputs() -> tuple[IntegrationPlan, ValidationStrategy]:
    plan = IntegrationPlan.model_validate(
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
    strategy = ValidationStrategy(
        strategy=SplitStrategy.TEMPORAL,
        n_folds=3,
        test_size=0.2,
        time_column="hire_date",
        holdout_cutoff="2019-01-01",
        rationale="Later hires form the untouched deployment holdout.",
    )
    return plan, strategy


def _problem(*, exclude_leak: bool) -> ProblemDefinition:
    return ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="annual_comp",
        primary_metric=Metric.RMSE,
        title="Predict annual physician compensation",
        description="Estimate compensation from pre-outcome physician attributes.",
        excluded_columns=["total_comp_ytd"] if exclude_leak else [],
        confirmed_by="auto",
    )


def _state(
    tmp_path: Path,
    sample_dir: Path,
    approved_inputs: tuple[IntegrationPlan, ValidationStrategy],
    *,
    exclude_leak: bool,
    run_id: str,
) -> RunState:
    plan, strategy = approved_inputs
    state = RunState(
        run_id=run_id,
        store=ArtifactStore(tmp_path / run_id),
        profile=FULL_AUTO,
    )
    configure_pipeline_state(
        state,
        source_path=sample_dir,
        integration_plan=plan,
        problem=_problem(exclude_leak=exclude_leak),
        validation_strategy=strategy,
        candidate_limit=2,
    )
    return state


def test_default_pipeline_runs_end_to_end_on_real_sample_data(
    tmp_path: Path,
    sample_dir: Path,
    approved_inputs: tuple[IntegrationPlan, ValidationStrategy],
) -> None:
    state = _state(
        tmp_path,
        sample_dir,
        approved_inputs,
        exclude_leak=True,
        run_id="pipeline-e2e",
    )

    outcome = run_workflow(
        build_default_spec(),
        build_default_registry(),
        state,
        rubrics=build_pipeline_rubrics(),
    )

    assert outcome.completed, outcome.error
    assert state.attempts_for("integration")[0].critique.rubric_version == "integration.v1"
    assert state.attempts_for("eda")[0].critique.rubric_version == "eda.v1"
    assert state.attempts_for("evaluation")[0].critique.rubric_version == "evaluation.v1"
    assert [attempt.stage_id for attempt in state.attempts] == [
        "intake",
        "integration",
        "eda",
        "leakage_audit",
        "feature_pipeline",
        "splitting",
        "training",
        "evaluation",
        "report",
    ]
    assert "total_comp_ytd" not in state.blackboard[TRAINING_FRAME_COLUMNS_KEY]
    markdown = state.blackboard[FINAL_MARKDOWN_KEY]
    assert "Performance against the baseline" in markdown
    assert "Ridge" in markdown

    final = state.require(ArtifactType.FINAL_REPORT, FinalReport, name="final_report")
    assert final.markdown == markdown
    assert len(final.evaluation_artifact_id) == 64


def test_leakage_retry_drops_offending_column_on_second_attempt(
    tmp_path: Path,
    sample_dir: Path,
    approved_inputs: tuple[IntegrationPlan, ValidationStrategy],
) -> None:
    state = _state(
        tmp_path,
        sample_dir,
        approved_inputs,
        exclude_leak=False,
        run_id="pipeline-leakage-retry",
    )
    default = build_default_spec()
    selected = tuple(
        stage
        for stage in default.stages
        if stage.id in {"intake", "integration", "eda", "leakage_audit"}
    )
    spec = linear_spec(
        "leakage-correction-real-data",
        "1",
        selected,
        retry_stages=("leakage_audit",),
    )
    policy = GatePolicy.load()
    leakage_spec = replace(policy.stage("leakage_audit"), max_attempts=2)
    policy = replace(
        policy,
        stages={**policy.stages, "leakage_audit": leakage_spec},
    )

    outcome = run_workflow(
        spec,
        build_default_registry(),
        state,
        policy=policy,
        rubrics=build_pipeline_rubrics(),
    )

    assert outcome.completed, outcome.error
    attempts = state.attempts_for("leakage_audit")
    assert len(attempts) == 2
    assert attempts[0].decision is not None
    assert attempts[0].decision.verdict is GateVerdict.RETRY
    assert attempts[0].decision.correction_instructions == [
        "drop_feature: total_comp_ytd  # target leakage"
    ]
    assert attempts[1].decision is not None
    assert attempts[1].decision.verdict is GateVerdict.AUTO_PROCEED

    model_frame = state.blackboard[MODEL_FRAME_KEY]
    assert "total_comp_ytd" not in model_frame.columns
    assert state.blackboard[DROPPED_FEATURES_KEY] == frozenset({"total_comp_ytd"})

    corrected_problem = next(
        state.store.load(artifact_id, StoredProblemDefinition)
        for artifact_id in attempts[1].artifact_ids
        if state.store.load(artifact_id).artifact_type is ArtifactType.PROBLEM_DEFINITION
    )
    assert corrected_problem.excluded_columns == ["total_comp_ytd"]
    current_audit = next(
        state.store.load(artifact_id, LeakageReport)
        for artifact_id in attempts[1].artifact_ids
        if state.store.load(artifact_id).artifact_type is ArtifactType.LEAKAGE_REPORT
    )
    assert current_audit.is_clean
