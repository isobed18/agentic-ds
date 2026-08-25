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
