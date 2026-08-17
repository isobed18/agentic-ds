"""Training-stage tests: honest CV, mandatory baselines, and gate wiring."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.preprocessing import StandardScaler

from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict
from ads.contracts.problem import Metric, TaskType
from ads.contracts.training import TrainingReport
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.gates import GatePolicy, evaluate_gate
from ads.orchestration import CritiqueContext, critique_stage
from ads.pipeline import build_pipeline_rubrics
from ads.store import ArtifactStore
from ads.training import TrainingError, default_candidates, train_candidates


def _strategy() -> ValidationStrategy:
    return ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        n_folds=4,
        test_size=0.2,
        rationale="Seeded iid test data.",
    )


def _regression_frame(*, predictive: bool) -> pd.DataFrame:
    rng = np.random.default_rng(12345)
    features = rng.normal(size=(500, 6))
    if predictive:
        target = 4.0 * features[:, 0] - 2.0 * features[:, 1] + rng.normal(
            scale=0.15, size=len(features)
        )
    else:
        target = rng.normal(size=len(features))
    frame = pd.DataFrame(features, columns=[f"x{i}" for i in range(features.shape[1])])
    frame["target"] = target
    return frame


@pytest.fixture(scope="module")
def predictive_report() -> Iterator[TrainingReport]:
    yield train_candidates(
        _regression_frame(predictive=True),
        _strategy(),
        StandardScaler,
        default_candidates(TaskType.REGRESSION),
        target_column="target",
        task_type=TaskType.REGRESSION,
    )


@pytest.fixture(scope="module")
def noise_report() -> Iterator[TrainingReport]:
    yield train_candidates(
        _regression_frame(predictive=False),
        _strategy(),
        StandardScaler,
        default_candidates(TaskType.REGRESSION),
        target_column="target",
        task_type=TaskType.REGRESSION,
    )


@pytest.mark.parametrize(
    ("task_type", "baseline_type"),
    [
        (TaskType.REGRESSION, DummyRegressor),
        (TaskType.BINARY_CLASSIFICATION, DummyClassifier),
        (TaskType.MULTICLASS_CLASSIFICATION, DummyClassifier),
    ],
)
def test_baseline_is_always_present_and_first(
    task_type: TaskType, baseline_type: type
) -> None:
    candidates = default_candidates(task_type)

    assert isinstance(candidates[0].estimator_factory(), baseline_type)
    assert len(candidates) == 4


def test_runner_rejects_a_menu_without_a_first_baseline() -> None:
    candidates = default_candidates(TaskType.REGRESSION)

    with pytest.raises(TrainingError, match="first candidate"):
        train_candidates(
            _regression_frame(predictive=True),
            _strategy(),
            StandardScaler,
            candidates[1:],
            target_column="target",
            task_type=TaskType.REGRESSION,
        )


def test_runner_owns_null_target_filtering_and_records_dropped_rows() -> None:
    frame = _regression_frame(predictive=True).iloc[:240].copy()
    frame.loc[frame.index[::9], "target"] = np.nan
    candidates = default_candidates(TaskType.REGRESSION)[:2]

    report = train_candidates(
        frame,
        _strategy(),
        StandardScaler,
        candidates,
        target_column="target",
        task_type=TaskType.REGRESSION,
    )
    explicitly_filtered = train_candidates(
        frame.dropna(subset=["target"]),
        _strategy(),
        StandardScaler,
        candidates,
        target_column="target",
        task_type=TaskType.REGRESSION,
    )

    assert report.input_row_count == 240
    assert report.target_null_rows_dropped == 27
    assert report.training_row_count == 213
    assert report.winner_id == explicitly_filtered.winner_id
    assert report.winner.evaluation_for(Metric.RMSE).holdout_score == pytest.approx(
        explicitly_filtered.winner.evaluation_for(Metric.RMSE).holdout_score
    )


def test_runner_rejects_an_entirely_null_target() -> None:
    frame = _regression_frame(predictive=True).iloc[:40].copy()
    frame["target"] = np.nan

    with pytest.raises(TrainingError, match="no labeled rows"):
        train_candidates(
            frame,
            _strategy(),
            StandardScaler,
            default_candidates(TaskType.REGRESSION)[:2],
            target_column="target",
            task_type=TaskType.REGRESSION,
        )


def test_predictive_data_beats_the_baseline(predictive_report: TrainingReport) -> None:
    winner = predictive_report.winner.evaluation_for(Metric.RMSE)
    baseline = predictive_report.baseline.evaluation_for(Metric.RMSE)

    assert predictive_report.winner_id != predictive_report.baseline.candidate_id
    assert winner.cv_mean < baseline.cv_mean
    assert winner.holdout_score < baseline.holdout_score
    assert len(winner.fold_scores) == _strategy().n_folds
    assert {evaluation.metric for evaluation in predictive_report.winner.metrics} == {
        Metric.RMSE,
        Metric.MAE,
        Metric.R2,
    }


def test_runner_records_executor_owned_fit_scope(
    predictive_report: TrainingReport,
) -> None:
    assert predictive_report.fitted_pipeline_verified
    assert predictive_report.fit_scope == "outer_train_only"
    assert predictive_report.holdout_rows_used_for_fit == 0
    assert predictive_report.outer_train_row_count is not None
    assert predictive_report.holdout_row_count is not None
    assert predictive_report.inner_fold_fit_count == _strategy().n_folds


def test_training_rubric_rejects_a_legacy_report_without_fit_scope(
    predictive_report: TrainingReport,
) -> None:
    fit_fields = {
        "fitted_pipeline_verified",
        "fit_scope",
        "outer_train_row_count",
        "holdout_row_count",
        "holdout_rows_used_for_fit",
        "inner_fold_fit_count",
    }
    legacy_report = TrainingReport.model_validate(
        predictive_report.model_dump(exclude=fit_fields)
    )
    rubric = build_pipeline_rubrics().get("training")
    assert rubric is not None

    critique = critique_stage(
        rubric,
        CritiqueContext(stage_id="training", artifacts=[legacy_report]),
    )

    assert "features.pipeline_is_fitted_object" in critique.unmet_criteria
    assert "features.no_test_fold_statistics" in critique.unmet_criteria


def test_pure_noise_does_not_beat_its_baseline(noise_report: TrainingReport) -> None:
    signals = noise_report.to_quality_signals()

    assert signals.best_score is not None
    assert signals.naive_baseline_score is not None
    assert signals.best_score <= signals.naive_baseline_score


def test_noise_report_triggers_model_below_baseline_gate(
    noise_report: TrainingReport,
) -> None:
    policy = GatePolicy.load()
    decision = evaluate_gate(
        stage=policy.stage("training"),
        signals=noise_report.to_quality_signals(),
        profile=BUILTIN_PROFILES["checkpointed"],
        policy=policy,
    )

    assert decision.verdict is GateVerdict.ESCALATE
    assert decision.reason_code == "model_below_baseline"


def test_training_report_round_trips_through_registry(
    noise_report: TrainingReport, tmp_path
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put(noise_report, run_id="training-run")

    loaded = store.load(ref.artifact_id)
    assert isinstance(loaded, TrainingReport)
    assert loaded.winner_id == noise_report.winner_id


@pytest.mark.parametrize(
    ("task_type", "expected_metrics"),
    [
        (
            TaskType.BINARY_CLASSIFICATION,
            {
                Metric.ROC_AUC,
                Metric.AVERAGE_PRECISION,
                Metric.F1,
                Metric.ACCURACY,
                Metric.BALANCED_ACCURACY,
            },
        ),
        (
            TaskType.MULTICLASS_CLASSIFICATION,
            {Metric.F1, Metric.ACCURACY, Metric.BALANCED_ACCURACY},
        ),
    ],
)
def test_classification_metrics_match_contract(
    task_type: TaskType, expected_metrics: set[Metric]
) -> None:
    rng = np.random.default_rng(9876)
    features = rng.normal(size=(300, 4))
    signal = features[:, 0] + 0.7 * features[:, 1]
    if task_type is TaskType.BINARY_CLASSIFICATION:
        target = np.where(signal > 0.0, "yes", "no")
    else:
        target = np.where(signal < -0.6, "low", np.where(signal > 0.6, "high", "mid"))
    frame = pd.DataFrame(features, columns=[f"x{i}" for i in range(features.shape[1])])
    frame["target"] = target
    strategy = ValidationStrategy(
        strategy=SplitStrategy.STRATIFIED,
        n_folds=3,
        test_size=0.2,
        rationale="Preserve seeded class proportions.",
    )

    report = train_candidates(
        frame,
        strategy,
        StandardScaler,
        default_candidates(task_type)[:2],
        target_column="target",
        task_type=task_type,
    )

    assert {evaluation.metric for evaluation in report.winner.metrics} == expected_metrics
