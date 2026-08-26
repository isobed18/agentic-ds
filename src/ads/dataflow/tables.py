"""Persist and load immutable Parquet-backed :class:`TableAsset` values."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from ads.contracts.dataflow import BlobReference, TableAsset, TableColumn, TableProvenance
from ads.store import ArtifactRef, ArtifactStore, compute_artifact_id

_TABLE_FILENAME = "table.parquet"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def persist_table_asset(
    store: ArtifactStore,
    frame: pd.DataFrame,
    *,
    run_id: str,
    producer_component_id: str,
    stage_exec_id: str | None = None,
    name: str | None = None,
    source_artifact_ids: Sequence[str] = (),
    source_uris: Sequence[str] = (),
    transformation: str | None = None,
    sensitivity: Mapping[str, Sequence[str]] | None = None,
) -> tuple[TableAsset, ArtifactRef]:
    """Write one ordered frame as an immutable blob plus typed metadata."""
    with TemporaryDirectory(prefix="ads-table-") as temporary:
        temporary_path = Path(temporary) / _TABLE_FILENAME
        frame.to_parquet(temporary_path, index=False)
        fingerprint = _sha256(temporary_path)
        columns = [
            TableColumn(
                name=str(column),
                physical_type=str(frame[column].dtype),
                nullable=bool(frame[column].isna().any()),
                sensitivity=list((sensitivity or {}).get(str(column), ())),
            )
            for column in frame.columns
        ]
        asset = TableAsset(
            fingerprint=fingerprint,
            row_count=len(frame),
            column_count=len(frame.columns),
            blob=BlobReference(
                filename=_TABLE_FILENAME,
                sha256=fingerprint,
                size_bytes=temporary_path.stat().st_size,
            ),
            columns=columns,
            provenance=TableProvenance(
                producer_component_id=producer_component_id,
                source_artifact_ids=list(source_artifact_ids),
                source_uris=list(source_uris),
                transformation=transformation,
            ),
        )
        artifact_id = compute_artifact_id(asset)
        destination = store.blob_dir(artifact_id) / _TABLE_FILENAME
        if destination.exists():
            if _sha256(destination) != fingerprint:
                raise ValueError(f"existing TableAsset blob checksum mismatch for {artifact_id}")
        else:
            staging = destination.with_suffix(".parquet.tmp")
            staging.write_bytes(temporary_path.read_bytes())
            staging.replace(destination)
        reference = store.put(
            asset,
            run_id=run_id,
            stage_exec_id=stage_exec_id or producer_component_id,
            name=name,
        )
    return asset, reference


def load_table_asset(store: ArtifactStore, artifact_id: str) -> tuple[TableAsset, pd.DataFrame]:
    """Verify the content-addressed blob before returning a fresh DataFrame."""
    asset = store.load(artifact_id, TableAsset)
    path = store.blob_dir(artifact_id) / asset.blob.filename
    if not path.exists():
        raise FileNotFoundError(f"TableAsset {artifact_id} has no blob {asset.blob.filename!r}")
    if path.stat().st_size != asset.blob.size_bytes or _sha256(path) != asset.blob.sha256:
        raise ValueError(f"TableAsset {artifact_id} blob failed integrity verification")
    frame = pd.read_parquet(path)
    if len(frame) != asset.row_count or len(frame.columns) != asset.column_count:
        raise ValueError(f"TableAsset {artifact_id} shape does not match its contract")
    return asset, frame
