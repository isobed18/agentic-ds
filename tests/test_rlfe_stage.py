"""The RL feature-engineering stage, and the second model it lets training build.

The service is never live here: each test injects a scripted client, and the
default suite environment points the real one at a closed port.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

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
from ads.contracts.rlfe import RlFeatureReport
from ads.contracts.training import EnhancedTrainingReport, TrainingReport
from ads.dataflow import persist_table_asset
from ads.integrations.rlfe import RlfeClient
from ads.orchestration import RunState
from ads.pipeline.stages import (
    ABT_FRAME_KEY,
    MODEL_FRAME_KEY,
    RLFE_CLIENT_KEY,
    feature_pipeline_stage,
    rl_feature_engineering_stage,
    splitting_stage,
    training_stage,
)
from ads.store import ArtifactStore, compute_artifact_id


def _response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "http://testserver/"),
    )


ANALYSIS_OK = {
    "dataset_id": "b" * 32,
    "filename": "dataset.csv",
    "applicability": {"applicable": True, "status": "SUPPORTED", "reasons": []},
    "leakage_review": {"required": False, "detected": []},
}


def _result(selected: list[str], generated: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "result": {
            "primary_metric": "rmse",
            "primary_direction": "minimize",
            "baseline": {"score": 4.9},
            "optimized": {"score": 4.2},
            "score_improvement": 0.7,
            "selected_features": selected,
            "removed_features": ["segment"],
            "generated_features": generated,
            "search": {"termination_reason": "budget_reached"},
        }
    }


class _Recorder:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for (route_method, fragment), response in self.routes.items():
            if method == route_method and fragment in url:
                self.calls.append((method, url, kwargs))
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unscripted request: {method} {url}")

    def uploaded_csv(self) -> str:
        for _, url, kwargs in self.calls:
            if "/analyze" in url:
                return kwargs["files"]["file"][1].decode("utf-8")
        raise AssertionError("no upload was made")


def _client(routes: dict[tuple[str, str], Any]) -> tuple[RlfeClient, _Recorder]:
    recorder = _Recorder(routes)
    return (
        RlfeClient(
            base_url="http://127.0.0.1:8000",
            transport=recorder,
            poll_interval_seconds=0.0,
            sleep=lambda _: None,
        ),
        recorder,
    )


def _applicable_client(generated: list[dict[str, Any]] | None = None):
    return _client(
        {
            ("POST", "/analyze"): _response(200, ANALYSIS_OK),
            ("POST", "/optimize"): _response(202, {"job_id": "j1", "status": "queued"}),
            ("GET", "/jobs/"): _response(200, {"status": "completed"}),
            ("GET", "/results/"): _response(
                200,
                _result(
                    ["age", "tenure"],
                    generated
                    if generated is not None
                    else [
                        {
                            "name": "divide__age__tenure",
                            "operation": "divide",
                            "inputs": ["age", "tenure"],
                            "expression": "age / tenure",
                        }
                    ],
                ),
            ),
        }
    )


def _ready_state(
    tmp_path: Path, *, strategy: ValidationStrategy | None = None
) -> tuple[RunState, pd.DataFrame]:
    """A run that has reached the point just before RL feature engineering."""
    frame = pd.DataFrame(
        {
            "customer_id": [f"c{index}" for index in range(120)],
            "age": [20.0 + index % 50 for index in range(120)],
            "tenure": [1.0 + index % 7 for index in range(120)],
            "segment": ["a", "b", "c"] * 40,
            "event_time": pd.date_range("2024-01-01", periods=120, freq="D"),
            "target": [float(index % 17) for index in range(120)],
        }
    )
    state = RunState(
        run_id="rlfe-test",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[ABT_FRAME_KEY] = frame
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
        strategy
        or ValidationStrategy(
            strategy=SplitStrategy.RANDOM,
            rationale="No ordering or repeated entity signal is present.",
        ),
        stage_id="validation_strategy",
    )
    asset, _ = persist_table_asset(
        state.store,
        frame,
        run_id=state.run_id,
        producer_component_id="integrate-data",
        stage_exec_id="integration",
        name="integrated_table",
    )
    for artifact in feature_pipeline_stage(state).artifacts:
        state.put(artifact, stage_id="feature_pipeline", name="feature_spec")
    for artifact in splitting_stage(state).artifacts:
        state.put(artifact, stage_id="splitting", name="split_manifest")
    assert asset is not None
    return state, frame


# ------------------------------------------------------------------- egress


def test_only_model_eligible_columns_leave_the_host(tmp_path: Path) -> None:
    """The identifier column is dropped by the FeatureSpec and must not be uploaded.

    This is the privacy boundary: what the pipeline refuses to model must not be
    handed to a remote service either. Send `frame` instead of the routed subset
    and `customer_id` appears in the upload.
    """
    state, _ = _ready_state(tmp_path)
    client, recorder = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    rl_feature_engineering_stage(state)

    header = recorder.uploaded_csv().splitlines()[0]
    assert "customer_id" not in header
    assert "age" in header and "target" in header


def test_only_training_rows_leave_the_host(tmp_path: Path) -> None:
    """The search must not see the holdout, or the enhanced score means nothing."""
    state, frame = _ready_state(tmp_path)
    client, recorder = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    result = rl_feature_engineering_stage(state)
    report = result.artifacts[0]
    assert isinstance(report, RlFeatureReport)

    uploaded_rows = len(recorder.uploaded_csv().strip().splitlines()) - 1
    assert uploaded_rows == report.sent_row_count
    assert uploaded_rows < len(frame)


# ------------------------------------------------------------ degraded paths


def test_unreachable_service_reports_unavailable_and_does_not_fail(tmp_path: Path) -> None:
    state, _ = _ready_state(tmp_path)
    client, _ = _client({("POST", "/analyze"): httpx.ConnectError("refused")})
    state.blackboard[RLFE_CLIENT_KEY] = client

    report = rl_feature_engineering_stage(state).artifacts[0]

    assert isinstance(report, RlFeatureReport)
    assert report.status == "unavailable"
    assert report.selected_features == []


def test_unreachable_service_still_leaves_the_run_with_one_model(tmp_path: Path) -> None:
    """The enhancement is not evidence: losing it costs the run nothing."""
    state, _ = _ready_state(tmp_path)
    client, _ = _client({("POST", "/analyze"): httpx.ConnectError("refused")})
    state.blackboard[RLFE_CLIENT_KEY] = client
    for artifact in rl_feature_engineering_stage(state).artifacts:
        state.put(artifact, stage_id="rl_feature_engineering", name="rl_feature_report")

    result = training_stage(state)

    assert len(result.artifacts) == 1
    assert isinstance(result.artifacts[0], TrainingReport)
    assert not isinstance(result.artifacts[0], EnhancedTrainingReport)


def test_missing_target_is_not_applicable_without_calling_the_service(tmp_path: Path) -> None:
    state, _ = _ready_state(tmp_path)
    state.put(
        ProblemDefinition(
            task_type=TaskType.ANOMALY_DETECTION,
            target_column=None,
            primary_metric=Metric.SILHOUETTE,
            title="Flag unusual rows",
            description="No supervised target is available.",
        ),
        stage_id="problem_discovery",
    )
    client, recorder = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    report = rl_feature_engineering_stage(state).artifacts[0]

    assert isinstance(report, RlFeatureReport)
    assert report.status == "not_applicable"
    assert report.reasons == ["NO_TARGET"]
    assert recorder.calls == []


def test_unapplicable_analysis_produces_no_enhanced_model(tmp_path: Path) -> None:
    state, _ = _ready_state(tmp_path)
    analysis = {
        **ANALYSIS_OK,
        "applicability": {
            "applicable": False,
            "status": "UNSUPPORTED",
            "reasons": ["INSUFFICIENT_ROWS"],
        },
    }
    client, _ = _client({("POST", "/analyze"): _response(200, analysis)})
    state.blackboard[RLFE_CLIENT_KEY] = client
    for artifact in rl_feature_engineering_stage(state).artifacts:
        state.put(artifact, stage_id="rl_feature_engineering", name="rl_feature_report")

    result = training_stage(state)

    assert [type(item) for item in result.artifacts] == [TrainingReport]


# ------------------------------------------------------------ the happy path


def _run_through_training(state: RunState):
    """Run both stages and persist their output, the way the runner would."""
    for artifact in rl_feature_engineering_stage(state).artifacts:
        state.put(artifact, stage_id="rl_feature_engineering", name="rl_feature_report")
    result = training_stage(state)
    for index, artifact in enumerate(result.artifacts):
        state.put(artifact, stage_id="training", name=result.names.get(index))
    return result


def test_applicable_search_adds_a_second_model_bound_to_the_first(tmp_path: Path) -> None:
    state, _ = _ready_state(tmp_path)
    client, _ = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    result = _run_through_training(state)

    assert len(result.artifacts) == 2
    base, enhanced = result.artifacts
    assert isinstance(base, TrainingReport)
    assert isinstance(enhanced, EnhancedTrainingReport)
    assert enhanced.base_model_artifact_id == compute_artifact_id(base)
    assert enhanced.generated_feature_names == ["divide__age__tenure"]
    # Both were fitted and both have a blob to download.
    assert base.model_blob is not None
    assert enhanced.model_blob is not None
    assert enhanced.model_blob.artifact_id != base.model_blob.artifact_id


def test_temporal_split_keeps_its_routing_column_out_of_the_rl_feature_set(
    tmp_path: Path,
) -> None:
    """A temporal run still produces its enhanced model.

    The sidecar deliberately never sees datetime columns. Dropping the temporal
    routing column from the enhanced frame made ``train_candidates`` fail while
    deriving the same split, and the best-effort fallback silently discarded
    the enhanced model even though the search returned generated features.
    """
    state, _ = _ready_state(
        tmp_path,
        strategy=ValidationStrategy(
            strategy=SplitStrategy.TEMPORAL,
            time_column="event_time",
            holdout_cutoff="2024-04-06",
            rationale="Later observations simulate deployment after the cutoff.",
        ),
    )
    client, recorder = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    result = _run_through_training(state)

    assert "event_time" not in recorder.uploaded_csv().splitlines()[0]
    assert [type(item) for item in result.artifacts] == [
        TrainingReport,
        EnhancedTrainingReport,
    ]


def test_selection_without_any_generated_feature_adds_no_second_model(
    tmp_path: Path,
) -> None:
    """Refitting on a subset of the same columns is a different model, not an enhanced one."""
    state, _ = _ready_state(tmp_path)
    client, _ = _applicable_client(generated=[])
    state.blackboard[RLFE_CLIENT_KEY] = client

    result = _run_through_training(state)

    assert [type(item) for item in result.artifacts] == [TrainingReport]


# --------------------------------------------------------- the invariants


def test_the_model_frame_is_never_mutated(tmp_path: Path) -> None:
    """Results must not alter the data the rest of the run is built on."""
    state, frame = _ready_state(tmp_path)
    before = frame.copy(deep=True)
    client, _ = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    _run_through_training(state)

    pd.testing.assert_frame_equal(state.blackboard[MODEL_FRAME_KEY], before)
    pd.testing.assert_frame_equal(state.blackboard[ABT_FRAME_KEY], before)


def test_the_enhanced_path_adds_no_table_asset_or_feature_spec(tmp_path: Path) -> None:
    """The regression this whole design exists to prevent.

    `state.latest` falls back to newest-wins, and training re-derives its
    FeatureSpec and SplitManifest and refuses to run if either changed. A stage
    that persisted the engineered table as a TABLE_ASSET, or its routing as a
    FEATURE_SPEC, would silently rebind training's inputs and break every run
    that reached it. Emit either type from the RL stage and this goes red.
    """
    state, _ = _ready_state(tmp_path)
    client, _ = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    _run_through_training(state)

    assert len(state.store.list(state.run_id, artifact_type=ArtifactType.TABLE_ASSET)) == 1
    assert len(state.store.list(state.run_id, artifact_type=ArtifactType.FEATURE_SPEC)) == 1
    # And the primary model is still resolvable as the run's trained model.
    latest = state.latest(ArtifactType.TRAINED_MODEL, TrainingReport)
    assert latest is not None
    assert not isinstance(latest, EnhancedTrainingReport)


def test_both_models_share_one_split(tmp_path: Path) -> None:
    """Same rows, same strategy, so the two holdout scores compare like for like."""
    state, _ = _ready_state(tmp_path)
    client, _ = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client

    base, enhanced = _run_through_training(state).artifacts
    assert isinstance(enhanced, EnhancedTrainingReport)

    assert enhanced.holdout_row_count == base.holdout_row_count
    assert enhanced.outer_train_row_count == base.outer_train_row_count
    assert enhanced.holdout_rows_used_for_fit == 0


def test_feature_spec_still_matches_after_the_stage_runs(tmp_path: Path) -> None:
    """Training's own consistency check must still pass with the new stage present."""
    state, _ = _ready_state(tmp_path)
    client, _ = _applicable_client()
    state.blackboard[RLFE_CLIENT_KEY] = client
    for artifact in rl_feature_engineering_stage(state).artifacts:
        state.put(artifact, stage_id="rl_feature_engineering", name="rl_feature_report")

    spec = state.latest(ArtifactType.FEATURE_SPEC, FeatureSpec)
    assert spec is not None
    # Would raise "FeatureSpec does not match the current model-frame schema".
    training_stage(state)
