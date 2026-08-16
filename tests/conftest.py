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
