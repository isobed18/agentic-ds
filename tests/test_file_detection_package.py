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


def test_repository_importers_use_only_the_english_detection_package() -> None:
    """Compatibility shims must not become a permanent second vocabulary."""
    root = Path(__file__).resolve().parents[1]
    legacy_package = root / "src" / "ads" / "kesif"
    legacy_import = "ads." + "kesif"
    offenders = [
        path.relative_to(root).as_posix()
        for base in (root / "src", root / "tests", root / "scripts", root / "benchmarks")
        for path in base.rglob("*.py")
        if path != Path(__file__).resolve()
        and legacy_import in path.read_text(encoding="utf-8")
    ]

    assert not legacy_package.exists()
    assert offenders == []
