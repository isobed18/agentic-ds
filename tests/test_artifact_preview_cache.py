"""The preview route tells the truth about an artifact never changing.

#368: switching between project sections unmounts the section's subtree, which
threw away the only cache in the path -- a `useRef` inside `ArtifactNodes`.
Coming back re-issued one `GET /api/artifacts/{id}/preview` per expanded
artifact, on a run that had finished hours ago.

Every one of those refetches is provably redundant. `ads.contracts.base` makes
immutability a contract invariant: a stage produces a new artifact rather than
mutating an existing one, which is what makes fork, time-travel and audit cheap.
So a preview for a given artifact id cannot change, and the response is entitled
to say so. The client-side cache covers a tab switch; this header covers a full
reload, which no in-memory cache can.

`private` rather than `public`: a preview belongs to the signed-in reader's run
and must not be parked in a shared proxy.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ads.api import ControlPlane, create_app
from ads.contracts.datacard import DataCard
from ads.store import ArtifactStore


def _preview_response(tmp_path: Path, **params: str):
    plane = ControlPlane(store=ArtifactStore(tmp_path / "artifacts"))
    ref = plane.store.put(
        DataCard(
            table_name="customers",
            source_uri="fixture://customers.csv",
            source_format="csv",
            n_rows=2,
            n_columns=1,
            columns=[],
            profiled_rows=2,
        ),
        run_id="run-x",
        stage_exec_id="intake",
    )
    client = TestClient(create_app(plane=plane))
    return client.get(f"/api/artifacts/{ref.artifact_id}/preview", params=params)


def test_a_preview_is_cacheable_for_as_long_as_the_reader_keeps_it(tmp_path: Path) -> None:
    response = _preview_response(tmp_path)

    assert response.status_code == 200
    cache_control = response.headers["cache-control"]
    # `immutable` is the part that stops a reload from revalidating; without it
    # a max-age alone still costs a conditional request per artifact.
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control
    # A preview names the run's tables and findings. A shared cache must not
    # hold it on behalf of whoever asks next.
    assert "private" in cache_control
    assert "public" not in cache_control


def test_the_cached_preview_is_not_shared_across_languages(tmp_path: Path) -> None:
    # The prose in a preview is composed per request language, so a cache that
    # ignored language would serve Turkish wording to an English reader. The
    # client puts `?lang=` in the URL, which a URL-keyed cache already
    # separates; `Vary` covers a caller that omits it and sends
    # Accept-Language instead.
    response = _preview_response(tmp_path, lang="en")

    assert response.status_code == 200
    assert "accept-language" in response.headers["vary"].casefold()


def test_a_missing_artifact_is_not_cached_as_one(tmp_path: Path) -> None:
    plane = ControlPlane(store=ArtifactStore(tmp_path / "artifacts"))
    client = TestClient(create_app(plane=plane))

    response = client.get("/api/artifacts/does-not-exist/preview")

    assert response.status_code == 404
    # A 404 is about what the store holds right now, not an immutable fact:
    # caching it for a year would outlive the artifact arriving.
    assert "immutable" not in response.headers.get("cache-control", "")
