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
    ValidationTrial,
    validation_strategy_fingerprint,
)
from ads.contracts.gates import PermissionTier
from ads.llm import LARGE
from ads.orchestration import RunState
from ads.pipeline.stages import MODEL_FRAME_KEY, splitting_stage
from ads.splitting import execute_validation_trial
from ads.splitting.trial import _invariant_failure_reason
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


def _strategy(frame: pd.DataFrame) -> ValidationStrategy:
    return ValidationStrategy.from_proposal(_proposal(), _signals(frame))


def test_a_failed_invariant_names_the_invariant_and_its_measurement() -> None:
    """#440: the invariant branch of the trial used to explain nothing.

    `execute_validation_trial` has two failure paths. The exception path already
    writes the whole `SplitError` text into `failure_reason` -- so surfacing
    that field in `splitting_stage` explains those failures exactly. The other
    path, where the split executed and then failed a measurement, hardcoded
    "Realized split violated a measured invariant.", a sentence with no content.
    Surfacing an empty sentence is no better than surfacing nothing, so the
    reason now names which invariant broke, using numbers the trial already
    measured on the same object.
    """
    reason = _invariant_failure_reason(
        _strategy(_frame()),
        n_input_rows=180,
        n_train_rows=180,
        n_holdout_rows=0,
        fold_sizes=[(120, 60), (150, 30), (170, 10)],
        group_overlap=0,
        temporal_violations=0,
    )

    assert "the holdout partition is empty (0 of 180 input rows)" in reason
    # Only the invariant that actually broke -- listing the ones that held
    # would bury the one that did not.
    assert "training partition" not in reason
    assert "group values" not in reason


def test_every_broken_invariant_is_named_with_the_column_it_is_about() -> None:
    # A grouped-temporal split can break both boundary rules at once, and
    # knowing only one of them sends a reader to the wrong column.
    reason = _invariant_failure_reason(
        _strategy(_frame()),
        n_input_rows=180,
        n_train_rows=140,
        n_holdout_rows=40,
        fold_sizes=[(100, 40), (110, 30)],
        group_overlap=7,
        temporal_violations=2,
    )

    assert "7 group values in 'entity_id' appear on both sides of a split boundary" in reason
    # Three boundaries: the outer holdout plus the two inner folds.
    assert "2 of 3 boundaries are not ordered in time" in reason


def test_an_empty_fold_is_reported_with_the_sizes_that_make_it_empty() -> None:
    # "A fold is empty" is not actionable; "fold 2 splits 150/0" says the inner
    # splitter ran out of rows to validate on, which points at n_folds.
    reason = _invariant_failure_reason(
        _strategy(_frame()),
        n_input_rows=180,
        n_train_rows=150,
        n_holdout_rows=30,
        fold_sizes=[(100, 50), (150, 0), (0, 150)],
        group_overlap=0,
        temporal_violations=0,
    )

    assert "2 of 3 folds have an empty side" in reason
    assert "fold 2 splits 150/0" in reason
    assert "fold 3 splits 0/150" in reason


def test_the_reason_stays_inside_the_length_the_contract_accepts() -> None:
    # `ValidationTrial.failure_reason` is capped at 1000 characters, and a
    # failed trial without a reason fails contract validation entirely -- so an
    # over-long reason would trade a bad message for no artifact at all.
    frame = _frame()
    fold_sizes = [(0, 0)] * 200

    reason = _invariant_failure_reason(
        _strategy(frame),
        n_input_rows=len(frame),
        n_train_rows=0,
        n_holdout_rows=0,
        fold_sizes=fold_sizes,
        group_overlap=9,
        temporal_violations=9,
    )

    assert len(reason) <= 1000
    assert "200 of 200 folds have an empty side" in reason
    assert "and 195 more" in reason
    trial = ValidationTrial(
        proposal_fingerprint=validation_strategy_fingerprint(_proposal()),
        strategy=SplitStrategy.GROUPED,
        n_input_rows=len(frame),
        n_train_rows=0,
        n_holdout_rows=0,
        fold_sizes=fold_sizes,
        passed=False,
        failure_reason=reason,
    )
    assert trial.failure_reason == reason


def test_a_passing_trial_records_no_reason_at_all() -> None:
    # The contract rejects a passing trial that carries a reason, so the two
    # halves of this change have to stay on their own side of `passed`.
    trial = execute_validation_trial(_frame(), _proposal(), _signals(_frame()))

    assert trial.passed is True
    assert trial.failure_reason is None


def test_splitting_reports_which_invariant_the_trial_broke(tmp_path: Path) -> None:
    """The two halves of #440 meet here.

    `splitting_stage` surfaces `failure_reason`; the reason now says something.
    Without both, a run that got past the escalation with an invariant failure
    still ends at a message that names no cause -- and the leakage exclusion
    that happened to run just before it still looks like the reason.
    """
    frame = _frame()
    reason = _invariant_failure_reason(
        _strategy(frame),
        n_input_rows=len(frame),
        n_train_rows=len(frame),
        n_holdout_rows=0,
        fold_sizes=[(120, 60)],
        group_overlap=0,
        temporal_violations=0,
    )
    trial = ValidationTrial(
        proposal_fingerprint=validation_strategy_fingerprint(_proposal()),
        strategy=SplitStrategy.GROUPED,
        n_input_rows=len(frame),
        n_train_rows=len(frame),
        n_holdout_rows=0,
        fold_sizes=[(120, 60)],
        passed=False,
        failure_reason=reason,
    )
    strategy = ValidationStrategy.from_proposal(
        _proposal(),
        _signals(frame),
        trial_artifact_id=compute_artifact_id(trial),
    )
    state = RunState(
        run_id="failed-invariant",
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

    with pytest.raises(ValueError, match="the holdout partition is empty"):
        splitting_stage(state)
