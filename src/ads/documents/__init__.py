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

__all__ = [
    "PDF_SUFFIXES",
    "DocumentExtractionError",
    "PdfDocument",
    "load_pdf",
    "load_pdf_directory",
    "pdf_prompt_context",
    "document_extraction_prompt_context",
    "extract_document_directory",
]
