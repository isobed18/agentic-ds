"""Joinable multi-table uploads that measured zero relationships (#381).

Uploading the two UCI Student Performance CSVs together dead-ended the run:
``detect_relationships`` skipped all 1089 column pairs because neither table has
a single unique column, the investigator was told verbatim that no candidates
existed, ``join_overlap`` could not have checked a composite join even if it had
guessed one, and schema discovery emitted an ``agent_audit`` with no
``integration_plan`` -- so the gate said the stage produced nothing and offered
rework or stop. Either file alone worked fine.

The shape is reproduced here rather than the dataset: two course-split exports
of one survey, sharing thirteen demographic columns and nothing else, with no
unique column anywhere on either side.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ads.intake.keys import (
    KeyDetectionOptions,
    composite_join_key,
    detect_primary_keys,
    detect_relationships,
    measure_composite_relationship,
)
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table

SHARED = [f"demo_{index}" for index in range(13)]


def _demographics(rows: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {name: rng.integers(0, 3 if index % 2 else 5, rows) for index, name in enumerate(SHARED)}
    )


def _course(demographics: pd.DataFrame, prefix: str, seed: int) -> pd.DataFrame:
    frame = demographics.copy()
    rng = np.random.default_rng(seed)
    for index in range(20):
        frame[f"{prefix}_{index}"] = rng.integers(0, 20, len(frame))
    return frame


@pytest.fixture
def survey() -> tuple[list, dict[str, pd.DataFrame]]:
    """395 and 649 rows sharing 382 students, exactly the reported proportions."""
    maths_demographics = _demographics(395, 1)
    portuguese_demographics = pd.concat(
        [maths_demographics.iloc[:382], _demographics(267, 2)], ignore_index=True
    )
    frames = {
        "student_mat": _course(maths_demographics, "mat", 3),
        "student_por": _course(portuguese_demographics, "por", 4),
    }
    cards = [
        profile_table(LoadedTable(name=name, frame=frame, source_uri=name, source_format="csv"))
        for name, frame in frames.items()
    ]
    return cards, {card.table_name: frames[card.table_name] for card in cards}


def test_the_reported_shape_still_has_no_single_column_key(survey):
    """The premise: nothing here is unique, which is what silenced detection."""
    cards, frames = survey
    for card in cards:
        assert detect_primary_keys(card, frames[card.table_name]) == []
        assert not any(column.is_unique for column in card.columns)


def test_a_shared_schema_join_is_measured_rather_than_skipped(survey):
    cards, frames = survey
    found = detect_relationships(cards, frames)

    assert found, "two tables sharing their demographic schema must not measure zero"
    composite = next(relationship for relationship in found if len(relationship.from_columns) > 1)
    # The columns the two tables actually have in common, not a guess.
    assert set(composite.from_columns) == set(SHARED)
    assert composite.from_columns == composite.to_columns
    # 382 of 395 rows match, as in the dataset's own merge script.
    assert composite.overlap_rate == pytest.approx(382 / 395, abs=0.01)


def test_a_composite_key_is_measured_row_wise_not_column_wise(survey):
    """Nulls are dropped per row, so parts of different rows never compare."""
    frame = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", None]})
    key = composite_join_key(frame, ["a", "b"])
    assert list(key.index) == [0]
    assert key.iloc[0].split("\x1f") == ["1", "x"]


def test_an_unrelated_pair_is_not_talked_into_a_join():
    """The floor still does the deciding; sharing names is only a reason to look."""
    rng = np.random.default_rng(11)
    left = pd.DataFrame({name: rng.integers(0, 1000, 400) for name in SHARED})
    right = pd.DataFrame({name: rng.integers(2000, 3000, 400) for name in SHARED})
    frames = {"left": left, "right": right}
    cards = [
        profile_table(LoadedTable(name=name, frame=frame, source_uri=name, source_format="csv"))
        for name, frame in frames.items()
    ]

    assert detect_relationships(cards, frames) == []


def test_the_widest_matching_key_wins_not_the_loosest():
    """Narrowing stops at the most specific key that still matches.

    Three columns always "match" between two small tables of small integers, so
    a search that narrowed until something passed would report noise. Starting
    wide and stopping at the first set that clears the floor is what prevents it.
    """
    rng = np.random.default_rng(5)
    keys = pd.DataFrame({name: rng.integers(0, 4, 300) for name in SHARED})
    frames = {"left": keys.copy(), "right": keys.iloc[:280].copy()}
    cards = [
        profile_table(LoadedTable(name=name, frame=frame, source_uri=name, source_format="csv"))
        for name, frame in frames.items()
    ]

    found = detect_relationships(cards, frames)
    assert found
    assert len(found[0].from_columns) == len(SHARED)


def test_detection_can_be_switched_off_and_is_bounded(survey):
    cards, frames = survey
    off = KeyDetectionOptions(detect_shared_schema_joins=False)
    assert detect_relationships(cards, frames, off) == []
    # Above the table budget the pass is skipped rather than run pairwise.
    capped = KeyDetectionOptions(max_shared_join_tables=1)
    assert detect_relationships(cards, frames, capped) == []


def test_measure_composite_relationship_reports_the_columns_it_measured(survey):
    cards, frames = survey
    measured = measure_composite_relationship(
        "student_mat", SHARED, frames["student_mat"], "student_por", SHARED, frames["student_por"]
    )
    assert measured.from_columns == SHARED
    assert measured.to_columns == SHARED
    assert measured.name_affinity == 1.0
    assert measured.orphan_rate == pytest.approx(1.0 - measured.overlap_rate, abs=1e-6)


def test_join_overlap_can_verify_a_composite_join(survey):
    """The tool the investigator is handed can now check what it is told.

    It took singular ``from_column``/``to_column``, so an agent told "no
    candidate relationships found above the overlap threshold" had no way to
    test a suspected composite join -- it guessed, failed the grain trial, and
    burned all twelve turns.
    """
    from ads.tools import ToolRuntime
    from ads.tools.ds import join_overlap

    cards, frames = survey
    runtime = ToolRuntime.from_sources(cards, frames, run_id="composite-join")
    try:
        payload = join_overlap(
            runtime,
            {
                "from_table": "student_mat",
                "from_columns": SHARED,
                "to_table": "student_por",
                "to_columns": SHARED,
            },
        )
        assert payload.data["from_columns"] == SHARED
        assert payload.data["overlap_rate"] == pytest.approx(382 / 395, abs=0.01)

        # The singular spelling every existing call uses still works.
        single = join_overlap(
            runtime,
            {
                "from_table": "student_mat",
                "from_column": SHARED[0],
                "to_table": "student_por",
                "to_column": SHARED[0],
            },
        )
        assert single.data["from_columns"] == [SHARED[0]]
    finally:
        runtime.close()


def test_join_overlap_refuses_a_lopsided_composite(survey):
    from ads.tools import ToolRuntime
    from ads.tools.ds import join_overlap

    cards, frames = survey
    runtime = ToolRuntime.from_sources(cards, frames, run_id="lopsided-join")
    try:
        with pytest.raises(ValueError, match="same number of columns"):
            join_overlap(
                runtime,
                {
                    "from_table": "student_mat",
                    "from_columns": SHARED,
                    "to_table": "student_por",
                    "to_columns": SHARED[:2],
                },
            )
    finally:
        runtime.close()


def test_two_unjoinable_tables_degrade_to_a_warning_not_a_dead_end(tmp_path):
    """The reported stop, and what should happen instead (#381).

    Two files that each work perfectly well alone used to end the run: schema
    discovery emitted an ``agent_audit`` and no ``integration_plan``, so the
    next stage's declared input was missing and the gate offered rework or stop.
    One table already degrades to a verified no-join plan over a row identity;
    several now do the same, on the largest, with an explicit warning naming
    what was left out.
    """
    from tests.test_pipeline_agents import FakeLLM

    from ads.contracts.base import ArtifactType
    from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict
    from ads.contracts.integration import IntegrationPlan
    from ads.orchestration import RunState, linear_spec, run_workflow
    from ads.pipeline import (
        build_full_spec,
        build_pipeline_rubrics,
        configure_full_pipeline_state,
    )
    from ads.store import ArtifactStore

    source = tmp_path / "unjoinable"
    source.mkdir()
    rng = np.random.default_rng(3)
    pd.DataFrame({"size": rng.integers(0, 4, 120), "grade": rng.integers(0, 6, 120)}).to_csv(
        source / "maths.csv", index=False
    )
    pd.DataFrame(
        {"height": rng.integers(50, 90, 200), "weight": rng.integers(10, 40, 200)}
    ).to_csv(source / "portuguese.csv", index=False)

    # Every investigator turn comes back unparseable, so no member submits a
    # tested plan -- the state the reported run reached after twelve turns.
    llm = FakeLLM(["not json"] * 60)
    full_spec, registry = build_full_spec(llm)
    selected = tuple(
        stage
        for stage in full_spec.stages
        if stage.id in {"intake", "schema_discovery", "integration"}
    )
    # The orchestrator's retry loop comes first; the fallback is the floor under
    # it, so the stage has to have been sent back once before it applies.
    spec = linear_spec(
        "unjoinable-multi-table", "1", selected, retry_stages=("schema_discovery",)
    )
    state = RunState(
        run_id="unjoinable-multi-table",
        store=ArtifactStore(tmp_path / "store"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    configure_full_pipeline_state(state, source_path=source, candidate_limit=2, validation_folds=3)

    outcome = run_workflow(spec, registry, state, rubrics=build_pipeline_rubrics())

    assert outcome.completed, outcome.error
    plan = state.require(ArtifactType.INTEGRATION_PLAN, IntegrationPlan)
    # The larger table, chosen deterministically, with the smaller one named.
    assert plan.base_table == "portuguese"
    assert any("continues on portuguese alone" in warning for warning in plan.warnings)
    assert any("maths" in warning for warning in plan.warnings)
    # The row-identity fallback still applies, and the run keeps going.
    assert plan.base_grain == ["__ads_row_id"]
    attempts = state.attempts_for("schema_discovery")
    assert len(attempts) == 2, "the retry is still tried before the fallback applies"
    assert attempts[0].decision.verdict is GateVerdict.RETRY
    assert attempts[1].decision.verdict is GateVerdict.AUTO_PROCEED


def test_the_no_relationships_message_names_the_next_step():
    """It read as "these tables are unrelated"; it means "the pass found none"."""
    from ads.intake.keys import relationships_digest

    message = relationships_digest([])
    assert "not that the tables are unrelated" in message
    assert "from_columns/to_columns" in message
