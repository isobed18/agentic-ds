"""Warnings the document engines report, written for the person reading them.

Everything else a reader is shown in this product is prose. Extraction warnings
were the exception: colon-separated machine codes such as
``ocr_auto_skipped:text_layer_sufficient:30/30_pages`` rendered verbatim in the
Documents and Synthesize panels. The one place extraction explains itself was
the one place it explained nothing.

Each warning is composed here, in both languages, at the moment it is measured.
Extraction runs as a background stage rather than inside a request, so there is
no reader's language to translate into at that point -- both halves are stored
and the edge picks one, the same way agent prose already works.

The measured numbers stay in the sentence. A warning that drops the counts is
prettier and less useful; "30 of 30 pages" is the part that lets someone decide
whether skipping OCR was right.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentWarning:
    """One warning in both languages. The reader's half is chosen at the edge."""

    en: str
    tr: str


def split_warnings(warnings: Sequence[DocumentWarning]) -> tuple[list[str], list[str]]:
    """Separate collected warnings into the two positionally aligned lists.

    The contract stores `warnings` and `warnings_tr` as parallel lists, so the
    two are only ever built together, from the same sequence, and never appended
    to independently.
    """
    return [item.en for item in warnings], [item.tr for item in warnings]


def engine_substituted(engine: str) -> DocumentWarning:
    return DocumentWarning(
        en=(
            f"{engine} is not installed here, so the built-in text-layer reader was "
            f"used instead. Scanned pages and table structure will be missing. "
            f"Install the documents-{engine} extra to use {engine}."
        ),
        tr=(
            f"{engine} burada kurulu değil, bu yüzden yerleşik metin katmanı okuyucusu "
            f"kullanıldı. Taranmış sayfalar ve tablo yapısı eksik olacak. {engine} "
            f"kullanmak için documents-{engine} ek paketini kurun."
        ),
    )


def ocr_auto_skipped(text_pages: int, page_count: int) -> DocumentWarning:
    return DocumentWarning(
        en=(
            f"OCR was skipped — the document's own text layer already covers "
            f"{text_pages} of {page_count} pages."
        ),
        tr=(
            f"OCR atlandı — belgenin kendi metin katmanı {page_count} sayfanın "
            f"{text_pages} tanesini zaten kapsıyor."
        ),
    )


def ocr_unavailable() -> DocumentWarning:
    return DocumentWarning(
        en=(
            "OCR is unavailable here because Tesseract is not installed, so only the "
            "text layer was read. Scanned pages, table structure, and figures are "
            "missing from this document."
        ),
        tr=(
            "Tesseract kurulu olmadığı için OCR burada kullanılamıyor; yalnızca metin "
            "katmanı okundu. Taranmış sayfalar, tablo yapısı ve görseller bu belgede "
            "eksik."
        ),
    )


def table_columns_truncated(index: int, found: int, kept: int) -> DocumentWarning:
    return DocumentWarning(
        en=f"Table {index} had {found} columns; only the first {kept} were kept.",
        tr=f"Tablo {index} içinde {found} sütun vardı; yalnızca ilk {kept} tanesi tutuldu.",
    )


def table_rows_truncated(index: int, found: int, kept: int) -> DocumentWarning:
    return DocumentWarning(
        en=f"Table {index} had {found} rows; only the first {kept} were kept.",
        tr=f"Tablo {index} içinde {found} satır vardı; yalnızca ilk {kept} tanesi tutuldu.",
    )


def table_unreadable(index: int, detail: str) -> DocumentWarning:
    return DocumentWarning(
        en=f"Table {index} could not be read into a normalized table ({detail}); it was left out.",
        tr=(
            f"Tablo {index} normalleştirilmiş bir tabloya dönüştürülemedi ({detail}); "
            f"dışarıda bırakıldı."
        ),
    )


def document_unreadable(source_file: str, detail: str) -> DocumentWarning:
    return DocumentWarning(
        en=f"{source_file} could not be read: {detail}",
        tr=f"{source_file} okunamadı: {detail}",
    )


def pdf_issue(issue: str) -> DocumentWarning:
    """Turn a `load_pdf` issue code into prose.

    `PdfDocument.issue` stays a code: it is also read as one by the intake
    profile, and this module is the edge that needs the sentence.
    """
    code, _, detail = issue.partition(":")
    detail = detail.strip()
    if code == "encrypted_pdf":
        return DocumentWarning(
            en=f"The PDF is password protected and could not be opened ({detail}).",
            tr=f"PDF parola korumalı ve açılamadı ({detail}).",
        )
    if code == "pdf_parse_error":
        return DocumentWarning(
            en=f"The PDF could not be parsed ({detail}); nothing was read from it.",
            tr=f"PDF ayrıştırılamadı ({detail}); içinden hiçbir şey okunamadı.",
        )
    return DocumentWarning(en=issue, tr=issue)


__all__ = [
    "DocumentWarning",
    "document_unreadable",
    "engine_substituted",
    "ocr_auto_skipped",
    "ocr_unavailable",
    "pdf_issue",
    "split_warnings",
    "table_columns_truncated",
    "table_rows_truncated",
    "table_unreadable",
]
