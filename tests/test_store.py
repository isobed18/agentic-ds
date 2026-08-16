"""Artifact store tests.

The content-addressing properties asserted here are what later make stage resume
safe: because re-running a stage that produces identical output yields an
identical id, a node restarted from the top on resume cannot duplicate state.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ads.contracts import ArtifactType, SplitStrategy, ValidationStrategy
from ads.contracts.problem import Metric, ProblemDefinition, TaskType
from ads.store import ArtifactNotFoundError, ArtifactStore, compute_artifact_id


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


def _strategy(rationale: str = "iid data") -> ValidationStrategy:
    return ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale=rationale)


class TestContentAddressing:
    def test_identical_content_yields_identical_id(self) -> None:
        assert compute_artifact_id(_strategy()) == compute_artifact_id(_strategy())

    def test_different_content_yields_different_id(self) -> None:
        assert compute_artifact_id(_strategy("a")) != compute_artifact_id(_strategy("b"))

    def test_created_at_excluded_from_hash(self) -> None:
        """Otherwise every re-run would mint a new id and defeat idempotent writes."""
        first = _strategy()
        time.sleep(0.01)
        second = _strategy()
        assert first.created_at != second.created_at
        assert compute_artifact_id(first) == compute_artifact_id(second)

    def test_different_types_do_not_collide(self) -> None:
        problem = ProblemDefinition(
            task_type=TaskType.REGRESSION,
            target_column="annual_comp",
            primary_metric=Metric.RMSE,
            title="t",
            description="d",
        )
        assert compute_artifact_id(problem) != compute_artifact_id(_strategy())


class TestRoundTrip:
    def test_put_then_load_typed(self, store: ArtifactStore) -> None:
        original = _strategy("grouped because entities repeat")
        ref = store.put(original, run_id="run1", name="validation")
        loaded = store.load(ref.artifact_id, ValidationStrategy)
        assert loaded.rationale == original.rationale
        assert loaded.strategy is SplitStrategy.RANDOM

    def test_load_without_model_resolves_from_index(self, store: ArtifactStore) -> None:
        ref = store.put(_strategy(), run_id="run1")
        loaded = store.load(ref.artifact_id)
        assert isinstance(loaded, ValidationStrategy)

    def test_missing_artifact_raises(self, store: ArtifactStore) -> None:
        with pytest.raises(ArtifactNotFoundError):
            store.load("0" * 64, ValidationStrategy)


class TestIdempotency:
    def test_double_put_is_stable(self, store: ArtifactStore) -> None:
        first = store.put(_strategy(), run_id="run1", name="v")
        second = store.put(_strategy(), run_id="run1", name="v")
        assert first.artifact_id == second.artifact_id
        assert len(store.list("run1")) == 1

    def test_double_put_preserves_original_created_at(self, store: ArtifactStore) -> None:
        first = store.put(_strategy(), run_id="run1")
        time.sleep(0.01)
        second = store.put(_strategy(), run_id="run1")
        assert first.created_at == second.created_at


class TestQuerying:
    def test_list_filters_by_type_and_run(self, store: ArtifactStore) -> None:
        store.put(_strategy("a"), run_id="run1")
        store.put(_strategy("b"), run_id="run1")
        store.put(_strategy("c"), run_id="run2")

        assert len(store.list("run1")) == 2
        assert len(store.list("run2")) == 1
        assert len(store.list("run1", artifact_type=ArtifactType.VALIDATION_STRATEGY)) == 2
        assert store.list("run1", artifact_type=ArtifactType.EDA_REPORT) == []

    def test_list_filters_by_name(self, store: ArtifactStore) -> None:
        store.put(_strategy("a"), run_id="run1", name="first")
        store.put(_strategy("b"), run_id="run1", name="second")
        refs = store.list("run1", name="second")
        assert len(refs) == 1
        assert refs[0].name == "second"

    def test_latest_returns_none_when_absent(self, store: ArtifactStore) -> None:
        assert store.latest("nope", ArtifactType.DATA_CARD) is None

    def test_summary_is_indexed(self, store: ArtifactStore) -> None:
        ref = store.put(_strategy(), run_id="run1")
        assert ref.summary["strategy"] == "random"
        assert store.list("run1")[0].summary["n_folds"] == 5

    def test_load_all_typed(self, store: ArtifactStore) -> None:
        store.put(_strategy("a"), run_id="run1")
        store.put(_strategy("b"), run_id="run1")
        items = store.load_all("run1", ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
        assert len(items) == 2
        assert {i.rationale for i in items} == {"a", "b"}


class TestPersistence:
    def test_store_survives_reopen(self, tmp_path: Path) -> None:
        root = tmp_path / "artifacts"
        ref = ArtifactStore(root).put(_strategy(), run_id="run1", name="v")
        reopened = ArtifactStore(root)
        assert reopened.load(ref.artifact_id, ValidationStrategy).strategy is SplitStrategy.RANDOM
        assert len(reopened.list("run1")) == 1

    def test_blob_dir_is_created(self, store: ArtifactStore) -> None:
        ref = store.put(_strategy(), run_id="run1")
        blobs = store.blob_dir(ref.artifact_id)
        assert blobs.is_dir()
