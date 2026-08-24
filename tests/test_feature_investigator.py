"""Live isolation test for the authored feature investigation loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.agents.feature_investigator import MANIFEST_NAME, investigate_features
from ads.contracts import (
    FeatureExperimentManifest,
    FeatureSpec,
    Metric,
    ProblemDefinition,
    TaskType,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.sandbox import SandboxConfig, SandboxManager, materialize_frame_copies
from ads.tools import ToolRuntime
from ads.training import prepare_experiment_partition


class _LLM:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.calls = 0

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        del system, prompt, json_schema
        action = self.actions[self.calls]
        self.calls += 1
        return LLMResponse(
            text=json.dumps(action),
            parsed=action,
            model=profile.name,
            latency_s=0.01,
        )


def _code() -> str:
    return f"""
import json
import pandas as pd
from sklearn.linear_model import LinearRegression

train = pd.read_csv('/data/experiment_train.csv')
validation = pd.read_csv('/data/experiment_validation.csv')
X_train = pd.DataFrame({{'age': train['age'], 'age_squared': train['age'] ** 2}})
X_validation = pd.DataFrame({{'age': validation['age'], 'age_squared': validation['age'] ** 2}})
model = LinearRegression().fit(X_train, train['target'])
predictions = pd.DataFrame({{
    '__ads_experiment_row_id': validation['__ads_experiment_row_id'],
    'prediction': model.predict(X_validation),
}})
predictions.to_csv('/artifacts/feature_predictions.csv', index=False)
manifest = {{
    'schema_version': '1',
    'title': 'Quadratic age signal',
    'hypothesis': 'A squared age term captures the nonlinear target relationship.',
    'source_columns': ['age'],
    'engineered_features': ['age_squared'],
    'model_family': 'linear_regression',
    'verification_question': 'Is a nonlinear age effect plausible and usable at prediction time?',
    'confidence': 'medium',
    'predictions_ref': 'artifact://feature_predictions.csv',
}}
with open('/artifacts/{MANIFEST_NAME}', 'w', encoding='utf-8') as handle:
    json.dump(manifest, handle)
"""


def test_agent_manifest_cannot_author_its_own_score() -> None:
    payload = {
        "title": "Quadratic age signal",
        "hypothesis": "A squared age term captures a nonlinear target relationship.",
        "source_columns": ["age"],
        "engineered_features": ["age_squared"],
        "model_family": "linear_regression",
        "verification_question": "Is the nonlinear effect plausible in this domain?",
        "confidence": "medium",
        "predictions_ref": "artifact://feature_predictions.csv",
        "score": 1.0,
    }

    with pytest.raises(ValidationError, match="score"):
        FeatureExperimentManifest.model_validate(payload)


def test_live_authored_feature_experiment_is_host_scored_and_exploratory(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    artifacts_dir = tmp_path / "artifacts"
    data_dir.mkdir()
    artifacts_dir.mkdir()
    backend = SandboxManager(SandboxConfig(data_dir=data_dir, artifacts_dir=artifacts_dir))
    if not backend.docker_available():
        pytest.skip("Docker daemon is unavailable")
    if not backend.image_available():
        pytest.skip("ads-sandbox:latest is not built")

    frame = pd.DataFrame(
        {
            "age": [float(value) for value in range(180)],
            "segment": ["a", "b", "c"] * 60,
            "target": [float(value**2 + value % 3) for value in range(180)],
        }
    )
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    problem = ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="target",
        primary_metric=Metric.RMSE,
        title="Nonlinear regression",
        description="Predict the numeric target.",
    )
    strategy = ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        rationale="Synthetic rows have no temporal or entity boundary.",
    )
    feature_spec = FeatureSpec.from_card(card, target_column="target", excluded_columns=frozenset())
    partition = prepare_experiment_partition(frame, strategy, target_column="target")
    frames = {
        "experiment_train": partition.training,
        "experiment_validation": partition.validation_features,
    }
    materialize_frame_copies(backend, frames)
    cards = [
        profile_table(
            LoadedTable(
                name=name,
                frame=value,
                source_uri="derived",
                source_format="pandas",
            )
        )
        for name, value in frames.items()
    ]
    actions = [
        {
            "action": "call_tool",
            "tool_id": "null_rate",
            "arguments": {"table": "experiment_train", "column": "target"},
            "reason": "Confirm usable training target coverage.",
        },
        {
            "action": "call_tool",
            "tool_id": "cardinality",
            "arguments": {"table": "experiment_train", "column": "age"},
            "reason": "Measure support for a continuous age transformation.",
        },
        {
            "action": "call_tool",
            "tool_id": "execute_python",
            "arguments": {"code": _code(), "timeout": 30},
            "reason": "Fit and emit predictions for the hidden-label development split.",
        },
        {
            "action": "finish",
            "artifact_ref": f"artifact://{MANIFEST_NAME}",
            "reason": "Publish the manifest created by the isolated execution.",
        },
    ]

    result = investigate_features(
        partition=partition,
        problem=problem,
        feature_spec=feature_spec,
        llm=_LLM(actions),
        runtime=ToolRuntime.from_sources(
            cards,
            frames,
            run_id="live-feature-investigation",
            execution_backend=backend,
            artifacts_dir=artifacts_dir,
        ),
    )

    assert result.degraded_reason is None
    assert result.experiment is not None
    assert result.experiment.evidence_class == "exploratory"
    assert result.experiment.final_holdout_used is False
    assert result.experiment.score < result.experiment.baseline_score
    assert result.experiment.tool_calls == ["null_rate", "cardinality", "execute_python"]
    assert feature_spec.selection_authority == "deterministic_floor"
