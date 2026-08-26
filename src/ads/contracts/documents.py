"""Normalized, provenance-bearing outputs from local document engines.

Every extractor writes this contract.  The planner and UI therefore consume a
stable evidence boundary instead of depending on Docling, Unstructured, Marker,
or MinerU's private object model.  Extracted tables remain candidates until a
person accepts them; producing a table is not the same as approving training
data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel

DocumentEngineId = Literal["docling", "unstructured", "marker", "mineru", "text_layer"]


class DocumentPageContent(FrozenModel):
    page_number: int = Field(ge=1)
    markdown: str = Field(max_length=250_000)


class ExtractedTableCandidate(FrozenModel):
    candidate_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]+$")
    source_file: str
    page_number: int | None = Field(default=None, ge=1)
    title: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str | int | float | bool | None]] = Field(default_factory=list)
    markdown: str = Field(default="", max_length=500_000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: Literal["candidate", "accepted", "rejected"] = "candidate"


class ExtractedFigureCandidate(FrozenModel):
    candidate_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]+$")
    source_file: str
    page_number: int | None = Field(default=None, ge=1)
    caption: str | None = None
    kind: Literal["picture", "chart", "unknown"] = "unknown"
    image_path: str | None = None
    review_status: Literal["candidate", "accepted", "rejected"] = "candidate"


class ExtractedDocument(FrozenModel):
    source_file: str
    title: str | None = None
    page_count: int = Field(default=0, ge=0)
    markdown: str = Field(default="", max_length=5_000_000)
    pages: list[DocumentPageContent] = Field(default_factory=list)
    tables: list[ExtractedTableCandidate] = Field(default_factory=list)
    figures: list[ExtractedFigureCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(default=0.0, ge=0.0)


class DocumentFileResult(FrozenModel):
    """Truthful per-file outcome retained even when one document fails."""

    source_file: str
    status: Literal["ready", "failed"]
    page_count: int = Field(default=0, ge=0)
    table_candidates: int = Field(default=0, ge=0)
    figure_candidates: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    warnings: list[str] = Field(default_factory=list)


class DocumentExtractionSummary(FrozenModel):
    artifact_id: str | None = None
    engine: DocumentEngineId
    engine_version: str | None = None
    ocr_mode: Literal["auto", "always", "never"] = "auto"
    status: Literal["ready", "failed"]
    document_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    text_characters: int = Field(ge=0)
    table_candidates: int = Field(ge=0)
    figure_candidates: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    files: list[DocumentFileResult] = Field(default_factory=list)


class DocumentExtraction(Artifact):
    """Complete local extraction output; raw content stays in the artifact store."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.DOCUMENT_EXTRACTION
    schema_version: ClassVar[str] = "1"

    source_id: str
    source_fingerprint: str
    engine: DocumentEngineId
    engine_version: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    documents: list[ExtractedDocument] = Field(default_factory=list)
    file_results: list[DocumentFileResult] = Field(default_factory=list)
    duration_seconds: float = Field(ge=0.0)
    warnings: list[str] = Field(default_factory=list)

    def extraction_summary(self) -> DocumentExtractionSummary:
        file_results = self.file_results or [
            DocumentFileResult(
                source_file=item.source_file,
                status="ready",
                page_count=item.page_count,
                table_candidates=len(item.tables),
                figure_candidates=len(item.figures),
                duration_seconds=item.duration_seconds,
                warnings=item.warnings,
            )
            for item in self.documents
        ]
        has_failed_file = any(item.status == "failed" for item in file_results)
        return DocumentExtractionSummary(
            engine=self.engine,
            engine_version=self.engine_version,
            ocr_mode=str(self.settings.get("ocr", "auto")),
            status="failed" if has_failed_file else "ready",
            document_count=len(self.documents),
            page_count=sum(item.page_count for item in self.documents),
            text_characters=sum(len(item.markdown) for item in self.documents),
            table_candidates=sum(len(item.tables) for item in self.documents),
            figure_candidates=sum(len(item.figures) for item in self.documents),
            duration_seconds=self.duration_seconds,
            warnings=[*self.warnings, *(w for item in self.documents for w in item.warnings)],
            files=file_results,
        )

    def summary(self) -> dict[str, Any]:
        value = self.extraction_summary()
        return value.model_dump(mode="json")


class DocumentTableDecision(FrozenModel):
    candidate_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]+$")
    decision: Literal["accepted", "rejected"]
    source_file: str
    page_number: int | None = Field(default=None, ge=1)
    columns: list[str] = Field(default_factory=list)
    reviewer: Literal["human"] = "human"
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentTableReview(Artifact):
    """Human decisions that authorize specific extracted tables for promotion."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.DOCUMENT_TABLE_REVIEW
    schema_version: ClassVar[str] = "1"

    extraction_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[DocumentTableDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def decisions_target_unique_candidates(self) -> DocumentTableReview:
        candidate_ids = [item.candidate_id for item in self.decisions]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("document table review contains duplicate candidate decisions")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "extraction_artifact_id": self.extraction_artifact_id,
            "accepted": sum(item.decision == "accepted" for item in self.decisions),
            "rejected": sum(item.decision == "rejected" for item in self.decisions),
        }


__all__ = [
    "DocumentEngineId",
    "DocumentExtraction",
    "DocumentExtractionSummary",
    "DocumentFileResult",
    "DocumentPageContent",
    "DocumentTableDecision",
    "DocumentTableReview",
    "ExtractedDocument",
    "ExtractedFigureCandidate",
    "ExtractedTableCandidate",
]
