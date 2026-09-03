"""Human review and promotion of document table candidates."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from ads.contracts.base import ArtifactType
from ads.contracts.dataflow import TableAsset
from ads.contracts.documents import (
    DocumentExtraction,
    DocumentTableDecision,
    DocumentTableReview,
    ExtractedTableCandidate,
)
from ads.dataflow import load_table_asset, persist_table_asset
from ads.intake import LoadedTable
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
    return pd.DataFrame(candidate.rows, columns=columns)


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


#: The component id every promotion writes into its asset's provenance. It is
#: what distinguishes a promoted PDF table from every other `TableAsset` a run
#: writes -- the integrated ABT is one too.
PROMOTION_COMPONENT_ID = "document-table-promotion"

#: The `source_format` a promoted table carries into profiling. PDF-derived rows
#: entering the ABT stay distinguishable from uploaded ones: the DataCard, the
#: digest an agent reads, and the source-profile payload all show this rather
#: than "csv" (#445).
PROMOTED_SOURCE_FORMAT = "document_table"


def _table_name(asset_name: str | None, artifact_id: str) -> str:
    """A SQL-safe table name for a promoted candidate.

    `promote_reviewed_document_tables` names the artifact after the candidate
    id, which is `report.pdf:table:1` -- dots and colons that the integration
    executor would have to quote and that read badly in a plan. The candidate id
    is still on the `PromotedDocumentTable` record and in the asset provenance,
    so nothing is lost by making this readable.
    """
    raw = asset_name or artifact_id
    cleaned = "".join(character if character.isalnum() else "_" for character in raw).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return f"doc_{cleaned.lower()}" if cleaned else f"doc_{artifact_id[:12]}"


def load_promoted_document_tables(store: ArtifactStore, run_id: str) -> list[LoadedTable]:
    """Every promoted document table for a run, as tables intake can profile.

    #445 (the follow-up #390 said was tracked separately and was not): promotion
    was a durable, consequential record that changed no training data. It wrote
    a Parquet-backed `TableAsset` with full provenance, recorded the human
    decision and settled the review gate -- and `load_table_asset` had no
    production caller, so nothing read it back.

    Returned oldest first so a re-promotion of the same run is stable, and so
    the order the person accepted candidates in is the order the plan sees them.
    Provenance is preserved rather than flattened: `source_uri` keeps the
    `file#page=N` the promotion recorded, and `source_format` says these rows
    came out of a document.
    """
    tables: list[LoadedTable] = []
    seen: set[str] = set()
    for reference in reversed(store.list(run_id, artifact_type=ArtifactType.TABLE_ASSET)):
        if reference.artifact_id in seen:
            continue
        try:
            asset, frame = load_table_asset(store, reference.artifact_id)
        except (FileNotFoundError, ValueError):
            # A blob that fails its own integrity check is not silently mixed
            # into training data. The promotion record stays; the rows do not.
            continue
        if asset.provenance.producer_component_id != PROMOTION_COMPONENT_ID:
            continue
        seen.add(reference.artifact_id)
        tables.append(
            LoadedTable(
                name=_table_name(reference.name, reference.artifact_id),
                frame=frame,
                source_uri=(
                    asset.provenance.source_uris[0]
                    if asset.provenance.source_uris
                    else f"artifact:{reference.artifact_id}"
                ),
                source_format=PROMOTED_SOURCE_FORMAT,
            )
        )
    return tables


__all__ = [
    "PROMOTED_SOURCE_FORMAT",
    "PROMOTION_COMPONENT_ID",
    "CandidateNotPromotable",
    "create_document_table_review",
    "load_promoted_document_tables",
    "promote_reviewed_document_tables",
]
