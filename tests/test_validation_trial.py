"""Executor-owned validation trial and exact-binding counter-tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ads.agents.base import AgentSpec
from ads.contracts import (
    BUILTIN_PROFILES,
    Metric,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationSignals,
    ValidationStrategy,
    ValidationStrategyProposal,
)
from ads.contracts.gates import PermissionTier
from ads.llm import LARGE
from ads.orchestration import RunState
from ads.pipeline.stages import MODEL_FRAME_KEY, splitting_stage
from ads.splitting import execute_validation_trial
from ads.store import ArtifactStore, compute_artifact_id
from ads.tools import PermissionBroker, ToolRuntime, build_tool_registry


def _frame() -> pd.DataFrame:
    rows = []
    for entity in range(60):
        for visit in range(3):
            rows.append(
                {
                    "entity_id": f"e{entity:03d}",
                    "event_time": pd.Timestamp("2024-01-01")
                    + pd.Timedelta(days=entity * 3 + visit),
                    "target": entity % 2,
                    "feature": float(entity + visit),
                }
            )
    return pd.DataFrame(rows)


def _signals(frame: pd.DataFrame) -> ValidationSignals:
    return ValidationSignals(
        n_rows=len(frame),
        n_usable_rows=len(frame),
        n_folds=3,
        target_column="target",
    )


def _proposal() -> ValidationStrategyProposal:
    return ValidationStrategyProposal(
        strategy=SplitStrategy.GROUPED,
        n_folds=3,
        test_size=0.2,
        group_column="entity_id",
        rationale="Keep every entity in exactly one partition.",
        rationale_tr="Her varlığı tam olarak bir bölümde tut.",
    )


def test_grouped_trial_measures_zero_entity_overlap() -> None:
    frame = _frame()

    trial = execute_validation_trial(frame, _proposal(), _signals(frame))

    assert trial.passed is True
    assert trial.group_overlap_count == 0
    assert trial.temporal_order_violation_count == 0
    assert len(trial.fold_sizes) == 3
    assert trial.n_train_rows + trial.n_holdout_rows == len(frame)


def test_registered_trial_tool_returns_executor_measurements() -> None:
    frame = _frame()
    registry = build_tool_registry()
    broker = PermissionBroker(registry)
    agent = AgentSpec(
        id="validation-test",
        system_prompt="test",
        output_contract=ValidationStrategyProposal,
        profile=LARGE,
        allowed_tools=frozenset({"trial_validation_strategy"}),
        max_tool_tier=PermissionTier.READ_DATA,
    )
    result = broker.invoke(
        agent,
        "trial_validation_strategy",
        ToolRuntime(
            frames={"abt": frame},
            resources={"validation_signals": _signals(frame)},
        ),
        table="abt",
        proposal=_proposal().model_dump(mode="json"),
    )

    assert result.data["passed"] is True
    assert result.data["group_overlap_count"] == 0


def test_splitting_rejects_strategy_changed_after_passing_trial(tmp_path: Path) -> None:
    frame = _frame()
    signals = _signals(frame)
    trial = execute_validation_trial(frame, _proposal(), signals)
    changed = ValidationStrategy.from_proposal(
        ValidationStrategyProposal(
            strategy=SplitStrategy.RANDOM,
            n_folds=3,
            test_size=0.2,
            rationale="Changed after the grouped trial.",
            rationale_tr="Gruplu denemeden sonra değiştirildi.",
        ),
        signals,
        trial_artifact_id=compute_artifact_id(trial),
    )
    state = RunState(
        run_id="changed-validation",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[MODEL_FRAME_KEY] = frame
    state.put(trial, stage_id="validation_strategy", name="validation_trial")
    state.put(changed, stage_id="validation_strategy", name="validation_strategy")
    state.put(
        ProblemDefinition(
            task_type=TaskType.BINARY_CLASSIFICATION,
            target_column="target",
            primary_metric=Metric.ROC_AUC,
            title="Target classification",
            description="Predict the binary target.",
        ),
        stage_id="problem_discovery",
    )

    with pytest.raises(ValueError, match="semantics changed after its trial"):
        splitting_stage(state)


def test_splitting_reports_failure_reason_for_a_failed_trial_unrelated_to_leakage(
    tmp_path: Path,
) -> None:
    """A trial measured before leakage_audit ran can fail for its own reasons.

    Regression test for #440: the ValueError splitting_stage raises used to be a
    bare, uninformative message that a reader could easily (and wrongly) pin on
    whatever leakage-driven feature drop happened to run just before it. The
    trial is realized on the full pre-exclusion ABT at the validation_strategy
    stage, so its failure is never caused by a later leakage exclusion -- the
    error must say so and surface the trial's actual failure_reason.
    """
    frame = _frame()
    signals = _signals(frame)
    # A grouped proposal over a group column that does not exist in this frame
    # fails the trial deterministically (KeyError inside execute_validation_trial),
    # independent of any leakage-driven column drop.
    broken_proposal = ValidationStrategyProposal(
        strategy=SplitStrategy.GROUPED,
        n_folds=3,
        test_size=0.2,
        group_column="does_not_exist",
        rationale="Group by a column the frame does not have.",
        rationale_tr="Çerçevede bulunmayan bir sütuna göre grupla.",
    )
    trial = execute_validation_trial(frame, broken_proposal, signals)
    assert trial.passed is False
    assert trial.failure_reason

    strategy = ValidationStrategy.from_proposal(
        broken_proposal,
        signals,
        trial_artifact_id=compute_artifact_id(trial),
    )
    state = RunState(
        run_id="failed-trial",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[MODEL_FRAME_KEY] = frame
    state.put(trial, stage_id="validation_strategy", name="validation_trial")
    state.put(strategy, stage_id="validation_strategy", name="validation_strategy")
    state.put(
        ProblemDefinition(
            task_type=TaskType.BINARY_CLASSIFICATION,
            target_column="target",
            primary_metric=Metric.ROC_AUC,
            title="Target classification",
            description="Predict the binary target.",
        ),
        stage_id="problem_discovery",
    )

    with pytest.raises(ValueError, match="unrelated to any leakage-driven feature exclusion"):
        splitting_stage(state)
