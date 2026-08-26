import re
from pathlib import Path

from pypdf import PdfWriter

from ads.contracts.documents import DocumentExtraction, DocumentPageContent, ExtractedDocument
from ads.documents import document_extraction_prompt_context, extract_document_directory
from ads.documents.extraction import _candidate_id, _docling_ocr_enabled
from ads.documents.pdf import PdfDocument, PdfPage


def test_candidate_ids_are_safe_and_source_specific_for_real_world_filenames() -> None:
    first = _candidate_id("toefl-ibt-teachers-resources-practice-test-3 (1).pdf", "figure", 1)
    second = _candidate_id("toefl-ibt-teachers-resources-practice-test-3_1.pdf", "figure", 1)

    assert re.fullmatch(r"[a-zA-Z0-9_.:-]+", first)
    assert first != second
    assert first == _candidate_id(
        "toefl-ibt-teachers-resources-practice-test-3 (1).pdf", "figure", 1
    )


def test_docling_auto_ocr_skips_ocr_for_a_text_layer_pdf(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "large report (1).pdf"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        "ads.documents.extraction.load_pdf",
        lambda source: PdfDocument(
            name=Path(source).name,
            page_count=5,
            pages=tuple(PdfPage(number=index, text="readable text " * 8) for index in range(1, 6)),
        ),
    )

    enabled, warning = _docling_ocr_enabled(path, {"ocr": "auto"})

    assert enabled is False
    assert warning == "ocr_auto_skipped:text_layer_sufficient:5/5_pages"


def test_document_extraction_reports_truthful_per_file_progress(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in ("survey.pdf", "report.pdf"):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=300)
        with (source / name).open("wb") as stream:
            writer.write(stream)

    events: list[tuple[str, dict]] = []
    extraction = extract_document_directory(
        source,
        source_id="mixed",
        source_fingerprint="fingerprint",
        engine="text_layer",
        settings={"ocr": "never", "extract_tables": True, "extract_figures": True},
        output_dir=tmp_path / "output",
        on_progress=lambda name, payload: events.append((name, payload)),
    )

    assert [name for name, _ in events] == [
        "file_started",
        "file_ready",
        "file_started",
        "file_ready",
    ]
    assert [item.source_file for item in extraction.file_results] == [
        "report.pdf",
        "survey.pdf",
    ]
    assert all(item.status == "ready" and item.page_count == 1 for item in extraction.file_results)
    summary = extraction.extraction_summary()
    assert summary.ocr_mode == "never"
    assert summary.page_count == 2
    assert len(summary.files) == 2


def test_planner_context_reserves_evidence_budget_for_every_document() -> None:
    extraction = DocumentExtraction(
        source_id="two-documents",
        source_fingerprint="fingerprint",
        engine="docling",
        documents=[
            ExtractedDocument(
                source_file="first.pdf",
                page_count=1,
                pages=[DocumentPageContent(page_number=1, markdown="first " * 4_000)],
            ),
            ExtractedDocument(
                source_file="second.pdf",
                page_count=1,
                pages=[DocumentPageContent(page_number=1, markdown="second " * 4_000)],
            ),
        ],
        duration_seconds=1.0,
    )

    context = document_extraction_prompt_context(
        extraction,
        "summarize every source",
        character_budget=2_000,
    )

    assert [item["name"] for item in context] == ["first.pdf", "second.pdf"]
    assert all(item["selected_excerpts"] for item in context)
    assert (
        sum(len(excerpt["text"]) for item in context for excerpt in item["selected_excerpts"])
        <= 2_000
    )
