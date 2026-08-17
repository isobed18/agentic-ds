"""Build persisted, row-free measurement catalogs from typed artifacts."""

from __future__ import annotations

import json

from ads.contracts.datacard import DataCard
from ads.contracts.eda import EDAReport
from ads.contracts.evidence import (
    MeasurementBundle,
    MeasurementKind,
    MeasurementRecord,
    SubjectRef,
)
from ads.contracts.integration import IntegrationPlan
from ads.store import compute_artifact_id


def _record(
    *,
    artifact_id: str,
    path: str,
    kind: MeasurementKind,
    subjects: list[SubjectRef],
    value: object,
) -> MeasurementRecord:
    return MeasurementRecord.create(
        source_artifact_id=artifact_id,
        field_path=path,
        kind=kind,
        subjects=subjects,
        value=value,
    )


def datacard_measurements(card: DataCard) -> list[MeasurementRecord]:
    """Project a DataCard without samples or source values."""
    artifact_id = compute_artifact_id(card)
    table_subject = SubjectRef(table=card.table_name)
    records = [
        _record(
            artifact_id=artifact_id,
            path="/",
            kind=MeasurementKind.TABLE_PROFILE,
            subjects=[table_subject],
            value={
                "table": card.table_name,
                "n_rows": card.n_rows,
                "n_columns": card.n_columns,
                "sampled": card.sampled,
                "profiled_rows": card.profiled_rows,
                "candidate_primary_keys": card.candidate_primary_keys,
            },
        )
    ]
    for index, column in enumerate(card.columns):
        subject = SubjectRef(table=card.table_name, column=column.name)
        path = f"/columns/{index}"
        records.append(
            _record(
                artifact_id=artifact_id,
                path=path,
                kind=MeasurementKind.COLUMN_PROFILE,
                subjects=[subject],
                value={
                    "name": column.name,
                    "dtype": column.dtype,
                    "semantic_type": column.semantic_type.value,
                    "sensitivity": column.sensitivity.value,
                    "null_count": column.null_count,
                    "null_rate": column.null_rate,
                    "n_unique": column.n_unique,
                    "unique_rate": column.unique_rate,
                    "is_unique": column.is_unique,
                    "notes": column.notes,
                },
            )
        )
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"{path}/null_rate",
                kind=MeasurementKind.MISSINGNESS,
                subjects=[subject],
                value={
                    "null_count": column.null_count,
                    "null_rate": column.null_rate,
                    "row_count": card.n_rows,
                },
            )
        )
        if column.numeric is not None:
            records.append(
                _record(
                    artifact_id=artifact_id,
                    path=f"{path}/numeric",
                    kind=MeasurementKind.NUMERIC_PROFILE,
                    subjects=[subject],
                    value=column.numeric.model_dump(mode="json"),
                )
            )
        if column.datetime is not None:
            records.append(
                _record(
                    artifact_id=artifact_id,
                    path=f"{path}/datetime",
                    kind=MeasurementKind.DATETIME_PROFILE,
                    subjects=[subject],
                    value=column.datetime.model_dump(mode="json"),
                )
            )
        if column.text is not None:
            records.append(
                _record(
                    artifact_id=artifact_id,
                    path=f"{path}/text",
                    kind=MeasurementKind.TEXT_SHAPE,
                    subjects=[subject],
                    value=column.text.model_dump(mode="json"),
                )
            )
    for index, columns in enumerate(card.candidate_primary_keys):
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"/candidate_primary_keys/{index}",
                kind=MeasurementKind.KEY_CARDINALITY,
                subjects=[
                    SubjectRef(table=card.table_name, column=column)
                    for column in columns
                ],
                value={"columns": columns, "is_unique": True, "null_rate": 0.0},
            )
        )
    return records


def integration_measurements(plan: IntegrationPlan) -> list[MeasurementRecord]:
    """Project relationship evidence, including both tables as subjects."""
    artifact_id = compute_artifact_id(plan)
    records: list[MeasurementRecord] = []
    for index, relationship in enumerate(plan.evidence):
        subjects = [
            *(
                SubjectRef(table=relationship.from_table, column=column)
                for column in relationship.from_columns
            ),
            *(
                SubjectRef(table=relationship.to_table, column=column)
                for column in relationship.to_columns
            ),
        ]
        path = f"/evidence/{index}"
        records.append(
            _record(
                artifact_id=artifact_id,
                path=path,
                kind=MeasurementKind.RELATIONSHIP_CARDINALITY,
                subjects=subjects,
                value={
                    "overlap_rate": relationship.overlap_rate,
                    "orphan_rate": relationship.orphan_rate,
                    "distinct_overlap_rate": relationship.distinct_overlap_rate,
                    "parent_coverage": relationship.parent_coverage,
                    "n_from_distinct": relationship.n_from_distinct,
                    "n_to_distinct": relationship.n_to_distinct,
                    "cardinality": relationship.cardinality.value,
                },
            )
        )
        if relationship.rows_per_parent is not None:
            records.append(
                _record(
                    artifact_id=artifact_id,
                    path=f"{path}/rows_per_parent",
                    kind=MeasurementKind.ROWS_PER_PARENT,
                    subjects=subjects,
                    value=relationship.rows_per_parent.model_dump(mode="json"),
                )
            )
    return records


