"""A promoted PDF-table candidate must reach intake, not just the store.

`promote_reviewed_document_tables` (ads.documents.promotion) has always written
its accepted candidates as TableAsset artifacts, but nothing downstream ever
read them back: `intake_stage` loaded exclusively from the run's source
directory, so a promoted table sat in the artifact store, retrievable by id,
while the agent that runs next never saw it as a table it could use. From the
UI this looked like promotion doing nothing at all -- the review dialog said
"N tables promoted", and the pipeline behaved as if none had been.

The fix folds any `document-table-promotion` TableAsset for this run into the
same list of tables intake profiles from disk. It cannot instead write the
promoted table into the source directory itself, because a source with
execution history is immutable (#111) -- these tests exist to prove the
in-store path works without touching it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ads.contracts.gates import BUILTIN_PROFILES
from ads.dataflow import persist_table_asset
from ads.orchestration import RunState
from ads.pipeline.stages import SOURCE_CARDS_KEY, SOURCE_FRAMES_KEY, SOURCE_PATH_KEY, intake_stage
from ads.store import ArtifactStore

FULL_AUTO = BUILTIN_PROFILES["full_auto"]


def _state(tmp_path: Path, source_dir: Path, run_id: str) -> tuple[RunState, ArtifactStore]:
    store = ArtifactStore(tmp_path / run_id)
    state = RunState(run_id=run_id, store=store, profile=FULL_AUTO)
    state.blackboard[SOURCE_PATH_KEY] = source_dir
    return state, store


def test_a_promoted_document_table_is_merged_into_intake(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    pd.DataFrame({"id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]}).to_csv(
        source_dir / "orders.csv", index=False
    )
    state, store = _state(tmp_path, source_dir, "run-promoted-table")

    persist_table_asset(
        store,
        pd.DataFrame({"quarter": ["Q1", "Q2"], "revenue": [1000, 1200]}),
        run_id=state.run_id,
        producer_component_id="document-table-promotion",
        stage_exec_id="document-table-promotion",
        name="quarterly_orders_pdf_table_1",
    )

    intake_stage(state)

    table_names = {card.table_name for card in state.blackboard[SOURCE_CARDS_KEY]}
    assert table_names == {"orders", "quarterly_orders_pdf_table_1"}
    assert set(state.blackboard[SOURCE_FRAMES_KEY]) == table_names
    promoted = state.blackboard[SOURCE_FRAMES_KEY]["quarterly_orders_pdf_table_1"]
    assert list(promoted.columns) == ["quarter", "revenue"]
    assert len(promoted) == 2


def test_intake_ignores_table_assets_from_other_stages(tmp_path: Path) -> None:
    """Only document-table-promotion output is folded in -- not, say, a prior
    attempt's own integrated ABT still sitting in the same run's store."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    pd.DataFrame({"id": [1]}).to_csv(source_dir / "orders.csv", index=False)
    state, store = _state(tmp_path, source_dir, "run-unrelated-table")

    persist_table_asset(
        store,
        pd.DataFrame({"x": [1]}),
        run_id=state.run_id,
        producer_component_id="integrate-data",
        stage_exec_id="integration",
        name="integrated_table",
    )

    intake_stage(state)

    table_names = {card.table_name for card in state.blackboard[SOURCE_CARDS_KEY]}
    assert table_names == {"orders"}
