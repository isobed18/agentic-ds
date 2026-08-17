"""Counter-tests for isolated, host-scored model experiments."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.agents.model_investigator import MANIFEST_NAME, investigate_model
from ads.contracts import Metric, ProblemDefinition, TaskType
from ads.contracts.base import ArtifactType
from ads.contracts.comprehension import ComprehensionBrief
from ads.contracts.evidence import MeasurementBundle, MeasurementKind
from ads.contracts.gates import BUILTIN_PROFILES, QualitySignals
from ads.contracts.model_experiment import ModelExperimentManifest
from ads.contracts.training import CandidateResult, MetricEvaluation, TrainingReport
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration import RunState, StageResult
from ads.pipeline.comprehension_stages import augment_training_stage
from ads.pipeline.stages import ABT_FRAME_KEY, EXECUTION_BACKEND_KEY
from ads.sandbox import ExecutionResult, TextOutput, materialize_frame_copies
from ads.store import ArtifactStore, compute_artifact_id
from ads.tools import ToolRuntime
from ads.training import prepare_experiment_partition
from ads.training.experiments import ROW_ID_COLUMN


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": [float(value) for value in range(120)],
            "segment": ["a", "b", "c"] * 40,
            "forbidden_leak": [float(value * 2) for value in range(120)],
            "target": [float(value * 2) for value in range(120)],
        }
    )


def _strategy() -> ValidationStrategy:
    return ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        n_folds=3,
        test_size=0.2,
        rationale="Seeded development split.",
    )


def _problem() -> ProblemDefinition:
    return ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="target",
        primary_metric=Metric.RMSE,
        title="Predict target",
        description="Predict the numeric target from pre-outcome features.",
        excluded_columns=["forbidden_leak"],
    )


def _report() -> TrainingReport:
    metric = MetricEvaluation(
        metric=Metric.RMSE,
        fold_scores=[70.0, 69.0, 71.0],
        cv_mean=70.0,
        cv_std=1.0,
        holdout_score=70.0,
    )
    return TrainingReport(
        task_type=TaskType.REGRESSION,
        primary_metric=Metric.RMSE,
        results=[
            CandidateResult(
                candidate_id="dummy",
                display_name="Dummy",
                estimator_class="DummyRegressor",
                is_baseline=True,
                metrics=[metric],
            )
        ],
        winner_id="dummy",
        fitted_pipeline_verified=True,
        fit_scope="outer_train_only",
        outer_train_row_count=96,
        holdout_row_count=24,
        holdout_rows_used_for_fit=0,
        inner_fold_fit_count=3,
    )


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "title": "Linear development model",
        "hypothesis": "A linear model should capture the measured numeric relationship.",
        "model_family": "linear regression",
        "declared_feature_columns": ["x"],
        "predictions_ref": "artifact://predictions.csv",
    }


class _Backend:
    def __init__(self, root: Path, *, missing_row: bool = False) -> None:
        self.data_dir = root / "data"
        self.artifacts_dir = root / "artifacts"
        self.data_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)
        self.missing_row = missing_row
        self.destroyed = False

    def available(self) -> bool:
        return True

    def create_session(self, run_id: str) -> str:
        return f"session:{run_id}"

    def execute(self, session: str, code: str, timeout: float = 30.0) -> ExecutionResult:
        validation = pd.read_csv(self.data_dir / "experiment_validation.csv")
        predictions = pd.DataFrame(
            {
                ROW_ID_COLUMN: validation[ROW_ID_COLUMN],
                "prediction": validation["x"] * 2.0,
            }
        )
        if self.missing_row:
            predictions = predictions.iloc[:-1]
        predictions.to_csv(self.artifacts_dir / "predictions.csv", index=False)
        (self.artifacts_dir / MANIFEST_NAME).write_text(
            json.dumps(_manifest()), encoding="utf-8"
        )
        return ExecutionResult(
            execution_count=1,
            outputs=(TextOutput(text="experiment complete\n", stream="stdout"),),
        )

    def destroy(self, session: str) -> None:
        self.destroyed = True


class _LLM:
    def __init__(self, actions: list[Any]) -> None:
        self.actions = actions
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        action = self.actions[len(self.calls)]
        if callable(action):
            action = action(prompt)
        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(
            text=json.dumps(action),
            model=profile.name,
            latency_s=0.01,
            parsed=action,
        )


def _actions(*, abandon_after_failure: bool = False) -> list[dict[str, Any]]:
    actions = [
        {
            "action": "call_tool",
            "tool_id": "column_profile",
            "arguments": {"table": "experiment_train", "column": "x"},
            "reason": "Inspect the candidate feature before choosing a model.",
        },
        {
            "action": "call_tool",
            "tool_id": "execute_python",
            "arguments": {"code": "# fit train and write manifest plus predictions"},
            "reason": "Fit and predict against the feature-only validation copy.",
        },
        {
            "action": "finish",
            "artifact_ref": f"artifact://{MANIFEST_NAME}",
            "reason": "Submit the files created by the same isolated execution.",
        },
    ]
    if abandon_after_failure:
        actions.append(
            {
                "action": "abandon",
                "reason": "The host rejected incomplete validation coverage.",
            }
        )
    return actions


def _runtime(tmp_path: Path, backend: _Backend, partition) -> ToolRuntime:
    frames = {
        "experiment_train": partition.training,
        "experiment_validation": partition.validation_features,
    }
    for name, frame in frames.items():
        frame.to_csv(backend.data_dir / f"{name}.csv", index=False)
    cards = [
        profile_table(
            LoadedTable(
                name=name,
                frame=frame,
                source_uri="derived",
                source_format="pandas",
            )
        )
        for name, frame in frames.items()
    ]
    return ToolRuntime.from_sources(
        cards,
        frames,
        run_id="model-investigator-test",
        execution_backend=backend,
        artifacts_dir=backend.artifacts_dir,
    )


def test_partition_hides_final_holdout_validation_labels_and_exclusions() -> None:
    frame = _frame()
    partition = prepare_experiment_partition(
        frame,
        _strategy(),
        target_column="target",
        excluded_columns=["forbidden_leak"],
    )

    assert "target" in partition.training
    assert "target" not in partition.validation_features
    assert "forbidden_leak" not in partition.training
    assert "forbidden_leak" not in partition.validation_features
    assert len(partition.training) + len(partition.validation_features) < len(frame)
    assert list(partition.validation_features[ROW_ID_COLUMN]) == list(
        partition.validation_targets.index
    )


def test_manifest_cannot_author_a_score() -> None:
    payload = {**_manifest(), "score": 0.0}

    with pytest.raises(ValidationError, match="Extra inputs"):
        ModelExperimentManifest.model_validate(payload)


def test_agent_predictions_are_scored_by_host_on_hidden_labels(tmp_path: Path) -> None:
    partition = prepare_experiment_partition(
        _frame(),
        _strategy(),
        target_column="target",
        excluded_columns=["forbidden_leak"],
    )
    backend = _Backend(tmp_path)

    result = investigate_model(
        partition=partition,
        problem=_problem(),
        report=_report(),
        llm=_LLM(_actions()),
        runtime=_runtime(tmp_path, backend, partition),
    )

    assert result.degraded_reason is None
    assert result.experiment is not None
    assert result.experiment.score == pytest.approx(0.0)
    assert result.experiment.baseline_score > 0.0
    assert result.experiment.final_holdout_used is False
    assert result.experiment.evidence_class == "exploratory"
    assert result.experiment.tool_calls == ["column_profile", "execute_python"]
    assert result.audit.raw_rows_shared is True
    assert backend.destroyed is True


def test_incomplete_predictions_never_become_an_experiment(tmp_path: Path) -> None:
    partition = prepare_experiment_partition(
        _frame(),
        _strategy(),
        target_column="target",
        excluded_columns=["forbidden_leak"],
    )
    backend = _Backend(tmp_path, missing_row=True)

    result = investigate_model(
        partition=partition,
        problem=_problem(),
        report=_report(),
        llm=_LLM(_actions(abandon_after_failure=True)),
        runtime=_runtime(tmp_path, backend, partition),
    )

    assert result.experiment is None
    assert "invalid_experiment_output" in result.audit.members[0].validation_failures
    assert backend.destroyed is True


def test_unexpected_model_client_failure_degrades_inside_investigator(
    tmp_path: Path,
) -> None:
    class CrashingLLM:
        def generate_structured(self, **kwargs):
            raise KeyError("unexpected local model response")

    partition = prepare_experiment_partition(
        _frame(),
        _strategy(),
        target_column="target",
        excluded_columns=["forbidden_leak"],
    )
    backend = _Backend(tmp_path)

    result = investigate_model(
        partition=partition,
        problem=_problem(),
        report=_report(),
        llm=CrashingLLM(),
        runtime=_runtime(tmp_path, backend, partition),
    )

    assert result.experiment is None
    assert result.audit.members[0].validation_failures == [
        "investigation_error:KeyError"
    ]
    assert backend.destroyed is False  # failure occurred before a session was created


def test_training_augmentation_binds_interpretation_to_host_score(
    tmp_path: Path,
) -> None:
    frame = _frame()
    problem = _problem()
    strategy = _strategy()
    report = _report()
    backend = _Backend(tmp_path / "sandbox")
    materialize_frame_copies(backend, {"abt": frame})
    store = ArtifactStore(tmp_path / "store")
    state = RunState(
        run_id="authored-model-stage",
        store=store,
        profile=BUILTIN_PROFILES["full_auto"],
        blackboard={ABT_FRAME_KEY: frame, EXECUTION_BACKEND_KEY: backend},
    )
    state.put(problem, stage_id="problem_discovery")
    state.put(strategy, stage_id="validation_strategy")
    floor_signals = QualitySignals(best_score=-70.0, naive_baseline_score=-70.0)

    def interpretation(prompt: str) -> dict[str, Any]:
        measurement_id = re.search(r"m_[0-9a-f]{24}", prompt)
        assert measurement_id is not None
        return {
            "items": [
                {
                    "kind": "open_question",
                    "subjects": [{"table": "abt", "column": "target"}],
                    "measurement_ids": [measurement_id.group(0)],
                    "interpretation": (
                        "The measured development result suggests this candidate merits "
                        "testing across the remaining folds."
                    ),
                    "why_it_matters": (
                        "A stable gain could justify adding the approach to the deterministic "
                        "candidate menu."
                    ),
                    "verification_question": (
                        "Does the improvement remain after evaluation across every inner fold?"
                    ),
                    "confidence": "medium",
                }
            ]
        }

    augmented = augment_training_stage(
        lambda state, correction=None: StageResult(
            artifacts=[report],
            signals=floor_signals,
            names={0: "training_report"},
        ),
        _LLM([*_actions(), interpretation]),
    )

    result = augmented(state)

    experiment = next(
        artifact
        for artifact in result.artifacts
        if artifact.artifact_type is ArtifactType.MODEL_EXPERIMENT
    )
    bundle = next(
        artifact for artifact in result.artifacts if isinstance(artifact, MeasurementBundle)
    )
    brief = next(
        artifact for artifact in result.artifacts if isinstance(artifact, ComprehensionBrief)
    )
    measurement = bundle.records[0]
    assert result.signals is floor_signals
    assert measurement.kind is MeasurementKind.MODEL_EXPERIMENT
    assert measurement.source_artifact_id == compute_artifact_id(experiment)
    assert measurement.value["score"] == pytest.approx(0.0)
    assert measurement.value["final_holdout_used"] is False
    assert brief.items[0].citations == [measurement]
    assert brief.items[0].epistemic_state == "proposed"
    assert not (backend.data_dir / "abt.csv").exists()


def test_failing_model_agent_preserves_deterministic_training(
    tmp_path: Path,
) -> None:
    class CrashingLLM:
        def generate_structured(self, **kwargs):
            raise KeyError("unexpected local model response")

    frame = _frame()
    problem = _problem()
    strategy = _strategy()
    report = _report()
    backend = _Backend(tmp_path / "sandbox")
    state = RunState(
        run_id="degraded-model-stage",
        store=ArtifactStore(tmp_path / "store"),
        profile=BUILTIN_PROFILES["full_auto"],
        blackboard={ABT_FRAME_KEY: frame, EXECUTION_BACKEND_KEY: backend},
    )
    state.put(problem, stage_id="problem_discovery")
    state.put(strategy, stage_id="validation_strategy")
    floor_signals = QualitySignals(best_score=-70.0, naive_baseline_score=-70.0)
    augmented = augment_training_stage(
        lambda state, correction=None: StageResult(
            artifacts=[report],
            signals=floor_signals,
        ),
        CrashingLLM(),
    )

    result = augmented(state)

    assert result.signals is floor_signals
    assert result.artifacts[0] is report
    assert not any(
        artifact.artifact_type is ArtifactType.MODEL_EXPERIMENT
        for artifact in result.artifacts
    )
    audit = result.artifacts[-1]
    assert audit.artifact_type is ArtifactType.AGENT_AUDIT
    assert audit.valid_members == 0
    assert "investigation_error:KeyError" in audit.members[0].validation_failures
