"""Counter-tests for measurement provenance and proposed interpretations."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.agents import AgentContext
from ads.agents.interpretation import validate_measurement_binding
from ads.contracts.comprehension import (
    InterpretationBatchProposal,
    InterpretationItem,
    InterpretationKind,
    InterpretationProposal,
    ModelConfidence,
)
from ads.contracts.evidence import (
    MeasurementBundle,
    MeasurementKind,
    MeasurementRecord,
    SubjectRef,
)
from ads.discovery.measurements import datacard_measurements, measurement_digest
from ads.intake import LoadedTable, profile_table

ARTIFACT_ID = "a" * 64


def test_measurement_digest_accepts_every_declared_measurement_kind() -> None:
    bundle = MeasurementBundle(
        scope="enum-coverage",
        records=[
            _measurement(kind, [SubjectRef(table="events")], path=f"/{kind.value}")
            for kind in MeasurementKind
        ],
    )

    _rendered, visible = measurement_digest(bundle)

    assert {record.kind for record in visible.values()} == set(MeasurementKind)


def _measurement(
    kind: MeasurementKind,
    subjects: list[SubjectRef],
    *,
    path: str = "/measured",
) -> MeasurementRecord:
    return MeasurementRecord.create(
        source_artifact_id=ARTIFACT_ID,
        field_path=path,
        kind=kind,
        subjects=subjects,
        value={"count": 12, "rate": 0.5},
    )


def _proposal(
    measurement: MeasurementRecord,
    *,
    kind: InterpretationKind,
    subjects: list[SubjectRef] | None = None,
) -> InterpretationProposal:
    return InterpretationProposal(
        kind=kind,
        subjects=subjects or measurement.subjects,
        measurement_ids=[measurement.measurement_id],
        interpretation="The measured structure suggests an operational event table.",
        why_it_matters="This changes which keys and aggregations are safe for analysis.",
        verification_question="What business event creates one row in this table?",
        confidence=ModelConfidence.MEDIUM,
    )


def test_measurement_identity_is_stable_and_bound_to_origin() -> None:
    subjects = [SubjectRef(table="orders", column="order_id")]
    first = _measurement(MeasurementKind.KEY_CARDINALITY, subjects)
    repeated = _measurement(MeasurementKind.KEY_CARDINALITY, subjects)
    moved = _measurement(MeasurementKind.KEY_CARDINALITY, subjects, path="/other")

    assert first.measurement_id == repeated.measurement_id
    assert first.measurement_id != moved.measurement_id
    with pytest.raises(ValidationError, match="immutable origin"):
        MeasurementRecord(
            measurement_id=moved.measurement_id,
            source_artifact_id=first.source_artifact_id,
            field_path=first.field_path,
            kind=first.kind,
            subjects=first.subjects,
            value=first.value,
        )


def test_model_confidence_is_deliberately_non_orderable() -> None:
    with pytest.raises(TypeError):
        _ = ModelConfidence.LOW < ModelConfidence.HIGH


def test_proposal_cannot_omit_verification_or_author_an_observation() -> None:
    measured = _measurement(MeasurementKind.TABLE_PROFILE, [SubjectRef(table="orders")])
    payload = {
        **_proposal(measured, kind=InterpretationKind.TABLE_PURPOSE).model_dump(),
        "observation": "This is not a model-authored field.",
    }
    with pytest.raises(ValidationError):
        InterpretationProposal.model_validate(payload)
    payload.pop("observation")
    payload.pop("verification_question")
    with pytest.raises(ValidationError):
        InterpretationProposal.model_validate(payload)


def test_executor_resolves_citation_and_keeps_claim_proposed() -> None:
    measured = _measurement(MeasurementKind.TABLE_PROFILE, [SubjectRef(table="orders")])
    proposal = _proposal(measured, kind=InterpretationKind.TABLE_PURPOSE)
    item = InterpretationItem.from_proposal(proposal, {measured.measurement_id: measured})

    assert item.citations == [measured]
    assert item.epistemic_state == "proposed"
    assert item.verification.epistemic_state == "unknown"


def test_relationship_claim_requires_one_citation_covering_both_tables() -> None:
    orders = _measurement(
        MeasurementKind.ROWS_PER_PARENT,
        [SubjectRef(table="orders", column="customer_id")],
    )
    customers = _measurement(
        MeasurementKind.ROWS_PER_PARENT,
        [SubjectRef(table="customers", column="customer_id")],
        path="/customers",
    )
    bundle = MeasurementBundle(scope="source", records=[orders, customers])
    context = AgentContext(
        sections={},
        facts={"measurement_by_id": bundle.by_id()},
    )
    proposal = InterpretationProposal(
        kind=InterpretationKind.RELATIONSHIP,
        subjects=[SubjectRef(table="orders"), SubjectRef(table="customers")],
        measurement_ids=[orders.measurement_id, customers.measurement_id],
        interpretation="Orders appear to reference customer records through a repeated key.",
        why_it_matters="A mistaken relationship would multiply rows during integration.",
        verification_question="Does customer_id identify the intended customer entity?",
        confidence=ModelConfidence.LOW,
    )

    failures = validate_measurement_binding(InterpretationBatchProposal(items=[proposal]), context)

    assert "relationship_requires_joint_measurement" in {item.code for item in failures}


def test_wrong_kind_and_unbound_column_are_rejected() -> None:
    measured = _measurement(
        MeasurementKind.MISSINGNESS,
        [SubjectRef(table="orders", column="price")],
    )
    context = AgentContext(
        sections={}, facts={"measurement_by_id": {measured.measurement_id: measured}}
    )
    proposal = _proposal(
        measured,
        kind=InterpretationKind.ROW_GRAIN,
        subjects=[SubjectRef(table="orders", column="freight_value")],
    )

    failures = validate_measurement_binding(InterpretationBatchProposal(items=[proposal]), context)
    codes = {item.code for item in failures}
    assert codes == {"incompatible_measurement_kind", "unbound_subject"}


def test_datacard_measurements_expose_zero_rate_without_source_text() -> None:
    secret = "PRIVATE source narrative that must not become measurement evidence"
    frame = pd.DataFrame(
        {
            "amount": [0, 5] * 30,
            "comment": [f"{secret} number {index}" for index in range(60)],
        }
    )
    card = profile_table(
        LoadedTable(name="events", frame=frame, source_uri="mem", source_format="csv")
    )

    records = datacard_measurements(card)
    numeric = next(
        record
        for record in records
        if record.kind is MeasurementKind.NUMERIC_PROFILE
        and record.subjects == [SubjectRef(table="events", column="amount")]
    )
    serialized = json.dumps([record.model_dump(mode="json") for record in records], sort_keys=True)

    assert numeric.value["zero_rate"] == 0.5
    assert secret not in serialized
