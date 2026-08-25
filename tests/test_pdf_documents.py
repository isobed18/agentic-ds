"""PDF staging stays local, bounded, and page-provenanced."""

from __future__ import annotations

from ads.documents.pdf import PdfDocument, PdfPage, pdf_prompt_context


def test_pdf_context_retrieves_relevant_page_with_small_bounded_payload() -> None:
    document = PdfDocument(
        name="study.pdf",
        page_count=4,
        pages=(
            PdfPage(1, "Executive overview of the study."),
            PdfPage(2, "The retention chart compares monthly churn by provider."),
            PdfPage(3, "Unrelated appendix material."),
            PdfPage(4, "Validation notes and limitations."),
        ),
        image_count=2,
    )

    context = pdf_prompt_context([document], "What does the retention chart say about churn?")

    assert context[0]["name"] == "study.pdf"
    assert {item["page"] for item in context[0]["selected_excerpts"]} >= {1, 2}
    assert sum(len(item["text"]) for item in context[0]["selected_excerpts"]) <= 24_000
    assert context[0]["training_status"] == "not_extracted"


def test_pdf_public_summary_never_contains_extracted_page_text() -> None:
    document = PdfDocument(
        name="private.pdf",
        page_count=1,
        pages=(PdfPage(1, "secret-person@example.test"),),
    )

    rendered = str(document.public_summary())

    assert "secret-person@example.test" not in rendered
    assert document.public_summary()["understanding_status"] == "text_ready"
