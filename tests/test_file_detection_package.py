from pathlib import Path


def test_english_file_detection_surface_routes_the_same_csv(tmp_path: Path) -> None:
    """The rename must expose an English package without changing its measurement."""
    from ads.file_detection.router import Decision, Flow, route

    source = tmp_path / "sample.csv"
    source.write_text("id,name\n1,Ada\n2,Grace\n", encoding="utf-8")

    decision = route(source)

    assert isinstance(decision, Decision)
    assert decision.akis is Flow.TABLE
    assert decision.deterministik is True
