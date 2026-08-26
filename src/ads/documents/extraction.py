"""Executable local document-engine adapters with one normalized output contract."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from ads.contracts.documents import (
    DocumentEngineId,
    DocumentExtraction,
    DocumentPageContent,
    ExtractedDocument,
    ExtractedFigureCandidate,
    ExtractedTableCandidate,
)
from ads.documents.pdf import PDF_SUFFIXES, load_pdf

_MAX_MARKDOWN_CHARS = 5_000_000
_MAX_PAGE_CHARS = 250_000
_MAX_TABLE_ROWS = 50_000
_MAX_TABLE_COLUMNS = 250
_MAX_WARNINGS = 100
_MARKDOWN_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


class DocumentExtractionError(RuntimeError):
    """A selected engine could not produce a trustworthy normalized artifact."""


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


@lru_cache(maxsize=8)
def _engine_version(engine: DocumentEngineId) -> str | None:
    distributions = {
        "docling": "docling",
        "unstructured": "unstructured",
        "marker": "marker-pdf",
        "mineru": "mineru",
        "text_layer": "pypdf",
    }
    direct = _version(distributions[engine])
    if direct is not None or engine not in {"marker", "mineru"}:
        return direct
    worker_python = Path.cwd() / ".document-envs" / engine / "Scripts" / "python.exe"
    if not worker_python.exists():
        return None
    result = subprocess.run(
        [
            str(worker_python),
            "-c",
            (
                "import importlib.metadata;"
                f"print(importlib.metadata.version({distributions[engine]!r}))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def _text(value: Any, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _ocr_enabled(settings: dict[str, Any]) -> bool:
    value = settings.get("ocr", "auto")
    return value is True or str(value).casefold() in {"auto", "always", "true", "1"}


def _page_number(item: Any) -> int | None:
    provenance = getattr(item, "prov", None) or []
    if provenance:
        value = getattr(provenance[0], "page_no", None)
        if isinstance(value, int) and value >= 1:
            return value
    metadata = getattr(item, "metadata", None)
    value = getattr(metadata, "page_number", None)
    return value if isinstance(value, int) and value >= 1 else None


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or bool(pd.isna(value)):
        return None
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _table_from_frame(
    frame: pd.DataFrame,
    *,
    source_file: str,
    index: int,
    page_number: int | None,
    markdown: str = "",
    title: str | None = None,
) -> tuple[ExtractedTableCandidate, list[str]]:
    warnings: list[str] = []
    if frame.shape[1] > _MAX_TABLE_COLUMNS:
        warnings.append(
            f"table_{index}_columns_truncated:{frame.shape[1]}->{_MAX_TABLE_COLUMNS}"
        )
        frame = frame.iloc[:, :_MAX_TABLE_COLUMNS]
    if frame.shape[0] > _MAX_TABLE_ROWS:
        warnings.append(f"table_{index}_rows_truncated:{frame.shape[0]}->{_MAX_TABLE_ROWS}")
        frame = frame.iloc[:_MAX_TABLE_ROWS]
    columns = [str(value) for value in frame.columns]
    rows = [[_scalar(value) for value in row] for row in frame.itertuples(index=False, name=None)]
    return (
        ExtractedTableCandidate(
            candidate_id=f"{Path(source_file).stem}:table:{index}",
            source_file=source_file,
            page_number=page_number,
            title=title,
            columns=columns,
            rows=rows,
            markdown=_text(markdown, limit=500_000),
        ),
        warnings,
    )


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]


def _tables_from_markdown(markdown: str, source_file: str) -> list[ExtractedTableCandidate]:
    """Recover ordinary Markdown tables emitted by Marker or MinerU."""
    lines = markdown.splitlines()
    tables: list[ExtractedTableCandidate] = []
    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index] or not _MARKDOWN_TABLE_SEPARATOR.match(lines[index + 1]):
            index += 1
            continue
        block = [lines[index], lines[index + 1]]
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
            block.append(lines[cursor])
            cursor += 1
        columns = _split_markdown_row(block[0])
        rows = [_split_markdown_row(line) for line in block[2:]]
        if columns and all(len(row) == len(columns) for row in rows):
            tables.append(
                ExtractedTableCandidate(
                    candidate_id=f"{Path(source_file).stem}:table:{len(tables) + 1}",
                    source_file=source_file,
                    columns=columns,
                    rows=rows[:_MAX_TABLE_ROWS],
                    markdown="\n".join(block)[:500_000],
                )
            )
        index = cursor
    return tables


def _text_layer(path: Path, settings: dict[str, Any], output_dir: Path) -> ExtractedDocument:
    del settings, output_dir
    parsed = load_pdf(path)
    markdown = "\n\n".join(
        f"<!-- page {page.number} -->\n\n{page.text}" for page in parsed.pages if page.text
    )
    return ExtractedDocument(
        source_file=path.name,
        title=parsed.title,
        page_count=parsed.page_count,
        markdown=markdown[:_MAX_MARKDOWN_CHARS],
        pages=[
            DocumentPageContent(page_number=page.number, markdown=page.text[:_MAX_PAGE_CHARS])
            for page in parsed.pages
            if page.text
        ],
        warnings=[parsed.issue] if parsed.issue else [],
    )


@lru_cache(maxsize=8)
def _docling_converter(ocr: bool, tables: bool, figures: bool) -> Any:
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
    from docling.document_converter import (  # noqa: PLC0415
        DocumentConverter,
        PdfFormatOption,
    )

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = tables
    options.generate_page_images = figures
    options.generate_picture_images = figures
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _docling(path: Path, settings: dict[str, Any], output_dir: Path) -> ExtractedDocument:
    del output_dir
    converter = _docling_converter(
        _ocr_enabled(settings),
        bool(settings.get("extract_tables", True)),
        bool(settings.get("extract_figures", True)),
    )
    result = converter.convert(path, max_file_size=100 * 1024 * 1024, max_num_pages=1_000)
    document = result.document
    markdown = _text(document.export_to_markdown(), limit=_MAX_MARKDOWN_CHARS)
    page_numbers = sorted(int(number) for number in getattr(document, "pages", {}))
    pages: list[DocumentPageContent] = []
    for number in page_numbers:
        try:
            page_markdown = document.export_to_markdown(page_no=number)
        except Exception:
            page_markdown = ""
        if page_markdown:
            pages.append(
                DocumentPageContent(
                    page_number=number,
                    markdown=_text(page_markdown, limit=_MAX_PAGE_CHARS),
                )
            )

    tables: list[ExtractedTableCandidate] = []
    warnings: list[str] = []
    if settings.get("extract_tables", True):
        for index, item in enumerate(getattr(document, "tables", []), start=1):
            try:
                frame = item.export_to_dataframe(doc=document)
                rendered = item.export_to_markdown(doc=document)
                candidate, table_warnings = _table_from_frame(
                    frame,
                    source_file=path.name,
                    index=index,
                    page_number=_page_number(item),
                    markdown=rendered,
                )
                tables.append(candidate)
                warnings.extend(table_warnings)
            except Exception as exc:
                warnings.append(f"table_{index}_normalization_failed:{type(exc).__name__}")

    figures: list[ExtractedFigureCandidate] = []
    if settings.get("extract_figures", True):
        for index, item in enumerate(getattr(document, "pictures", []), start=1):
            caption: str | None = None
            try:
                caption = _text(item.caption_text(document), limit=1_000) or None
            except Exception:
                pass
            figures.append(
                ExtractedFigureCandidate(
                    candidate_id=f"{path.stem}:figure:{index}",
                    source_file=path.name,
                    page_number=_page_number(item),
                    caption=caption,
                    kind="picture",
                )
            )
    return ExtractedDocument(
        source_file=path.name,
        title=getattr(document, "name", None),
        page_count=len(page_numbers),
        markdown=markdown,
        pages=pages,
        tables=tables,
        figures=figures,
        warnings=warnings[:_MAX_WARNINGS],
    )


def _unstructured(path: Path, settings: dict[str, Any], output_dir: Path) -> ExtractedDocument:
    from unstructured.partition.pdf import partition_pdf  # noqa: PLC0415

    figures_dir = output_dir / "unstructured" / path.stem
    figures_dir.mkdir(parents=True, exist_ok=True)
    wants_layout = bool(
        settings.get("extract_tables", True) or settings.get("extract_figures", True)
    )
    warnings: list[str] = []
    try:
        elements = partition_pdf(
            filename=str(path),
            strategy=(
                "hi_res" if wants_layout else "auto" if _ocr_enabled(settings) else "fast"
            ),
            infer_table_structure=bool(settings.get("extract_tables", True)),
            extract_images_in_pdf=bool(settings.get("extract_figures", True)),
            extract_image_block_types=(
                ["Image"] if settings.get("extract_figures", True) else None
            ),
            extract_image_block_output_dir=str(figures_dir),
            languages=["eng", "tur"],
        )
    except Exception as exc:
        # Unstructured delegates local OCR to the system Tesseract binary. A
        # digital PDF can still be understood safely through its text layer,
        # so retain that usable output and say exactly which capabilities fell
        # back instead of failing the whole automation.
        if "Tesseract" not in type(exc).__name__ and "tesseract" not in str(exc).casefold():
            raise
        elements = partition_pdf(filename=str(path), strategy="fast")
        warnings.append("ocr_table_figure_fallback:text_layer_only:tesseract_unavailable")
    page_parts: dict[int, list[str]] = {}
    tables: list[ExtractedTableCandidate] = []
    figures: list[ExtractedFigureCandidate] = []
    for element in elements:
        page = _page_number(element) or 1
        category = str(getattr(element, "category", type(element).__name__))
        content = _text(element, limit=_MAX_PAGE_CHARS)
        page_parts.setdefault(page, []).append(content)
        metadata = getattr(element, "metadata", None)
        if category.casefold() == "table" and settings.get("extract_tables", True):
            html = str(getattr(metadata, "text_as_html", "") or "")
            try:
                frames = pd.read_html(html) if html else []
            except Exception:
                frames = []
            if frames:
                candidate, table_warnings = _table_from_frame(
                    frames[0],
                    source_file=path.name,
                    index=len(tables) + 1,
                    page_number=page,
                    markdown=content,
                )
                tables.append(candidate)
                warnings.extend(table_warnings)
            else:
                fallback = _tables_from_markdown(content, path.name)
                tables.extend(fallback)
        image_path = getattr(metadata, "image_path", None)
        if category.casefold() == "image" and settings.get("extract_figures", True):
            figures.append(
                ExtractedFigureCandidate(
                    candidate_id=f"{path.stem}:figure:{len(figures) + 1}",
                    source_file=path.name,
                    page_number=page,
                    caption=content[:1_000] or None,
                    kind="picture",
                    image_path=str(image_path) if image_path else None,
                )
            )
    pages = [
        DocumentPageContent(
            page_number=number,
            markdown="\n\n".join(parts)[:_MAX_PAGE_CHARS],
        )
        for number, parts in sorted(page_parts.items())
    ]
    markdown = "\n\n".join(
        f"<!-- page {page.page_number} -->\n\n{page.markdown}" for page in pages
    )
    return ExtractedDocument(
        source_file=path.name,
        page_count=max(page_parts, default=0),
        markdown=markdown[:_MAX_MARKDOWN_CHARS],
        pages=pages,
        tables=tables,
        figures=figures,
        warnings=warnings[:_MAX_WARNINGS],
    )


def _marker(path: Path, settings: dict[str, Any], output_dir: Path) -> ExtractedDocument:
    worker_python = Path.cwd() / ".document-envs" / "marker" / "Scripts" / "python.exe"
    if not worker_python.exists():
        raise DocumentExtractionError("Marker worker environment is not installed")
    worker = Path(__file__).with_name("engine_worker.py")
    worker_temp = Path.cwd() / ".document-models" / "tmp"
    worker_temp.mkdir(parents=True, exist_ok=True)
    worker_environment = {
        **os.environ,
        "MODEL_CACHE_DIR": str(Path.cwd() / ".document-models" / "marker"),
        "HF_HOME": str(Path.cwd() / ".document-models" / "huggingface"),
        "TEMP": str(worker_temp),
        "TMP": str(worker_temp),
    }
    result = subprocess.run(
        [str(worker_python), str(worker), "marker", str(path), str(output_dir / "marker")],
        capture_output=True,
        text=True,
        env=worker_environment,
        timeout=30 * 60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2_000:]
        raise DocumentExtractionError(f"Marker failed with exit {result.returncode}: {detail}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise DocumentExtractionError("Marker returned an invalid worker response") from exc
    markdown = _text(payload.get("markdown"), limit=_MAX_MARKDOWN_CHARS)
    metadata = payload.get("metadata") or {}
    images = payload.get("images") or []
    figures: list[ExtractedFigureCandidate] = []
    if settings.get("extract_figures", True):
        for index, image in enumerate(images, start=1):
            figures.append(
                ExtractedFigureCandidate(
                    candidate_id=f"{path.stem}:figure:{index}",
                    source_file=path.name,
                    caption=str(image.get("name") or ""),
                    kind="picture",
                    image_path=str(image.get("path") or "") or None,
                )
            )
    page_count = load_pdf(path).page_count
    if isinstance(metadata, dict):
        page_count = int(metadata.get("page_count") or metadata.get("pages") or page_count)
    return ExtractedDocument(
        source_file=path.name,
        page_count=page_count,
        markdown=markdown,
        tables=(
            _tables_from_markdown(markdown, path.name)
            if settings.get("extract_tables", True)
            else []
        ),
        figures=figures,
    )


def _mineru_executable() -> str:
    isolated = Path.cwd() / ".document-envs" / "mineru" / "Scripts" / "mineru.exe"
    local = Path(sys.executable).with_name("mineru.exe")
    found = (
        str(isolated)
        if isolated.exists()
        else str(local)
        if local.exists()
        else shutil.which("mineru")
    )
    if not found:
        raise DocumentExtractionError("MinerU is installed without its mineru command")
    return found


def _mineru(path: Path, settings: dict[str, Any], output_dir: Path) -> ExtractedDocument:
    target = output_dir / "mineru" / path.stem
    target.mkdir(parents=True, exist_ok=True)
    command = [_mineru_executable(), "-p", str(path), "-o", str(target), "-b", "pipeline"]
    worker_temp = Path.cwd() / ".document-models" / "tmp"
    worker_temp.mkdir(parents=True, exist_ok=True)
    worker_environment = {
        **os.environ,
        "HF_HOME": str(Path.cwd() / ".document-models" / "huggingface"),
        "MODELSCOPE_CACHE": str(Path.cwd() / ".document-models" / "modelscope"),
        "TEMP": str(worker_temp),
        "TMP": str(worker_temp),
    }
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=worker_environment,
        timeout=30 * 60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2_000:]
        raise DocumentExtractionError(f"MinerU failed with exit {result.returncode}: {detail}")
    markdown_files = sorted(
        target.rglob("*.md"), key=lambda item: item.stat().st_size, reverse=True
    )
    if not markdown_files:
        raise DocumentExtractionError("MinerU completed without a Markdown output")
    markdown = markdown_files[0].read_text(encoding="utf-8", errors="replace")[:_MAX_MARKDOWN_CHARS]
    image_files = [
        item for item in target.rglob("*") if item.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    figures = [
        ExtractedFigureCandidate(
            candidate_id=f"{path.stem}:figure:{index}",
            source_file=path.name,
            caption=item.name,
            kind="picture",
            image_path=str(item),
        )
        for index, item in enumerate(image_files, start=1)
    ] if settings.get("extract_figures", True) else []
    return ExtractedDocument(
        source_file=path.name,
        page_count=load_pdf(path).page_count,
        markdown=markdown,
        tables=(
            _tables_from_markdown(markdown, path.name)
            if settings.get("extract_tables", True)
            else []
        ),
        figures=figures,
    )


_EXTRACTORS: dict[DocumentEngineId, Callable[[Path, dict[str, Any], Path], ExtractedDocument]] = {
    "docling": _docling,
    "unstructured": _unstructured,
    "marker": _marker,
    "mineru": _mineru,
    "text_layer": _text_layer,
}


def extract_document_directory(
    directory: str | Path,
    *,
    source_id: str,
    source_fingerprint: str,
    engine: DocumentEngineId,
    settings: dict[str, Any],
    output_dir: str | Path,
) -> DocumentExtraction:
    """Execute the selected adapter locally and return a normalized artifact."""
    started = time.perf_counter()
    root = Path(directory)
    documents = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in PDF_SUFFIXES
    ]
    if not documents:
        raise DocumentExtractionError("this source has no supported documents")
    extractor = _EXTRACTORS[engine]
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[ExtractedDocument] = []
    warnings: list[str] = []
    for path in documents:
        try:
            extracted.append(extractor(path, settings, destination))
        except Exception as exc:
            warnings.append(f"{path.name}:{type(exc).__name__}:{str(exc)[:500]}")
    if not extracted:
        raise DocumentExtractionError("; ".join(warnings) or f"{engine} produced no documents")
    return DocumentExtraction(
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        engine=engine,
        engine_version=_engine_version(engine),
        settings=dict(settings),
        documents=extracted,
        duration_seconds=time.perf_counter() - started,
        warnings=warnings[:_MAX_WARNINGS],
    )


def document_extraction_prompt_context(
    artifact: DocumentExtraction,
    query: str,
    *,
    character_budget: int = 24_000,
) -> list[dict[str, Any]]:
    """Select page-provenanced engine output for the local planner."""
    terms = {word.casefold() for word in re.findall(r"[\w-]{3,}", query)}
    remaining = character_budget
    result: list[dict[str, Any]] = []
    for document in artifact.documents:
        candidates = document.pages or [
            DocumentPageContent(page_number=1, markdown=document.markdown)
        ]
        ranked = sorted(
            candidates,
            key=lambda page: sum(page.markdown.casefold().count(term) for term in terms),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        for page in ranked[:7]:
            if remaining <= 0:
                break
            text = page.markdown[:remaining]
            selected.append({"page": page.page_number, "text": text})
            remaining -= len(text)
        result.append(
            {
                "name": document.source_file,
                "format": "pdf",
                "engine": artifact.engine,
                "pages": document.page_count,
                "table_candidates": len(document.tables),
                "figure_candidates": len(document.figures),
                "selected_excerpts": selected,
            }
        )
        if remaining <= 0:
            break
    return result


__all__ = [
    "DocumentExtractionError",
    "document_extraction_prompt_context",
    "extract_document_directory",
]
