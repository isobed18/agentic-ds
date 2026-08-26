"""Bounded, local PDF profiling and retrieval for planner conversations.

This is deliberately not a chart-to-training-data extractor. It recovers the
PDF text layer and document metadata, then selects a small set of page chunks
for the local planner. A later Docling/VLM adapter can add OCR, layout, chart,
and table artifacts without changing this module's public evidence boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

PDF_SUFFIXES = {".pdf"}
_WORD = re.compile(r"[\w-]{3,}", re.UNICODE)
_CHUNK_CHARS = 3_500
_PROMPT_BUDGET = 24_000
_MAX_CHUNKS = 7


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str


@dataclass(frozen=True)
class PdfDocument:
    """Extracted document kept inside the control plane, never returned raw."""

    name: str
    page_count: int
    pages: tuple[PdfPage, ...]
    title: str | None = None
    author: str | None = None
    image_count: int = 0
    issue: str | None = None

    def public_summary(self) -> dict[str, Any]:
        text_pages = sum(bool(page.text.strip()) for page in self.pages)
        return {
            "name": self.name,
            "format": "pdf",
            "pages": self.page_count,
            "text_pages": text_pages,
            "text_characters": sum(len(page.text) for page in self.pages),
            "image_count": self.image_count,
            "title": self.title,
            "author": self.author,
            "understanding_status": (
                "text_ready" if text_pages else "needs_ocr_or_vision"
            ),
            "training_status": "not_extracted",
            "issues": [self.issue] if self.issue else [],
        }


def _metadata_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:300] or None


def load_pdf(path: str | Path) -> PdfDocument:
    """Read a PDF text layer locally while retaining page provenance."""
    source = Path(path)
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pragma: no cover - backend-specific
                return PdfDocument(source.name, len(reader.pages), (), issue=f"encrypted_pdf:{exc}")

        pages: list[PdfPage] = []
        image_count = 0
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            pages.append(PdfPage(number=number, text=text))
            try:
                image_count += len(page.images)
            except Exception:
                pass

        metadata = reader.metadata or {}
        return PdfDocument(
            name=source.name,
            page_count=len(reader.pages),
            pages=tuple(pages),
            title=_metadata_text(metadata.get("/Title")),
            author=_metadata_text(metadata.get("/Author")),
            image_count=image_count,
        )
    except Exception as exc:
        return PdfDocument(source.name, 0, (), issue=f"pdf_parse_error:{type(exc).__name__}")


def load_pdf_directory(directory: str | Path) -> list[PdfDocument]:
    """Load PDFs in deterministic relative-path order."""
    root = Path(directory)
    return [
        load_pdf(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in PDF_SUFFIXES
    ]


def _chunks(document: PdfDocument) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in document.pages:
        text = page.text.strip()
        for start in range(0, len(text), _CHUNK_CHARS):
            value = text[start : start + _CHUNK_CHARS].strip()
            if value:
                chunks.append({"page": page.number, "text": value})
    return chunks


def _query_terms(query: str) -> set[str]:
    stop = {
        "about", "could", "data", "dataset", "document", "explain", "file",
        "from", "have", "please", "should", "summarize", "understand", "what",
        "which", "with", "this", "that", "these", "those", "user",
    }
    return {word.casefold() for word in _WORD.findall(query) if word.casefold() not in stop}


def pdf_prompt_context(documents: list[PdfDocument], query: str) -> list[dict[str, Any]]:
    """Select page-provenanced excerpts within a small-model context budget.

    Specific questions use lexical retrieval. Broad briefing questions use the
    opening pages plus evenly distributed samples, which is a deterministic
    first pass until hierarchical Docling/VLM summaries are added.
    """
    terms = _query_terms(query)
    remaining = _PROMPT_BUDGET
    result: list[dict[str, Any]] = []
    for document in documents:
        candidates = _chunks(document)
        if not candidates:
            result.append({**document.public_summary(), "selected_excerpts": []})
            continue

        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, chunk in enumerate(candidates):
            lowered = chunk["text"].casefold()
            score = sum(lowered.count(term) for term in terms)
            scored.append((score, -index, chunk))

        selected: list[dict[str, Any]] = []
        # Preserve document orientation even for a narrow query.
        selected.append(candidates[0])
        if terms and any(score for score, _, _ in scored):
            ranked = [item[2] for item in sorted(scored, reverse=True)]
        else:
            indexes = {
                0,
                len(candidates) - 1,
                len(candidates) // 4,
                len(candidates) // 2,
                (len(candidates) * 3) // 4,
            }
            ranked = [candidates[index] for index in sorted(indexes)]

        for chunk in ranked:
            if chunk not in selected:
                selected.append(chunk)
            if len(selected) >= _MAX_CHUNKS:
                break

        bounded: list[dict[str, Any]] = []
        for chunk in selected:
            if remaining <= 0:
                break
            text = chunk["text"][:remaining]
            if text:
                bounded.append({"page": chunk["page"], "text": text})
                remaining -= len(text)
        result.append({**document.public_summary(), "selected_excerpts": bounded})
        if remaining <= 0:
            break
    return result
