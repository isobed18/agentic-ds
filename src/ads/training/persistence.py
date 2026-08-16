"""Versioned persistence for fitted scikit-learn pipelines."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import tempfile
import warnings
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import sklearn
from sklearn.exceptions import NotFittedError
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from ads.contracts.validation import ValidationStrategy
from ads.store import ArtifactStore

MODEL_FILENAME = "model.joblib"
PROVENANCE_FILENAME = "provenance.json"
_PROVENANCE_SCHEMA_VERSION = "1"
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ModelPersistenceError(RuntimeError):
    """Raised when a model blob is missing, corrupt, or lacks provenance."""


def hash_training_frame(frame: pd.DataFrame) -> str:
    """Hash ordered values, index, columns, and dtypes of a training frame."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame.")
    digest = hashlib.sha256()
    schema = json.dumps(
        {
            "columns": [str(column) for column in frame.columns],
            "dtypes": [str(dtype) for dtype in frame.dtypes],
            "index_names": [str(name) if name is not None else None for name in frame.index.names],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest.update(schema.encode("utf-8"))
    try:
        row_hashes = pd.util.hash_pandas_object(frame, index=True, categorize=False)
    except (TypeError, ValueError) as exc:
        raise ModelPersistenceError(
            "The training frame contains values that cannot be deterministically hashed."
        ) from exc
    digest.update(row_hashes.to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()


def attach_training_provenance(
    pipeline: Pipeline,
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
) -> None:
    """Attach the inputs save_model needs without changing its public signature."""
    pipeline.ads_training_frame_hash_ = hash_training_frame(frame)
    pipeline.ads_validation_strategy_ = strategy.model_dump(
        mode="json", exclude={"created_at"}
    )


def model_artifact_id(
    *,
    training_frame_hash: str,
    strategy: ValidationStrategy,
    target_column: str,
    task_type: str,
    winner_recipe: dict[str, Any],
    preprocessor_recipe: dict[str, Any],
) -> str:
    """Build the content address used for a fitted model's blob directory."""
    identity = {
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "training_frame_hash": training_frame_hash,
        "validation_strategy": strategy.model_dump(mode="json", exclude={"created_at"}),
        "target_column": target_column,
        "task_type": task_type,
        "winner_recipe": winner_recipe,
        "preprocessor_recipe": preprocessor_recipe,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"sklearn-pipeline@1\x00{canonical}".encode()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifact_id(artifact_id: str) -> None:
    if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ValueError("artifact_id must be a 64-character lowercase hexadecimal digest.")


def _write_json_once(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ModelPersistenceError(
                f"Refusing to overwrite conflicting immutable model metadata at {path}."
            )
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def save_model(
    pipeline: Pipeline,
    store: ArtifactStore,
    *,
    run_id: str,
    artifact_id: str,
) -> Path:
    """Persist a fitted pipeline and immutable provenance in its artifact blob directory."""
    if not isinstance(pipeline, Pipeline):
        raise TypeError("pipeline must be an sklearn.pipeline.Pipeline.")
    _validate_artifact_id(artifact_id)
    if not run_id.strip():
        raise ValueError("run_id must not be empty.")
    try:
        check_is_fitted(pipeline)
    except NotFittedError as exc:
        raise ModelPersistenceError("Refusing to persist an unfitted pipeline.") from exc

    frame_hash = getattr(pipeline, "ads_training_frame_hash_", None)
    strategy = getattr(pipeline, "ads_validation_strategy_", None)
    if not isinstance(frame_hash, str) or not isinstance(strategy, dict):
        raise ModelPersistenceError(
            "Pipeline provenance is missing; attach the training frame hash and "
            "ValidationStrategy before persistence."
        )

    blob_dir = store.blob_dir(artifact_id)
    model_path = blob_dir / MODEL_FILENAME
    with tempfile.NamedTemporaryFile(dir=blob_dir, suffix=".joblib.tmp", delete=False) as handle:
        temporary_model = Path(handle.name)
    try:
        joblib.dump(pipeline, temporary_model)
        model_hash = _sha256(temporary_model)
        if model_path.exists():
            if _sha256(model_path) != model_hash:
                raise ModelPersistenceError(
                    f"Artifact id {artifact_id} already refers to a different model blob."
                )
        else:
            temporary_model.replace(model_path)
    finally:
        temporary_model.unlink(missing_ok=True)

    provenance = {
        "schema_version": _PROVENANCE_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "blob_filename": MODEL_FILENAME,
        "blob_sha256": model_hash,
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "training_frame_hash": frame_hash,
        "validation_strategy": strategy,
    }
    _write_json_once(blob_dir / PROVENANCE_FILENAME, provenance)
    run_digest = hashlib.sha256(run_id.encode()).hexdigest()[:16]
    _write_json_once(blob_dir / f"run-{run_digest}.json", {"run_id": run_id})
    return model_path


def load_model(store: ArtifactStore, artifact_id: str) -> Pipeline:
    """Load a trusted fitted pipeline after verifying its sidecar and checksum."""
    _validate_artifact_id(artifact_id)
    blob_dir = store.blob_dir(artifact_id)
    model_path = blob_dir / MODEL_FILENAME
    provenance_path = blob_dir / PROVENANCE_FILENAME
    if not model_path.is_file() or not provenance_path.is_file():
        raise ModelPersistenceError(f"Model blob or provenance is missing for {artifact_id}.")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ModelPersistenceError(f"Could not read model provenance for {artifact_id}.") from exc
    if provenance.get("artifact_id") != artifact_id:
        raise ModelPersistenceError("Model provenance artifact id does not match its location.")
    if provenance.get("blob_sha256") != _sha256(model_path):
        raise ModelPersistenceError("Model blob checksum does not match its provenance.")
    recorded_sklearn = provenance.get("sklearn_version")
    if recorded_sklearn != sklearn.__version__:
        warnings.warn(
            f"Model was fitted with scikit-learn {recorded_sklearn}, but runtime is "
            f"{sklearn.__version__}; predictions may not be reproducible.",
            RuntimeWarning,
            stacklevel=2,
        )
    pipeline = joblib.load(model_path)
    if not isinstance(pipeline, Pipeline):
        raise ModelPersistenceError("Stored model is not an sklearn.pipeline.Pipeline.")
    return pipeline


__all__ = [
    "MODEL_FILENAME",
    "PROVENANCE_FILENAME",
    "ModelPersistenceError",
    "attach_training_provenance",
    "hash_training_frame",
    "load_model",
    "model_artifact_id",
    "save_model",
]