def eda_measurements(report: EDAReport) -> list[MeasurementRecord]:
    artifact_id = compute_artifact_id(report)
    records: list[MeasurementRecord] = []
    for index, shape in enumerate(report.numeric_distributions):
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"/numeric_distributions/{index}",
                kind=MeasurementKind.DISTRIBUTION_SHAPE,
                subjects=[SubjectRef(table="abt", column=shape.column)],
                value=shape.model_dump(mode="json"),
            )
        )
    for index, period in enumerate(report.datetime_distributions):
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"/datetime_distributions/{index}",
                kind=MeasurementKind.PERIOD_DISTRIBUTION,
                subjects=[SubjectRef(table="abt", column=period.column)],
                value=period.model_dump(mode="json"),
            )
        )
    for index, missing in enumerate(report.missingness):
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"/missingness/{index}",
                kind=MeasurementKind.MISSINGNESS,
                subjects=[SubjectRef(table="abt", column=missing.column)],
                value=missing.model_dump(mode="json"),
            )
        )
    for index, relationship in enumerate(report.target_relationships):
        subjects = [SubjectRef(table="abt", column=relationship.column)]
        if report.target_column is not None:
            subjects.append(SubjectRef(table="abt", column=report.target_column))
        records.append(
            _record(
                artifact_id=artifact_id,
                path=f"/target_relationships/{index}",
                kind=MeasurementKind.TARGET_RELATIONSHIP,
                subjects=subjects,
                value=relationship.model_dump(mode="json"),
            )
        )
    return records


def source_measurement_bundle(
    cards: list[DataCard],
    plan: IntegrationPlan,
) -> MeasurementBundle:
    return MeasurementBundle(
        scope="source",
        records=[
            *(record for card in cards for record in datacard_measurements(card)),
            *integration_measurements(plan),
        ],
    )


def analysis_measurement_bundle(
    card: DataCard,
    report: EDAReport,
) -> MeasurementBundle:
    return MeasurementBundle(
        scope="analysis",
        records=[*datacard_measurements(card), *eda_measurements(report)],
    )


_KIND_PRIORITY = {
    MeasurementKind.RELATIONSHIP_CARDINALITY: 0,
    MeasurementKind.ROWS_PER_PARENT: 1,
    MeasurementKind.KEY_CARDINALITY: 2,
    MeasurementKind.DISTRIBUTION_SHAPE: 3,
    MeasurementKind.PERIOD_DISTRIBUTION: 4,
    MeasurementKind.TEXT_SHAPE: 5,
    MeasurementKind.DATETIME_PROFILE: 6,
    MeasurementKind.TABLE_PROFILE: 7,
    MeasurementKind.COLUMN_PROFILE: 8,
    MeasurementKind.NUMERIC_PROFILE: 9,
    MeasurementKind.MISSINGNESS: 10,
    MeasurementKind.TARGET_RELATIONSHIP: 11,
    MeasurementKind.EXPLORATORY_ANALYSIS: 12,
    MeasurementKind.MODEL_EXPERIMENT: 13,
    MeasurementKind.FEATURE_EXPERIMENT: 14,
}


def measurement_digest(
    bundle: MeasurementBundle,
    *,
    max_records: int = 160,
) -> tuple[str, dict[str, MeasurementRecord]]:
    """Render a bounded row-free catalog and return exactly the visible records."""
    ordered = sorted(
        bundle.records,
        key=lambda record: (
            _KIND_PRIORITY[record.kind],
            tuple((subject.table, subject.column or "") for subject in record.subjects),
            record.measurement_id,
        ),
    )[:max_records]
    lines = []
    for record in ordered:
        subjects = ",".join(
            f"{subject.table}.{subject.column}" if subject.column else subject.table
            for subject in record.subjects
        )
        value = json.dumps(record.value, sort_keys=True, separators=(",", ":"))
        lines.append(
            f"{record.measurement_id} kind={record.kind.value} "
            f"subjects=[{subjects}] value={value}"
        )
    return "\n".join(lines), {record.measurement_id: record for record in ordered}


__all__ = [
    "analysis_measurement_bundle",
    "datacard_measurements",
    "eda_measurements",
    "integration_measurements",
    "measurement_digest",
    "source_measurement_bundle",
]
