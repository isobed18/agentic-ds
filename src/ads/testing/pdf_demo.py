"""Bundled, offline PDF-table demo shared by the product and its benchmark."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

PDF_DEMO_NAME = "quarterly_orders.pdf"
PDF_DEMO_TRUTH_NAME = "quarterly_orders.csv"
PDF_DEMO_MANIFEST_NAME = "manifest.json"
PDF_DEMO_FILES = (PDF_DEMO_NAME, PDF_DEMO_TRUTH_NAME)
PDF_DEMO_COLUMNS = ("order_id", "region", "units", "revenue_try")
PDF_DEMO_ROWS = (
    ("TR-1001", "Istanbul", "12", "18450.00"),
    ("TR-1002", "Ankara", "8", "12720.00"),
    ("TR-1003", "Izmir", "15", "23100.00"),
    ("TR-1004", "Bursa", "6", "9180.00"),
    ("TR-1005", "Antalya", "11", "16940.00"),
    ("TR-1006", "Adana", "9", "13950.00"),
)


def pdf_demo_fixture_dir() -> Path:
    """Return the installed fixture directory without network or generation."""
    root = Path(__file__).with_name("fixtures") / "pdf_demo"
    missing = [
        name
        for name in (*PDF_DEMO_FILES, PDF_DEMO_MANIFEST_NAME)
        if not (root / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"bundled PDF demo is incomplete: {', '.join(missing)}")
    return root


def read_pdf_demo_truth(directory: str | Path | None = None) -> tuple[list[str], list[list[str]]]:
    """Read the CSV oracle used to score the PDF extraction."""
    root = Path(directory) if directory is not None else pdf_demo_fixture_dir()
    with (root / PDF_DEMO_TRUTH_NAME).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise ValueError("PDF demo ground truth is empty")
    return rows[0], rows[1:]


def write_pdf_demo_fixture(out_dir: str | Path) -> Path:
    """Regenerate the deterministic checked-in PDF, CSV truth, and manifest."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with (out / PDF_DEMO_TRUTH_NAME).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(PDF_DEMO_COLUMNS)
        writer.writerows(PDF_DEMO_ROWS)

    width, height = A4
    canvas = Canvas(str(out / PDF_DEMO_NAME), pagesize=A4, pageCompression=0, invariant=1)
    canvas.setTitle("Agentic DS PDF Extraction Demo")
    canvas.setAuthor("Agentic DS")
    canvas.setSubject("Offline table extraction benchmark")

    navy = HexColor("#14213D")
    blue = HexColor("#2563EB")
    pale = HexColor("#EFF6FF")
    line = HexColor("#CBD5E1")
    muted = HexColor("#64748B")
    canvas.setFillColor(navy)
    canvas.rect(0, height - 118, width, 118, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#93C5FD"))
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(48, height - 43, "AGENTIC DS / LOCAL DEMO")
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("Helvetica-Bold", 24)
    canvas.drawString(48, height - 76, "Quarterly order summary")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(
        48,
        height - 98,
        "A compact, deterministic table for offline PDF extraction checks",
    )

    canvas.setFillColor(pale)
    canvas.roundRect(48, height - 185, 499, 42, 8, fill=1, stroke=0)
    canvas.setFillColor(blue)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(62, height - 160, "6 orders")
    canvas.drawString(190, height - 160, "4 columns")
    canvas.drawString(330, height - 160, "TRY 94,340.00 revenue")

    canvas.setFillColor(navy)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(48, height - 222, "Orders by region")
    canvas.setFillColor(muted)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(
        48,
        height - 238,
        "The pipe-delimited grid is intentionally machine-readable and visually inspectable.",
    )

    lines = [
        "order_id | region   | units | revenue_try",
        "-------- | -------- | ----- | -----------",
        *(
            f"{order_id:<8} | {region:<8} | {units:>5} | {revenue:>11}"
            for order_id, region, units, revenue in PDF_DEMO_ROWS
        ),
    ]
    table_top = height - 270
    row_height = 31
    canvas.setStrokeColor(line)
    canvas.setLineWidth(0.7)
    for index, text in enumerate(lines):
        y = table_top - index * row_height
        if index == 0:
            canvas.setFillColor(navy)
        elif index % 2 == 0:
            canvas.setFillColor(HexColor("#F8FAFC"))
        else:
            canvas.setFillColor(HexColor("#FFFFFF"))
        canvas.rect(48, y - 22, 499, row_height, fill=1, stroke=1)
        canvas.setFillColor(HexColor("#FFFFFF") if index == 0 else navy)
        canvas.setFont("Courier-Bold" if index == 0 else "Courier", 9.5)
        canvas.drawString(62, y - 10, text)

    canvas.setFillColor(muted)
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(
        48,
        52,
        "Fixture version: pdf-extraction-v0 | Ground truth: quarterly_orders.csv",
    )
    canvas.setFillColor(blue)
    canvas.circle(535, 55, 4, fill=1, stroke=0)
    canvas.save()

    manifest: dict[str, Any] = {
        "benchmark": "pdf-extraction-v0",
        "source_file": PDF_DEMO_NAME,
        "ground_truth_file": PDF_DEMO_TRUTH_NAME,
        "expected": {
            "candidate_tables": 1,
            "page_number": 1,
            "columns": list(PDF_DEMO_COLUMNS),
            "row_count": len(PDF_DEMO_ROWS),
        },
    }
    (out / PDF_DEMO_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return out


__all__ = [
    "PDF_DEMO_COLUMNS",
    "PDF_DEMO_FILES",
    "PDF_DEMO_MANIFEST_NAME",
    "PDF_DEMO_NAME",
    "PDF_DEMO_ROWS",
    "PDF_DEMO_TRUTH_NAME",
    "pdf_demo_fixture_dir",
    "read_pdf_demo_truth",
    "write_pdf_demo_fixture",
]
