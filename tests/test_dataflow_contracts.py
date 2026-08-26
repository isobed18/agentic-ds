from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.automation.catalog import automation_component_catalog, instantiate_component
from ads.contracts.dataflow import FoldSelection, RowSelection, SplitManifest
from ads.contracts.registry import canonical_contract_id, contract_definition
from ads.dataflow import load_table_asset, persist_table_asset
from ads.store import ArtifactStore


def _selection(positions: list[int]) -> RowSelection:
    fingerprint = hashlib.sha256(json.dumps(positions, separators=(",", ":")).encode()).hexdigest()
    return RowSelection(count=len(positions), fingerprint=fingerprint, positions=positions)


def test_table_asset_round_trips_as_verified_parquet_blob(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    frame = pd.DataFrame({"entity": [1, 2, 3], "amount": [10.5, 11.0, None]})

    asset, first = persist_table_asset(
        store,
        frame,
        run_id="run-1",
        producer_component_id="integrate-data",
        source_artifact_ids=["source-a"],
    )
    _, second = persist_table_asset(
        store,
        frame.copy(),
        run_id="run-1",
        producer_component_id="integrate-data",
        source_artifact_ids=["source-a"],
    )

    assert first.artifact_id == second.artifact_id
    assert asset.row_count == 3
    assert asset.columns[1].nullable is True
    loaded_asset, loaded = load_table_asset(store, first.artifact_id)
    assert loaded_asset.fingerprint == asset.fingerprint
    pd.testing.assert_frame_equal(loaded, frame)


def test_split_manifest_pins_exact_table_strategy_and_rows() -> None:
    manifest = SplitManifest(
        table_asset_id="a" * 64,
        table_fingerprint="b" * 64,
        validation_strategy_artifact_id="c" * 64,
        target_column="target",
        outer_train=_selection([0, 1, 2, 3]),
        holdout=_selection([4, 5]),
        folds=[FoldSelection(fold=0, train=_selection([0, 1]), validation=_selection([2, 3]))],
    )
    assert manifest.summary()["holdout_rows"] == 2

    with pytest.raises(ValidationError, match="outside outer training"):
        SplitManifest.model_validate(
            {
                **manifest.model_dump(),
                "folds": [
                    FoldSelection(fold=0, train=_selection([0, 1]), validation=_selection([5]))
                ],
            }
        )


def test_contract_registry_accepts_legacy_alias_but_rejects_unknown_contract() -> None:
    assert canonical_contract_id("integrated_table") == "ads.table_asset@1"
    assert contract_definition("ads.split_manifest@1").durable is True
    with pytest.raises(ValueError, match="unregistered graph contract"):
        canonical_contract_id("planner.made_this_up@1")


def test_catalog_exposes_and_enforces_one_pydantic_settings_schema() -> None:
    training = next(
        definition
        for definition in automation_component_catalog()
        if definition.catalog_id == "ml.train"
    )
    assert "candidate_limit" in training.settings_schema["properties"]
    assert '"maximum": 20' in json.dumps(training.settings_schema)
    assert training.executor_id == "ads.executor.ml.train"
    configured = instantiate_component("ml.train", "training-a", settings={"candidate_limit": 2})
    assert configured.settings["candidate_limit"] == 2
    with pytest.raises(ValidationError):
        instantiate_component("ml.train", "training-b", settings={"candidate_limit": 200})
