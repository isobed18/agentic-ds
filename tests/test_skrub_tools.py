import pandas as pd
import pytest

pytest.importorskip("skrub")

from ads.sandbox.skrub_tools import fit_transform_feature_fold  # noqa: E402


def test_skrub_helper_fits_only_training_and_does_not_mutate_inputs() -> None:
    training = pd.DataFrame(
        {
            "__ads_experiment_row_id": ["t1", "t2", "t3", "t4"],
            "dirty_city": ["Istanbul", "istanbul", "Ankara", "Izmir"],
            "event_time": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
            ),
            "target": [0, 1, 0, 1],
        }
    )
    validation = pd.DataFrame(
        {
            "__ads_experiment_row_id": ["v1", "v2"],
            "dirty_city": ["ISTANBUL", "Bursa"],
            "event_time": pd.to_datetime(["2024-02-01", "2024-02-02"]),
        }
    )
    original_training = training.copy(deep=True)
    original_validation = validation.copy(deep=True)

    result = fit_transform_feature_fold(
        training,
        validation,
        target_column="target",
        string_components=3,
    )

    pd.testing.assert_frame_equal(training, original_training)
    pd.testing.assert_frame_equal(validation, original_validation)
    assert "target" not in result.training.columns
    assert list(result.training.columns) == list(result.validation.columns)
    assert not any("Bursa" in str(column) for column in result.training.columns)
    assert result.validation["__ads_experiment_row_id"].tolist() == ["v1", "v2"]
