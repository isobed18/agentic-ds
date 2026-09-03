"""Local document-intake adapters used by the staging workspace."""

from ads.documents.extraction import (
    DocumentExtractionError,
    document_extraction_prompt_context,
    extract_document_directory,
)
from ads.documents.pdf import (
    PDF_SUFFIXES,
    PdfDocument,
    load_pdf,
    load_pdf_directory,
    pdf_prompt_context,
)
from ads.documents.promotion import (
    PROMOTED_SOURCE_FORMAT,
    CandidateNotPromotable,
    create_document_table_review,
    load_promoted_document_tables,
    promote_reviewed_document_tables,
)

__all__ = [
    "PDF_SUFFIXES",
    "CandidateNotPromotable",
    "DocumentExtractionError",
    "PdfDocument",
    "load_pdf",
    "load_pdf_directory",
    "pdf_prompt_context",
    "PROMOTED_SOURCE_FORMAT",
    "create_document_table_review",
    "load_promoted_document_tables",
    "promote_reviewed_document_tables",
    "document_extraction_prompt_context",
    "extract_document_directory",
]
