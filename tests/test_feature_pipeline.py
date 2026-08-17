"""Counter-tests for the first-class feature preprocessing boundary."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ads.contracts import (
    BUILTIN_PROFILES,
    ArtifactType,
    FeatureSpec,
    Metric,
    ProblemDefinition,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)
from ads.orchestration import RunState
from ads.pipeline.stages import MODEL_FRAME_KEY, feature_pipeline_stage, training_stage
from ads.store import ArtifactStore


def _state(tmp_path: Path) -> tuple[RunState, pd.DataFrame]:
    frame = pd.DataFrame(
        {
            "customer_id": [f"c{index}" for index in range(120)],
            "age": [20 + index % 50 for index in range(120)],
            "segment": ["a", "b", "c"] * 40,
            "signup_at": pd.date_range("2024-01-01", periods=120, freq="D"),
            "target": [float(index % 17) for index in range(120)],
        }
    )
    state = RunState(
        run_id="feature-test",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[MODEL_FRAME_KEY] = frame
    state.put(
        ProblemDefinition(
            task_type=TaskType.REGRESSION,
            target_column="target",
            primary_metric=Metric.RMSE,
            title="Target regression",
            description="Predict the numeric target.",
        ),
        stage_id="problem_discovery",
    )
    state.put(
        ValidationStrategy(
            strategy=SplitStrategy.RANDOM,
            rationale="No ordering or repeated entity signal is present.",
        ),
        stage_id="validation_strategy",
    )
    return state, frame


def test_feature_stage_accounts_for_every_column_without_fitting_global_state(
    tmp_path: Path,
) -> None:
    state, frame = _state(tmp_path)

    result = feature_pipeline_stage(state)
    spec = result.artifacts[0]

    assert isinstance(spec, FeatureSpec)
    assert spec.preprocessing_scope == "fit_per_training_fold"
    assert spec.source_mutation_allowed is False
    assert set(spec.numeric_columns) == {"age"}
    assert set(spec.categorical_columns) == {"segment"}
    assert set(spec.datetime_columns) == {"signup_at"}
    assert set(spec.dropped_columns) == {"customer_id"}
    assert set(spec.input_columns) == set(frame.columns)


def test_training_rejects_feature_spec_that_does_not_match_current_frame(
    tmp_path: Path,
) -> None:
    state, _ = _state(tmp_path)
    correct = feature_pipeline_stage(state).artifacts[0]
    assert isinstance(correct, FeatureSpec)
    tampered = correct.model_copy(
        update={
            "numeric_columns": [],
            "dropped_columns": [*correct.dropped_columns, "age"],
        }
    )
    state.put(tampered, stage_id="feature_pipeline", name="feature_spec")

    with pytest.raises(ValueError, match="does not match the current model-frame schema"):
        training_stage(state)

    assert state.all_of(ArtifactType.TRAINED_MODEL, object) == []
