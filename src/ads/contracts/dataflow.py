"""Durable graph-native dataflow contracts.

Frames and fitted runtime objects never cross a graph edge.  A table edge carries
an immutable :class:`TableAsset` whose rows live in a content-addressed Parquet
blob.  A split edge carries exact row positions against one exact table asset.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class BlobReference(FrozenModel):
    filename: str = Field(pattern=r"^[a-zA-Z0-9_.-]+$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class TableColumn(FrozenModel):
    name: str = Field(min_length=1)
    physical_type: str = Field(min_length=1)
    logical_type: str | None = None
    nullable: bool
    sensitivity: list[str] = Field(default_factory=list)


class TableProvenance(FrozenModel):
    producer_component_id: str
    source_artifact_ids: list[str] = Field(default_factory=list)
    source_uris: list[str] = Field(default_factory=list)
    transformation: str | None = None


class TableAsset(Artifact):
    """Metadata handle for one immutable, ordered Parquet table."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.TABLE_ASSET
    schema_version: ClassVar[str] = "1"

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    format: Literal["parquet"] = "parquet"
    blob: BlobReference
    columns: list[TableColumn]
    provenance: TableProvenance

    @model_validator(mode="after")
    def _schema_matches_shape(self) -> TableAsset:
        if self.column_count != len(self.columns):
            raise ValueError("column_count must match the persisted table schema")
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("table column names must be unique")
        return self

    def summary(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "rows": self.row_count,
            "columns": self.column_count,
            "format": self.format,
            "producer_component_id": self.provenance.producer_component_id,
        }


class RowSelection(FrozenModel):
    """Exact zero-based row positions in the ordered input TableAsset."""

    count: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    positions: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _positions_are_valid(self) -> RowSelection:
        if self.count != len(self.positions):
            raise ValueError("row selection count must match positions")
        if any(position < 0 for position in self.positions):
            raise ValueError("row positions must be non-negative")
        if len(self.positions) != len(set(self.positions)):
            raise ValueError("row positions must be unique")
        return self


class FoldSelection(FrozenModel):
    fold: int = Field(ge=0)
    train: RowSelection
    validation: RowSelection


class SplitManifest(Artifact):
    """Exact, restart-safe outer holdout and inner-fold selections."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.SPLIT_MANIFEST
    schema_version: ClassVar[str] = "1"

    table_asset_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_strategy_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_column: str
    outer_train: RowSelection
    holdout: RowSelection
    folds: list[FoldSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def _partitions_are_consistent(self) -> SplitManifest:
        outer = set(self.outer_train.positions)
        holdout = set(self.holdout.positions)
        if outer & holdout:
            raise ValueError("outer training and holdout rows must be disjoint")
        for fold in self.folds:
            train = set(fold.train.positions)
            validation = set(fold.validation.positions)
            if train & validation:
                raise ValueError(f"fold {fold.fold} training and validation rows overlap")
            if not train | validation <= outer:
                raise ValueError(f"fold {fold.fold} references rows outside outer training")
        return self

    def summary(self) -> dict[str, object]:
        return {
            "table_asset_id": self.table_asset_id,
            "outer_train_rows": self.outer_train.count,
            "holdout_rows": self.holdout.count,
            "folds": len(self.folds),
            "target_column": self.target_column,
        }


__all__ = [
    "BlobReference",
    "FoldSelection",
    "RowSelection",
    "SplitManifest",
    "TableAsset",
    "TableColumn",
    "TableProvenance",
]
