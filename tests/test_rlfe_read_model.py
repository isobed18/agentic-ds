"""How a run's two models reach the models list, and how they leave it.

One run produces one training outcome. The list keeps one row for it, carrying
the RL-enhanced variant as a second download, because two rows would read as two
unrelated models rather than two files from the same result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads.api.service import ControlPlane, RunSummary
from ads.contracts import ArtifactType, Metric, TaskType
from ads.contracts.training import (
    CandidateResult,
    EnhancedTrainingReport,
    MetricEvaluation,
    ModelBlobReference,
    TrainingReport,
)
from ads.store import ArtifactStore, compute_artifact_id

RUN_ID = "models-read-model"
_BLOB = "c" * 64


def _evaluation(holdout: float) -> MetricEvaluation:
    return MetricEvaluation(
        metric=Metric.RMSE,
        fold_scores=[holdout, holdout],
        cv_mean=holdout,
        cv_std=0.0,
        holdout_score=holdout,
    )


def _report(holdout: float, *, winner: str = "ridge") -> TrainingReport:
    return TrainingReport(
        task_type=TaskType.REGRESSION,
        primary_metric=Metric.RMSE,
        results=[
            CandidateResult(
                candidate_id="baseline",
                display_name="Baseline",
                estimator_class="DummyRegressor",
                is_baseline=True,
                metrics=[_evaluation(9.0)],
            ),
            CandidateResult(
                candidate_id=winner,
                display_name=winner.title(),
                estimator_class="Ridge",
                metrics=[_evaluation(holdout)],
            ),
        ],
        winner_id=winner,
        model_blob=ModelBlobReference(artifact_id=_BLOB),
    )


def _plane(tmp_path: Path) -> ControlPlane:
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(),
        upload_root=tmp_path / "uploads",
    )


def _summary() -> RunSummary:
    return RunSummary(
        run_id=RUN_ID,
        artifact_count=0,
        stages=[],
        status="completed",
        last_activity="2026-01-01T00:00:00Z",
    )


def _persist(plane: ControlPlane, base: TrainingReport, enhanced_holdout: float | None):
    base_id = compute_artifact_id(base)
    plane.store.put(base, run_id=RUN_ID, stage_exec_id="training", name="training_report")
    if enhanced_holdout is None:
        return base_id, None
    enhanced = EnhancedTrainingReport(
        **base.model_dump(exclude={"created_at"}),
        base_model_artifact_id=base_id,
        rl_feature_report_id="d" * 64,
        generated_feature_names=["divide__a__b"],
    )
    enhanced = enhanced.model_copy(
        update={
            "results": [
                enhanced.results[0],
                enhanced.results[1].model_copy(
                    update={"metrics": [_evaluation(enhanced_holdout)]}
                ),
            ]
        }
    )
    enhanced_id = compute_artifact_id(enhanced)
    plane.store.put(
        enhanced, run_id=RUN_ID, stage_exec_id="training", name="rl_enhanced_training_report"
    )
    return base_id, enhanced_id


def test_one_row_carries_both_models(tmp_path: Path) -> None:
    """The counter-test for two cards.

    Walk RL_ENHANCED_MODEL as its own row in `_model_summaries` and this reports
    two models for a run that trained once.
    """
    plane = _plane(tmp_path)
    base_id, enhanced_id = _persist(plane, _report(4.0), enhanced_holdout=3.0)

    models = plane._model_summaries(_summary())

    assert len(models) == 1
    row = models[0]
    assert row["artifact_id"] == base_id
    assert row["enhanced"]["artifact_id"] == enhanced_id
    assert row["enhanced"]["generated_feature_count"] == 1
    assert row["enhanced"]["saved"] is True


def test_a_lower_is_better_metric_reports_improvement_as_positive(tmp_path: Path) -> None:
    """RMSE falling is an improvement. The card colours on this sign."""
    plane = _plane(tmp_path)
    _persist(plane, _report(4.0), enhanced_holdout=3.0)

    row = plane._model_summaries(_summary())[0]

    assert row["enhanced"]["score_delta"] == pytest.approx(1.0)


def test_a_worse_enhanced_model_reports_a_negative_delta(tmp_path: Path) -> None:
    """It is still offered for download, and it still says it lost."""
    plane = _plane(tmp_path)
    _persist(plane, _report(4.0), enhanced_holdout=6.0)

    row = plane._model_summaries(_summary())[0]

    assert row["enhanced"]["score_delta"] == pytest.approx(-2.0)


def test_a_run_without_an_enhanced_model_has_none(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    _persist(plane, _report(4.0), enhanced_holdout=None)

    row = plane._model_summaries(_summary())[0]

    assert row["enhanced"] is None


def test_deleting_the_model_takes_its_enhanced_counterpart(tmp_path: Path) -> None:
    """The two share one card and one delete control.

    Leave the enhanced artifact behind and it outlives the only affordance that
    could remove it -- a model nobody can see and nobody can delete.
    """
    plane = _plane(tmp_path)
    base_id, enhanced_id = _persist(plane, _report(4.0), enhanced_holdout=3.0)
    assert enhanced_id is not None

    plane.delete_model(base_id)

    assert plane.store.list_all(ArtifactType.RL_ENHANCED_MODEL) == []
    assert plane._model_summaries(_summary()) == []


def test_the_enhanced_model_is_downloadable_by_its_own_id(tmp_path: Path) -> None:
    """No new route: the existing one reads `model_blob` off whatever it is given."""
    plane = _plane(tmp_path)
    _, enhanced_id = _persist(plane, _report(4.0), enhanced_holdout=3.0)
    assert enhanced_id is not None
    blob_dir = plane.store.blob_dir(_BLOB)
    blob_dir.mkdir(parents=True, exist_ok=True)
    (blob_dir / "model.joblib").write_bytes(b"fitted-pipeline")

    data, filename = plane.model_download(enhanced_id)

    assert data == b"fitted-pipeline"
    assert filename == "model.joblib"
