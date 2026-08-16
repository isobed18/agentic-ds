"""Fitted model persistence, provenance, and process-boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ads.contracts import SplitStrategy, TaskType, ValidationStrategy
from ads.store import ArtifactStore
from ads.training import default_candidates, train_candidates
from ads.training.persistence import (
    PROVENANCE_FILENAME,
    attach_training_provenance,
    hash_training_frame,
    load_model,
    save_model,
)


def _strategy() -> ValidationStrategy:
    return ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        n_folds=3,
        test_size=0.2,
        rationale="Seeded independent rows.",
    )


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(4102)
    features = rng.normal(size=(120, 3))
    frame = pd.DataFrame(features, columns=["x0", "x1", "x2"])
    frame["target"] = 3.0 * frame["x0"] - 1.5 * frame["x1"] + 0.2 * frame["x2"]
    return frame


def _subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, existing]))
    return environment


def test_save_load_round_trip_has_provenance_and_identical_predictions(tmp_path: Path) -> None:
    frame = _frame()
    features = frame.drop(columns=["target"])
    pipeline = Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=1.0))])
    pipeline.fit(features, frame["target"])
    strategy = _strategy()
    attach_training_provenance(pipeline, frame, strategy)

    store_root = tmp_path / "artifacts"
    store = ArtifactStore(store_root)
    artifact_id = hashlib.sha256(b"persistence-round-trip").hexdigest()
    model_path = save_model(
        pipeline,
        store,
        run_id="round-trip-run",
        artifact_id=artifact_id,
    )

    assert model_path == store.blob_dir(artifact_id) / "model.joblib"
    provenance = json.loads(
        (store.blob_dir(artifact_id) / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    )
    assert provenance["sklearn_version"] == sklearn.__version__
    assert provenance["python_version"]
    assert provenance["training_frame_hash"] == hash_training_frame(frame)
    assert provenance["validation_strategy"]["strategy"] == "random"

    prediction_rows = features.iloc[:9]
    expected = pipeline.predict(prediction_rows)
    input_path = tmp_path / "prediction_rows.joblib"
    joblib.dump(prediction_rows, input_path)
    code = """
import json
import sys
import joblib
from ads.store import ArtifactStore
from ads.training.persistence import load_model

model = load_model(ArtifactStore(sys.argv[1]), sys.argv[2])
rows = joblib.load(sys.argv[3])
print(json.dumps(model.predict(rows).tolist()))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(store_root), artifact_id, str(input_path)],
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )

    assert completed.returncode == 0, completed.stderr
    actual = np.asarray(json.loads(completed.stdout))
    np.testing.assert_array_equal(actual, expected)


def test_repeated_identity_reuses_blob_and_records_each_run(tmp_path: Path) -> None:
    """A retry may produce the same content address; that is an idempotent save."""
    frame = _frame()
    pipeline = Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=1.0))])
    pipeline.fit(frame.drop(columns=["target"]), frame["target"])
    attach_training_provenance(pipeline, frame, _strategy())

    store = ArtifactStore(tmp_path / "artifacts")
    artifact_id = hashlib.sha256(b"same-model-across-attempts").hexdigest()
    first_path = save_model(
        pipeline,
        store,
        run_id="critique-attempt-1",
        artifact_id=artifact_id,
    )
    first_bytes = first_path.read_bytes()
    first_provenance = (store.blob_dir(artifact_id) / PROVENANCE_FILENAME).read_bytes()

    second_path = save_model(
        pipeline,
        store,
        run_id="critique-attempt-2",
        artifact_id=artifact_id,
    )

    assert second_path == first_path
    assert second_path.read_bytes() == first_bytes
    assert (store.blob_dir(artifact_id) / PROVENANCE_FILENAME).read_bytes() == first_provenance
    run_markers = sorted(store.blob_dir(artifact_id).glob("run-*.json"))
    assert len(run_markers) == 2
    assert {json.loads(path.read_text(encoding="utf-8"))["run_id"] for path in run_markers} == {
        "critique-attempt-1",
        "critique-attempt-2",
    }


def test_train_candidates_persists_only_when_store_and_run_are_supplied(tmp_path: Path) -> None:
    frame = _frame()
    strategy = _strategy()
    without_store = train_candidates(
        frame,
        strategy,
        StandardScaler,
        default_candidates(TaskType.REGRESSION)[:2],
        target_column="target",
        task_type=TaskType.REGRESSION,
    )
    assert without_store.model_blob is None

    store = ArtifactStore(tmp_path / "artifacts")
    persisted = train_candidates(
        frame,
        strategy,
        StandardScaler,
        default_candidates(TaskType.REGRESSION)[:2],
        target_column="target",
        task_type=TaskType.REGRESSION,
        store=store,
        run_id="training-run",
    )

    assert persisted.model_blob is not None
    loaded = load_model(store, persisted.model_blob.artifact_id)
    predictions = loaded.predict(frame.drop(columns=["target"]).iloc[:5])
    assert np.isfinite(predictions).all()
