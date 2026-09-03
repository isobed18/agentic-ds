"""A target column classified as personal must not crash the feature spec (#472).

`FeatureSpec.from_card` skips the target while routing columns, so a target
that is *also* in `excluded_columns` is written into the spec's exclusion
field and never reaches `dropped_columns`. The model validator then requires
`excluded <= dropped` and rejects the spec it was just asked to build -- four
stages after intake, as a raw pydantic traceback.

The target reaches that set because the exclusion set unions in every column
classified as personal, subtracting nothing. Reported on UCI Student
Performance predicting `g3`, where the sensitivity investigator is entitled to
call a student's grade personal data. Excluding the target from the *features*
is meaningless -- the target is never a feature -- so the only thing that
union could ever do to it was crash.
"""

from __future__ import annotations

import pandas as pd

from ads.contracts.datacard import Sensitivity
from ads.contracts.features import FeatureSpec
from ads.contracts.gates import BUILTIN_PROFILES
from ads.contracts.problem import Metric, ProblemDefinition, TaskType
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table
from ads.orchestration import RunState
from ads.pipeline.stages import SOURCE_CARDS_KEY, _excluded_feature_columns
from ads.store import ArtifactStore


def _card(frame: pd.DataFrame, name: str = "student_mat"):
    return profile_table(
        LoadedTable(
            name=name,
            frame=frame,
            source_uri=f"memory://{name}",
            source_format="memory",
        )
    )


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "address": ["U", "R"] * 20,
            "absences": list(range(40)),
            "g1": [10 + (i % 5) for i in range(40)],
            "g2": [11 + (i % 5) for i in range(40)],
            "g3": [12 + (i % 5) for i in range(40)],
        }
    )


def _problem() -> ProblemDefinition:
    return ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="g3",
        primary_metric=Metric.RMSE,
        title="Predict the final grade",
        description="A bounded regression on the student performance data.",
        confirmed_by="auto",
    )


def test_a_personal_target_is_not_excluded_from_its_own_features(tmp_path) -> None:
    """The exclusion set must never contain the column being predicted."""
    frame = _frame()
    card = _card(frame)
    # The sensitivity investigator, or a person using the Personal data
    # override, marks the target as personal. Both write to the source cards.
    source_card = card.model_copy(
        update={
            "columns": [
                column.model_copy(update={"sensitivity": Sensitivity.PII})
                if column.name in {"g3", "g1", "g2", "absences", "address"}
                else column
                for column in card.columns
            ]
        }
    )
    state = RunState(
        run_id="run-pii-target",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[SOURCE_CARDS_KEY] = [source_card]

    excluded = _excluded_feature_columns(state, _problem(), card)

    assert "g3" not in excluded, (
        "the target was excluded from its own feature set; FeatureSpec can "
        f"never route that and refuses the spec. excluded={sorted(excluded)}"
    )
    # The other personal columns are still excluded -- this must not become a
    # licence to feed personal data into the features.
    assert {"g1", "g2", "absences", "address"} <= excluded


def test_the_feature_spec_that_used_to_raise_now_builds(tmp_path) -> None:
    """End to end: the exact call from the report, through the real helper."""
    frame = _frame()
    card = _card(frame)
    source_card = card.model_copy(
        update={
            "columns": [
                column.model_copy(update={"sensitivity": Sensitivity.PII})
                if column.name in {"g3", "g1", "g2", "absences"}
                else column
                for column in card.columns
            ]
        }
    )
    state = RunState(
        run_id="run-pii-target-spec",
        store=ArtifactStore(tmp_path / "artifacts"),
        profile=BUILTIN_PROFILES["full_auto"],
    )
    state.blackboard[SOURCE_CARDS_KEY] = [source_card]

    excluded = _excluded_feature_columns(state, _problem(), card)
    spec = FeatureSpec.from_card(card, target_column="g3", excluded_columns=excluded)

    assert spec.target_column == "g3"
    assert "g3" not in spec.excluded_columns
    assert "g3" not in spec.dropped_columns
    # Every other column really was flagged personal -- `address` by the
    # deterministic classifier, the rest by the investigator -- so the spec
    # builds with no features at all. That is a run worth stopping, and it is
    # now a decision the pipeline can reach rather than a pydantic traceback.
    assert set(spec.numeric_columns) | set(spec.categorical_columns) == set()
    assert set(spec.dropped_columns) == {"address", "absences", "g1", "g2"}
