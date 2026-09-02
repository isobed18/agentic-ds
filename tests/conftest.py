"""Shared fixtures.

The sample dataset is generated once per session into a temp directory: it is
deterministic, so tests can assert exact statistics against it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ads.contracts import DataCard, RelationshipCandidate
from ads.intake import LoadedTable, detect_relationships, load_directory, profile_tables
from ads.testing.sample_data import write_sample_dataset


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_sample_dataset(tmp_path_factory.mktemp("sample_data"))


@pytest.fixture(scope="session")
def loaded_tables(sample_dir: Path) -> list[LoadedTable]:
    return load_directory(sample_dir)


@pytest.fixture(scope="session")
def cards(loaded_tables: list[LoadedTable]) -> list[DataCard]:
    return profile_tables(loaded_tables)


@pytest.fixture(scope="session")
def frames(loaded_tables: list[LoadedTable]) -> dict[str, pd.DataFrame]:
    return {t.name: t.frame for t in loaded_tables}


@pytest.fixture(scope="session")
def cards_by_name(cards: list[DataCard]) -> dict[str, DataCard]:
    return {c.table_name: c for c in cards}


@pytest.fixture(scope="session")
def relationships(
    cards: list[DataCard], frames: dict[str, pd.DataFrame]
) -> list[RelationshipCandidate]:
    return detect_relationships(cards, frames)


LEDGER = "ledger_2019_2024"
MASTER = "physicians__physician_master"
COMPENSATION = "physicians__compensation"
TRANSACTIONS = "transactions"


@pytest.fixture(autouse=True, scope="session")
def _no_live_rlfe_service():
    """Point the external feature service at a closed port for the whole suite.

    The pipeline attempts the service on every run and degrades when it is not
    there, so the default base URL is a live loopback address. Left alone, the
    suite would behave differently on a machine that happens to be running the
    sidecar -- passing or failing on what else is open locally rather than on the
    code. Port 1 refuses immediately, so the unavailable path is what every test
    exercises unless it injects its own client.

    Session-scoped, with its own MonkeyPatch context, deliberately. As a
    function-scoped autouse fixture taking `monkeypatch` it pulled the shared
    function-scoped `monkeypatch` earlier in the setup order, which pushed its
    undo *after* other modules' teardown -- `test_engine_fallback` then tore down
    while its own patch was still installed and raised `'function' object has no
    attribute 'cache_clear'` eight times. Nothing about this fixture needs to be
    per-test, and staying out of that ordering is what keeps it harmless.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ADS_RLFE_API_URL", "http://127.0.0.1:1")
        yield


@pytest.fixture(autouse=True, scope="session")
def _english_assertions():
    """Pin the suite to English.

    Panel prose is now translated, and the default language is Turkish because
    that is who this deployment is for. Tests assert on the English source
    strings, which are also the catalogue keys — so rather than restating every
    assertion in Turkish and having to restate it again for the next language,
    the suite states which language it is testing and the assertions stay
    stable. The Turkish rendering is covered separately in test_i18n.py.
    """
    from ads.api import i18n

    with i18n.using("en"):
        yield
