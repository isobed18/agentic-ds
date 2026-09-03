"""Human review and promotion of document table candidates."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from ads.contracts.dataflow import TableAsset
from ads.contracts.documents import (
    DocumentExtraction,
    DocumentTableDecision,
    DocumentTableReview,
    ExtractedTableCandidate,
)
from ads.dataflow import persist_table_asset
from ads.intake import normalize_columns
from ads.store import ArtifactRef, ArtifactStore


def create_document_table_review(
    extraction: DocumentExtraction,
    *,
    extraction_artifact_id: str,
    decisions: Mapping[str, str],
) -> DocumentTableReview:
    candidates = {
        table.candidate_id: table for document in extraction.documents for table in document.tables
    }
    if len(candidates) != sum(len(document.tables) for document in extraction.documents):
        raise ValueError("document extraction contains duplicate table candidate ids")
    unknown = sorted(set(decisions) - set(candidates))
    if unknown:
        raise ValueError(f"unknown document table candidates: {', '.join(unknown)}")
    if not decisions:
        raise ValueError("at least one document table decision is required")
    review_decisions: list[DocumentTableDecision] = []
    for candidate_id, raw_decision in decisions.items():
        if raw_decision not in {"accepted", "rejected"}:
            raise ValueError(f"invalid review decision for {candidate_id!r}")
        candidate = candidates[candidate_id]
        review_decisions.append(
            DocumentTableDecision(
                candidate_id=candidate_id,
                decision=raw_decision,
                source_file=candidate.source_file,
                page_number=candidate.page_number,
                columns=candidate.columns,
            )
        )
    return DocumentTableReview(
        extraction_artifact_id=extraction_artifact_id,
        decisions=review_decisions,
    )


class CandidateNotPromotable(ValueError):
    """An accepted candidate cannot become a table.

    Raised with a code and the candidate's own provenance rather than a
    finished sentence, because the promote endpoint answers with the exception
    text and the reader saw it verbatim: an English message with the internal
    candidate id inside it (#310). The words are chosen at the edge, where the
    request's language is known -- see `ads.api.i18n`.
    """

    def __init__(self, code: str, candidate: ExtractedTableCandidate) -> None:
        super().__init__(f"{code}: {candidate.candidate_id}")
        self.code = code
        self.candidate_id = candidate.candidate_id
        #: How the review dialog named this table, not how the store keys it.
        self.title = candidate.title
        self.source_file = candidate.source_file
        self.page_number = candidate.page_number


def _candidate_frame(candidate: ExtractedTableCandidate) -> pd.DataFrame:
    # `rows` defaults to an empty list, so extraction can produce a candidate
    # with headers and no data at all. It looks promotable in the review dialog,
    # which reads the preview and never sees rows.
    if not candidate.rows:
        raise CandidateNotPromotable("no_rows", candidate)
    width = max(len(row) for row in candidate.rows)
    if width == 0:
        raise CandidateNotPromotable("no_columns", candidate)
    if any(len(row) != width for row in candidate.rows):
        raise CandidateNotPromotable("ragged_rows", candidate)
    columns = candidate.columns or [f"column_{index + 1}" for index in range(width)]
    if len(columns) != width:
        raise CandidateNotPromotable("schema_mismatch", candidate)
    frame = pd.DataFrame(candidate.rows, columns=columns)
    # A PDF table extracted from merged/multi-row headers routinely produces
    # blank or duplicate column labels (two sub-columns collapsing to the same
    # header text, an empty merged cell). Parquet -- unlike pandas itself --
    # rejects duplicate column names outright, so promotion crashed opaquely
    # on exactly the kind of real-world table this review step exists to let
    # a human accept. Apply the same normalization CSV/Excel intake already
    # does, so a promoted document table is cleaned exactly like an uploaded
    # file, not held to a stricter standard.
    normalized, _ = normalize_columns(frame)
    return normalized


def promote_reviewed_document_tables(
    store: ArtifactStore,
    *,
    run_id: str,
    review: DocumentTableReview,
) -> list[tuple[TableAsset, ArtifactRef]]:
    extraction = store.load(review.extraction_artifact_id, DocumentExtraction)
    candidates = {
        table.candidate_id: table for document in extraction.documents for table in document.tables
    }
    promoted: list[tuple[TableAsset, ArtifactRef]] = []
    for decision in review.decisions:
        if decision.decision != "accepted":
            continue
        candidate = candidates.get(decision.candidate_id)
        if candidate is None:
            raise ValueError(f"review references missing candidate {decision.candidate_id!r}")
        if (
            candidate.source_file != decision.source_file
            or candidate.page_number != decision.page_number
            or candidate.columns != decision.columns
        ):
            raise ValueError(f"candidate provenance changed for {decision.candidate_id!r}")
        promoted.append(
            persist_table_asset(
                store,
                _candidate_frame(candidate),
                run_id=run_id,
                producer_component_id="document-table-promotion",
                stage_exec_id="document-table-promotion",
                name=decision.candidate_id,
                source_artifact_ids=[review.extraction_artifact_id],
                source_uris=[
                    f"{decision.source_file}#page={decision.page_number}"
                    if decision.page_number
                    else decision.source_file
                ],
                transformation=f"promote_document_table:{decision.candidate_id}",
            )
        )
    return promoted


__all__ = [
    "CandidateNotPromotable",
    "create_document_table_review",
    "promote_reviewed_document_tables",
]
