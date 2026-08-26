"""The profile cache, and the restart that used to throw it away.

`source_profile` re-reads and re-measures every file in a source. Its own
docstring records the cost: listing three sources took 5.22s through the tunnel,
of which 4.91s was one 121 MB dataset being re-profiled for a screen that had
already shown it. The in-memory cache fixed that *within* a process — and then
every restart, deploy and crash paid the whole bill again, on the first page
load, which is exactly when someone is watching.

So the behaviour under test is not "is it cached" but "is it still cached after
the object holding it is gone". A cache that only works while the process lives
is the one that was already there.

Correctness matters more than the saving: a stale profile would describe data
that is no longer on disk. Every test here that asserts a hit is paired with one
asserting the fingerprint still invalidates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ads.api import service
from ads.api.service import ControlPlane
from ads.store.artifacts import ArtifactStore


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    source = tmp_path / "data" / "sample"
    source.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2, 3], "city": ["Ankara", "Izmir", "Bursa"]}).to_csv(
        source / "places.csv", index=False
    )
    return tmp_path


def _plane(root: Path) -> ControlPlane:
    return ControlPlane(
        store=ArtifactStore(root / "artifacts"),
        source_roots=(root / "data",),
        profile_cache_dir=root / "cache" / "profiles",
    )


def test_a_profile_survives_the_process_that_computed_it(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this exists for: a second, unrelated ControlPlane -- what a
    restart produces -- must not re-measure the files.

    Asserted by counting the expensive call rather than by comparing the two
    results. An earlier version of this test compared them, which passed
    whether or not the cache was consulted, because re-profiling unchanged
    files naturally returns the same answer. It proved nothing; removing the
    disk read left it green.
    """
    first = _plane(corpus)
    original = first.source_profile("sample")

    calls: list[int] = []
    real = service.profile_tables

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "profile_tables", counted)

    second = _plane(corpus)
    assert not second._profile_cache, "the fresh plane starts with an empty memory cache"

    restored = second.source_profile("sample")
    assert restored == original
    assert calls == [], "a restart must not re-profile a source that has not changed"
    assert "sample" in second._profile_cache, "the disk hit should populate memory too"


def test_a_changed_source_is_re_profiled_and_that_costs_the_work(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same claim: the saving must not apply when the data
    moved. Counted the same way, so a cache that never invalidates cannot hide
    behind matching output."""
    _plane(corpus).source_profile("sample")

    pd.DataFrame({"id": [9], "city": ["Konya"]}).to_csv(
        corpus / "data" / "sample" / "places.csv", index=False
    )

    calls: list[int] = []
    real = service.profile_tables

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "profile_tables", counted)
    _plane(corpus).source_profile("sample")
    assert calls, "changed files must be measured again"


def test_a_changed_file_is_re_profiled_even_though_a_cache_file_exists(corpus: Path) -> None:
    """Persistence must not buy speed with staleness."""
    first = _plane(corpus)
    before = first.source_profile("sample")

    pd.DataFrame({"id": [1, 2, 3, 4, 5], "city": list("abcde")}).to_csv(
        corpus / "data" / "sample" / "places.csv", index=False
    )

    after = _plane(corpus).source_profile("sample")
    assert after != before, "the fingerprint changed, so the cached answer is wrong"
    rows = {table["name"]: table["rows"] for table in after["tables"]}
    assert rows == {"places": 5}, "the re-profile must reflect the file on disk now"


def test_a_new_file_invalidates_the_source(corpus: Path) -> None:
    """Adding a table changes what the source *is*, not merely one file in it."""
    first = _plane(corpus)
    before = first.source_profile("sample")

    pd.DataFrame({"id": [1], "note": ["x"]}).to_csv(
        corpus / "data" / "sample" / "extra.csv", index=False
    )
    after = _plane(corpus).source_profile("sample")

    assert len(after["tables"]) > len(before["tables"])


def test_a_corrupt_cache_file_costs_time_not_correctness(corpus: Path) -> None:
    """A truncated write, a full disk, an interrupted deploy. None of these may
    turn into a wrong answer -- the only acceptable outcome is re-profiling."""
    first = _plane(corpus)
    expected = first.source_profile("sample")

    cache_files = list((corpus / "cache" / "profiles").glob("*.json"))
    assert cache_files, "the profile should have been written to disk"
    cache_files[0].write_text('{"fingerprint": "abc", "profile": {tru', encoding="utf-8")

    assert _plane(corpus).source_profile("sample") == expected


def test_a_cache_entry_for_different_content_is_ignored(corpus: Path) -> None:
    """The fingerprint is the whole guard; a file claiming to be a cache for
    some other state of the data must not be believed."""
    first = _plane(corpus)
    expected = first.source_profile("sample")

    cache_file = next((corpus / "cache" / "profiles").glob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["fingerprint"] = "not-the-current-one"
    payload["profile"] = {"source_id": "sample", "tables": [], "poisoned": True}
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    assert _plane(corpus).source_profile("sample") == expected


def test_the_cache_is_optional(corpus: Path) -> None:
    """With no directory configured nothing is written and nothing breaks --
    tests and embedded uses should not have to opt out of a filesystem."""
    plane = ControlPlane(
        store=ArtifactStore(corpus / "artifacts"),
        source_roots=(corpus / "data",),
        profile_cache_dir=None,
    )
    assert plane.source_profile("sample")["tables"]
    assert not (corpus / "cache").exists()


def test_the_id_never_becomes_a_path(corpus: Path) -> None:
    """Source ids arrive from a URL. They are hashed into the filename rather
    than trusted, so a traversal attempt lands inside the cache directory."""
    plane = _plane(corpus)
    path = plane._profile_cache_path("../../etc/passwd")
    assert path is not None
    assert path.parent == (corpus / "cache" / "profiles")
    assert path.suffix == ".json"
    assert ".." not in path.name
