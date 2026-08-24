"""Consumer-neutral, row-free measurements addressable by stable references.

A MeasurementRecord says only what was measured and where the value came from.
It deliberately contains no interpretation prose, claim, protocol, acceptance
criterion, or consumer-specific metadata. Comprehension citations and assurance
ledger entries both reference this primitive rather than redefining evidence.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, JsonValue, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class SubjectRef(FrozenModel):
    """A table or column that a measurement is about."""

    table: str = Field(min_length=1)
    column: str | None = Field(default=None, min_length=1)


class MeasurementKind(StrEnum):
    """Closed vocabulary used to validate claim-to-measurement compatibility."""

    TABLE_PROFILE = "table_profile"
    COLUMN_PROFILE = "column_profile"
    TEXT_SHAPE = "text_shape"
    DATETIME_PROFILE = "datetime_profile"
    NUMERIC_PROFILE = "numeric_profile"
    KEY_CARDINALITY = "key_cardinality"
    RELATIONSHIP_CARDINALITY = "relationship_cardinality"
    ROWS_PER_PARENT = "rows_per_parent"
    DISTRIBUTION_SHAPE = "distribution_shape"
    PERIOD_DISTRIBUTION = "period_distribution"
    MISSINGNESS = "missingness"
    TARGET_RELATIONSHIP = "target_relationship"
    EXPLORATORY_ANALYSIS = "exploratory_analysis"
    MODEL_EXPERIMENT = "model_experiment"
    FEATURE_EXPERIMENT = "feature_experiment"


def measurement_record_id(
    *,
    source_artifact_id: str,
    field_path: str,
    kind: MeasurementKind,
    subjects: list[SubjectRef],
) -> str:
    """Return the stable identity of one field in one immutable artifact."""
    canonical = json.dumps(
        {
            "source_artifact_id": source_artifact_id,
            "field_path": field_path,
            "kind": kind.value,
            "subjects": [subject.model_dump(mode="json") for subject in subjects],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "m_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


class MeasurementRecord(FrozenModel):
    """One immutable, row-free measured value with a resolvable origin.

    The contract proves stable identity and structured provenance. Producers are
    responsible for ensuring value is aggregate and row-free; consumers must not
    infer that a compatible citation entails a claim.
    """

    measurement_id: str = Field(pattern=r"^m_[0-9a-f]{24}$")
    source_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    field_path: str = Field(pattern=r"^/")
    kind: MeasurementKind
    subjects: list[SubjectRef] = Field(min_length=1)
    value: JsonValue

    @model_validator(mode="after")
    def _stable_identity_and_subjects(self) -> MeasurementRecord:
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("Measurement subjects must be unique.")
        expected = measurement_record_id(
            source_artifact_id=self.source_artifact_id,
            field_path=self.field_path,
            kind=self.kind,
            subjects=self.subjects,
        )
        if self.measurement_id != expected:
            raise ValueError("measurement_id does not match the immutable origin.")
        return self

    @classmethod
    def create(
        cls,
        *,
        source_artifact_id: str,
        field_path: str,
        kind: MeasurementKind,
        subjects: list[SubjectRef],
        value: JsonValue,
    ) -> MeasurementRecord:
        return cls(
            measurement_id=measurement_record_id(
                source_artifact_id=source_artifact_id,
                field_path=field_path,
                kind=kind,
                subjects=subjects,
            ),
            source_artifact_id=source_artifact_id,
            field_path=field_path,
            kind=kind,
            subjects=subjects,
            value=value,
        )


class MeasurementBundle(Artifact):
    """Persisted catalog shared by interpretation and later assurance ledgers."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.MEASUREMENT_BUNDLE
    schema_version: ClassVar[str] = "1"

    scope: str = Field(min_length=1)
    records: list[MeasurementRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_measurements(self) -> MeasurementBundle:
        ids = [record.measurement_id for record in self.records]
        if len(set(ids)) != len(ids):
            raise ValueError("Measurement ids must be unique within a bundle.")
        return self

    def by_id(self) -> dict[str, MeasurementRecord]:
        return {record.measurement_id: record for record in self.records}

    def summary(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "n_measurements": len(self.records),
            "kinds": sorted({record.kind.value for record in self.records}),
            "tables": sorted(
                {subject.table for record in self.records for subject in record.subjects}
            ),
        }


def pointer_escape(value: str) -> str:
    """Escape one component for an RFC 6901 JSON pointer."""
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "MeasurementBundle",
    "MeasurementKind",
    "MeasurementRecord",
    "SubjectRef",
    "measurement_record_id",
    "pointer_escape",
]
