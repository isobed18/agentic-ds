"""An anomaly-detection framing is refused where it is proposed (#466).

"Flag unusual rows" was selectable in two pickers, buildable by
`_quick_problem_proposal`, and proposable by the agent -- and then fatal in
`feature_pipeline_stage`, which raises on a null target. Intake, integration,
EDA and the leakage audit had all run by then, and the message read like a
misconfiguration a person could fix.

Supporting it is not one missing branch. `default_candidates` has no
unsupervised menu and raises; `training_stage` raises on the same null target;
`SplitManifest` carries a target column and `evaluation` scores against one.
The two stages that *were* written to handle a null target -- `splitting_stage`
and `rl_feature_engineering_stage` -- both run after `feature_pipeline`, so
neither branch had ever executed. That dead code is the evidence: the raise is
not an oversight in one stage, it is the shape of the whole spine.

So the framing is refused, as the issue's second option asks -- with a measured
reason, at problem discovery, the way an unviable target is refused today.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.contracts.problem import TaskType
from ads.discovery.support import UNSUPPORTED_UNSUPERVISED_FRAMING, compute_support
from ads.intake.loaders import LoadedTable
from ads.intake.profiler import profile_table
from ads.training.candidates import default_candidates


def _frame() -> pd.DataFrame:
    # 200 rows so the supervised comparison below is viable on its own terms:
    # the minority class needs 50 examples before support stops blocking.
    return pd.DataFrame(
        {
            "amount": [float(index) for index in range(200)],
            "region": ["north", "south"] * 100,
            "label": [index % 2 for index in range(200)],
        }
    )


def _card(frame: pd.DataFrame):
    return profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="mem", source_format="csv")
    )


class TestTheFramingIsRefusedWhereItIsMeasured:
    def test_an_anomaly_framing_is_not_viable_however_good_the_data_is(self) -> None:
        # Rows and features are fine here -- 200 rows, two usable columns. The
        # blocking reason is about the pipeline, not the data, and saying so is
        # the point: nothing about this dataset can be changed to make it run.
        frame = _frame()

        support = compute_support(
            _card(frame), frame, target_column=None, task_type=TaskType.ANOMALY_DETECTION
        )

        assert support.is_viable is False
        assert UNSUPPORTED_UNSUPERVISED_FRAMING in support.blocking_reasons

    def test_the_data_reasons_are_still_measured_alongside_it(self) -> None:
        # The framing reason does not replace the ones a person could act on;
        # a three-row source is still reported as too small.
        frame = pd.DataFrame({"amount": [1.0, 2.0, 3.0]})

        support = compute_support(
            _card(frame), frame, target_column=None, task_type=TaskType.ANOMALY_DETECTION
        )

        assert any(reason.startswith("insufficient_rows") for reason in support.blocking_reasons)
        assert UNSUPPORTED_UNSUPERVISED_FRAMING in support.blocking_reasons

    def test_a_supervised_framing_on_the_same_data_is_untouched(self) -> None:
        frame = _frame()

        support = compute_support(
            _card(frame),
            frame,
            target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )

        assert support.is_viable is True
        assert support.blocking_reasons == []


class TestTheSpineCouldNotHaveRunIt:
    """Why refusing is the honest answer rather than adding one branch."""

    def test_there_is_no_unsupervised_candidate_menu(self) -> None:
        with pytest.raises(ValueError, match="No supervised candidate menu exists"):
            default_candidates(TaskType.ANOMALY_DETECTION)

    def test_training_requires_a_target_too(self) -> None:
        # `feature_pipeline` is not the only wall, so giving it a branch would
        # have moved the same failure one stage later.
        from pathlib import Path

        stages = Path("src/ads/pipeline/stages.py").read_text(encoding="utf-8")
        training = stages[
            stages.index("def training_stage(") : stages.index("def evaluation_stage(")
        ]
        assert "Deterministic training requires a supervised target column." in training

    def test_the_feature_stage_says_what_is_actually_true(self) -> None:
        # A run framed before this existed still reaches the raise. The message
        # used to read like a misconfiguration; it names the framing now.
        from pathlib import Path

        stages = Path("src/ads/pipeline/stages.py").read_text(encoding="utf-8")
        # `splitting_stage` is defined *above* this one in the file -- which is
        # the shape of the bug: the null-target branch it holds runs after.
        feature = stages[
            stages.index("def feature_pipeline_stage(") : stages.index("def _rlfe_client(")
        ]
        assert "has no executable pipeline" in feature
        assert "Feature preparation requires a supervised target column." not in feature


class TestTheRequestIsRefusedAtTheDoor:
    """A blocking reason costs a stage; a rejected request costs nothing."""

    def test_starting_a_run_with_the_selection_is_refused(self) -> None:
        from pathlib import Path

        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        assert service.count("raise ValueError(UNSUPPORTED_UNSUPERVISED_FRAMING)") == 2

    def test_pinning_it_as_a_reframe_is_refused(self) -> None:
        from pathlib import Path

        service = Path("src/ads/api/service.py").read_text(encoding="utf-8")
        pin = service[
            service.index("def pin_problem_framing(") : service.index("def answer_run(")
        ]
        assert 'if kind == "flag_anomalies":' in pin
        assert "raise ValueError(UNSUPPORTED_UNSUPERVISED_FRAMING)" in pin


class TestTheContractsStayLegal:
    def test_the_task_type_and_its_metric_are_not_removed(self) -> None:
        # Refused, not deleted: the day an unsupervised candidate menu exists,
        # one blocking reason is what has to come out. Removing the task type
        # would make that a contract migration instead.
        from ads.contracts.problem import METRICS_BY_TASK, Metric

        assert Metric.SILHOUETTE in METRICS_BY_TASK[TaskType.ANOMALY_DETECTION]
