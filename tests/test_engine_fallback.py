"""What happens when the requested extraction engine is not installed.

Every engine but `text_layer` is an optional extra and `docling` is the default,
so the container image -- which omits docling on purpose, because it pulls torch
-- requests an engine that is not there on every single upload.

The lazy import inside the adapter then raises ModuleNotFoundError per file, and
the per-file except turns each one into a warning, so a source of twenty PDFs
fails twenty times with "No module named docling". That reads as a broken
deployment rather than a missing optional dependency, which is what it is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ads.documents import extraction


@pytest.fixture(autouse=True)
def _clear_version_cache():
    extraction._engine_version.cache_clear()
    yield
    extraction._engine_version.cache_clear()


def _absent(*missing: str):
    """Report the named engines as not installed, everything else as present."""

    def fake(engine: str) -> str | None:
        return None if engine in missing else "1.0.0"

    return fake


def test_a_missing_engine_falls_back_to_the_text_layer(monkeypatch) -> None:
    monkeypatch.setattr(extraction, "_engine_version", _absent("docling"))
    engine, warning = extraction._resolve_engine("docling")
    assert engine == "text_layer"
    assert warning is not None


def test_the_warning_names_the_engine_and_the_cost(monkeypatch) -> None:
    """A silent downgrade would leave somebody wondering why the tables vanished."""
    monkeypatch.setattr(extraction, "_engine_version", _absent("docling"))
    _, warning = extraction._resolve_engine("docling")
    assert warning is not None
    assert "docling" in warning.en
    assert "documents-docling" in warning.en, "say how to get it back"
    assert "table" in warning.en.casefold(), "say what is lost"
    # A Turkish reader is owed the same three facts, not an English sentence.
    assert "documents-docling" in warning.tr, "say how to get it back"
    assert "tablo" in warning.tr.casefold(), "say what is lost"


def test_an_installed_engine_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(extraction, "_engine_version", _absent())
    assert extraction._resolve_engine("docling") == ("docling", None)


@pytest.mark.parametrize("engine", ["unstructured", "marker", "mineru"])
def test_every_optional_engine_degrades_the_same_way(monkeypatch, engine: str) -> None:
    monkeypatch.setattr(extraction, "_engine_version", _absent(engine))
    resolved, warning = extraction._resolve_engine(engine)
    assert resolved == "text_layer"
    assert warning is not None
    assert engine in warning.en and engine in warning.tr


def test_the_text_layer_is_never_substituted_for_itself(monkeypatch) -> None:
    """pypdf is a core dependency, so this is the floor. Recursing here would
    swap text_layer for text_layer and emit a warning saying nothing."""
    monkeypatch.setattr(extraction, "_engine_version", _absent("text_layer"))
    assert extraction._resolve_engine("text_layer") == ("text_layer", None)


def test_the_substitution_reaches_the_caller(monkeypatch, tmp_path: Path) -> None:
    """The warning has to survive into the artifact, not just be computed."""
    monkeypatch.setattr(extraction, "_engine_version", _absent("docling"))

    pdf = tmp_path / "source" / "one.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")

    def fake_text_layer(path, settings, output_dir):
        return extraction.ExtractedDocument(
            source_file=path.name, markdown="text", page_count=1, tables=[], figures=[]
        )

    monkeypatch.setitem(extraction._EXTRACTORS, "text_layer", fake_text_layer)

    artifact = extraction.extract_document_directory(
        pdf.parent,
        source_id="upload:test",
        source_fingerprint="fp",
        engine="docling",
        settings={},
        output_dir=tmp_path / "out",
    )
    assert artifact.engine == "text_layer", "record what actually ran, not what was asked for"
    assert any("docling" in w for w in artifact.warnings)
