"""The bundled PDF demo and its benchmark are one offline, measurable fixture."""

from __future__ import annotations

from pathlib import Path

from benchmarks.pdf_extraction_v0.score import score_demo
from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.store import ArtifactStore
from ads.testing.pdf_demo import (
    PDF_DEMO_COLUMNS,
    PDF_DEMO_FILES,
    PDF_DEMO_ROWS,
    pdf_demo_fixture_dir,
    read_pdf_demo_truth,
)


def _plane(tmp_path: Path) -> ControlPlane:
    return ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "sources",),
        upload_root=tmp_path / "uploads",
    )


def test_pdf_demo_benchmark_recovers_the_structured_truth_offline(tmp_path: Path) -> None:
    result = score_demo(pdf_demo_fixture_dir(), output_dir=tmp_path / "extraction")

    assert result["passed"] is True
    assert result["candidate_tables"] == 1
    assert result["columns_exact"] is True
    assert result["row_count"] == result["expected_row_count"] == 6
    assert result["cell_accuracy"] == 1.0


def test_checked_in_csv_oracle_matches_the_fixture_definition() -> None:
    columns, rows = read_pdf_demo_truth()

    assert tuple(columns) == PDF_DEMO_COLUMNS
    assert tuple(map(tuple, rows)) == PDF_DEMO_ROWS


def test_pdf_demo_installs_once_as_a_ready_project_source(tmp_path: Path) -> None:
    plane = _plane(tmp_path)

    first = plane.install_pdf_demo(owner="ishak-ads")
    second = plane.install_pdf_demo(owner="ishak-ads")

    assert second["source_id"] == first["source_id"]
    assert second["reused"] is True
    assert first["files"] == sorted(PDF_DEMO_FILES)
    assert {
        path.name for path in plane.source_path(first["source_id"]).iterdir() if path.is_file()
    } == set(PDF_DEMO_FILES)
    assert len([path for path in plane.upload_root.iterdir() if path.is_dir()]) == 1


def test_pdf_demo_is_available_through_the_product_api(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    client = TestClient(create_app(plane.store.root, plane=plane))

    first = client.post("/api/demo-data/pdf")
    second = client.post("/api/demo-data/pdf")

    assert first.status_code == 200
    assert first.json()["files"] == sorted(PDF_DEMO_FILES)
    assert second.json()["source_id"] == first.json()["source_id"]
    assert second.json()["reused"] is True
