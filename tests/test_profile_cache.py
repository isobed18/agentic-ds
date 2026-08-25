"""The source-profile cache, and the one thing that must invalidate it.

A cache that never expires is fast and wrong. These check both halves: that a
repeat call does not re-read the files, and that a changed file is noticed.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.api.service import ControlPlane
from ads.store import ArtifactStore


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "sources" / "demo"
    root.mkdir(parents=True)
    pd.DataFrame({"physician_id": [1, 2, 3], "tutar": [10.0, 20.0, 30.0]}).to_csv(
        root / "table.csv", index=False
    )
    return tmp_path / "sources", root


@pytest.fixture
def plane(tmp_path, source):
    roots, _ = source
    return ControlPlane(store=ArtifactStore(tmp_path / "artifacts"), source_roots=(roots,))


def test_a_repeat_call_is_served_from_cache(plane: ControlPlane, monkeypatch) -> None:
    first = plane.source_profile("demo")

    # If the loader is called again, the cache did not work. Asserting on a
    # timing would make this flaky; asserting the file is not re-read is the
    # actual claim.
    def _fail(*args, **kwargs):
        raise AssertionError("the source was re-read despite an unchanged fingerprint")

    monkeypatch.setattr("ads.api.service.load_directory", _fail)
    assert plane.source_profile("demo") == first


def test_a_changed_file_invalidates_it(plane: ControlPlane, source) -> None:
    _, root = source
    before = plane.source_profile("demo")
    assert before["tables"][0]["rows"] == 3

    pd.DataFrame({"physician_id": [1, 2, 3, 4], "tutar": [1.0, 2.0, 3.0, 4.0]}).to_csv(
        root / "table.csv", index=False
    )

    after = plane.source_profile("demo")
    assert after["tables"][0]["rows"] == 4, "a changed file must be re-profiled"


def test_a_new_file_invalidates_it(plane: ControlPlane, source) -> None:
    """Adding a table changes the dataset even though no existing file did."""
    _, root = source
    assert len(plane.source_profile("demo")["tables"]) == 1

    pd.DataFrame({"k": [1], "v": [2]}).to_csv(root / "second.csv", index=False)
    assert len(plane.source_profile("demo")["tables"]) == 2


def test_the_fingerprint_covers_size_and_time(plane: ControlPlane, source) -> None:
    _, root = source
    before = plane._source_fingerprint("demo")
    (root / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert plane._source_fingerprint("demo") != before


def test_two_sources_do_not_share_an_entry(plane: ControlPlane, source) -> None:
    roots, root = source
    other = roots / "other"
    other.mkdir()
    pd.DataFrame({"x": [1, 2, 3, 4, 5]}).to_csv(other / "t.csv", index=False)

    assert plane.source_profile("demo")["tables"][0]["rows"] == 3
    assert plane.source_profile("other")["tables"][0]["rows"] == 5
    assert plane.source_profile("demo")["tables"][0]["rows"] == 3
