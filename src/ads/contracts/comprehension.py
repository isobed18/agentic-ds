"""Contracts for proposed interpretations bound to deterministic measurements.

Validation can prove that an interpretation is about its cited subjects and that
the cited measurement kind is compatible with the interpretation kind. It does
not prove entailment. A confidently wrong reading of a correctly cited, correctly
typed measurement can pass every check and therefore remains proposed with an
explicit unknown verification question.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum, StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.evidence import MeasurementRecord, SubjectRef


class InterpretationKind(StrEnum):
    ROW_GRAIN = "row_grain"
    TABLE_PURPOSE = "table_purpose"
    RELATIONSHIP = "relationship"
    DISTRIBUTION_PATTERN = "distribution_pattern"
    COLUMN_SEMANTICS = "column_semantics"
    OPEN_QUESTION = "open_question"


class ModelConfidence(Enum):
    """Model self-assessment only; intentionally non-orderable."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ComprehensionScope(StrEnum):
    SOURCE = "source"
    ANALYSIS = "analysis"


class InterpretationProposal(FrozenModel):
    """What the model may author; observations and epistemic state are excluded."""

    kind: InterpretationKind
    subjects: list[SubjectRef] = Field(min_length=1)
    measurement_ids: list[str] = Field(min_length=1, max_length=4)
    interpretation: str = Field(min_length=10, max_length=800)
    why_it_matters: str = Field(min_length=10, max_length=800)
    #: The same interpretation in Turkish. Written in the same pass rather than
    #: translated afterwards: the agent already holds the measurements and the
    #: reasoning, and a later translation of composed prose loses the thread
    #: between a sentence and the number it came from. Optional because a model
    #: that cannot produce it should still deliver the English half rather than
    #: failing validation and leaving the reader with nothing.
    interpretation_tr: str | None = Field(default=None, max_length=800)
    why_it_matters_tr: str | None = Field(default=None, max_length=800)
    verification_question: str = Field(
        min_length=10,
        max_length=500,
        description="Mandatory human check because every model interpretation is proposed.",
    )
    verification_question_tr: str | None = Field(default=None, max_length=500)
    confidence: ModelConfidence = Field(
        description=(
            "Model self-assessment only. It is not calibrated evidence and must never "
            "drive gates, ranking, filtering, or suppression."
        )
    )

    @model_validator(mode="after")
    def _unique_references(self) -> InterpretationProposal:
        if len(set(self.subjects)) != len(self.subjects):
            raise ValueError("Interpretation subjects must be unique.")
        if len(set(self.measurement_ids)) != len(self.measurement_ids):
            raise ValueError("Measurement references must be unique.")
        return self


class InterpretationBatchProposal(FrozenModel):
    items: list[InterpretationProposal] = Field(default_factory=list, max_length=8)


class VerificationQuestion(FrozenModel):
    question: str = Field(min_length=10, max_length=500)
    epistemic_state: Literal["unknown"] = "unknown"


class InterpretationItem(FrozenModel):
    """Persisted proposed interpretation with executor-resolved observations."""

    interpretation_id: str = Field(pattern=r"^i_[0-9a-f]{24}$")
    kind: InterpretationKind
    subjects: list[SubjectRef] = Field(min_length=1)
    citations: list[MeasurementRecord] = Field(min_length=1, max_length=4)
    interpretation: str
    why_it_matters: str
    #: Turkish alongside English rather than instead of it. The reader's
    #: language is chosen per request and can change between two people looking
    #: at the same run, so both are stored and the edge picks one.
    interpretation_tr: str | None = None
    why_it_matters_tr: str | None = None
    verification: VerificationQuestion
    verification_question_tr: str | None = None
    confidence: ModelConfidence
    epistemic_state: Literal["proposed"] = "proposed"

    @classmethod
    def from_proposal(
        cls,
        proposal: InterpretationProposal,
        measurements: dict[str, MeasurementRecord],
    ) -> InterpretationItem:
        citations = [measurements[item] for item in proposal.measurement_ids]
        canonical = json.dumps(
            {
                "kind": proposal.kind.value,
                "subjects": [item.model_dump(mode="json") for item in proposal.subjects],
                "measurement_ids": proposal.measurement_ids,
                "interpretation": proposal.interpretation,
                "why_it_matters": proposal.why_it_matters,
                "verification_question": proposal.verification_question,
                "confidence": proposal.confidence.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            interpretation_id="i_" + hashlib.sha256(canonical.encode()).hexdigest()[:24],
            kind=proposal.kind,
            subjects=proposal.subjects,
            citations=citations,
            interpretation=proposal.interpretation,
            # Deliberately outside the canonical payload above: a translation of
            # the same interpretation is the same interpretation, so adding it
            # would give one finding two different content-addressed ids.
            interpretation_tr=proposal.interpretation_tr,
            why_it_matters_tr=proposal.why_it_matters_tr,
            verification_question_tr=proposal.verification_question_tr,
            why_it_matters=proposal.why_it_matters,
            verification=VerificationQuestion(question=proposal.verification_question),
            confidence=proposal.confidence,
        )


class ComprehensionBrief(Artifact):
    """Bounded advisory output; empty and degraded is a valid non-blocking result."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.COMPREHENSION_BRIEF
    schema_version: ClassVar[str] = "1"

    scope: ComprehensionScope
    measurement_bundle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[InterpretationItem] = Field(default_factory=list, max_length=8)
    degraded: bool = False
    degradation_reason: str | None = None

    @model_validator(mode="after")
    def _degradation_is_honest(self) -> ComprehensionBrief:
        if self.degraded and not self.degradation_reason:
            raise ValueError("A degraded brief must explain why interpretation is absent.")
        if not self.degraded and self.degradation_reason is not None:
            raise ValueError("A healthy brief cannot carry a degradation reason.")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "scope": self.scope.value,
            "n_interpretations": len(self.items),
            "n_unknown_questions": len(self.items),
            "degraded": self.degraded,
        }


__all__ = [
    "ComprehensionBrief",
    "ComprehensionScope",
    "InterpretationBatchProposal",
    "InterpretationItem",
    "InterpretationKind",
    "InterpretationProposal",
    "ModelConfidence",
    "VerificationQuestion",
]
