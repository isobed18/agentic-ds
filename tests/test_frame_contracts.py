import pandas as pd
import pytest

from ads.training.frame_contracts import (
    FrameContractError,
    validate_feature_partition,
    validate_frame_copy,
)


def test_copy_contract_rejects_duplicate_columns_before_materialization() -> None:
    frame = pd.DataFrame([[1, 2]], columns=["value", "value"])
    with pytest.raises(FrameContractError) as caught:
        validate_frame_copy(frame)
    assert caught.value.code == "frame_schema_invalid"
    assert caught.value.parameters["failure_count"] >= 1


def test_feature_postcondition_rejects_target_exposure() -> None:
    training = pd.DataFrame({"row_id": ["train_1"], "feature": [1], "target": [0]})
    validation = pd.DataFrame({"row_id": ["validation_1"], "feature": [2], "target": [1]})
    with pytest.raises(FrameContractError) as caught:
        validate_feature_partition(
            training=training,
            validation_features=validation,
            target_column="target",
            row_id_column="row_id",
        )
    assert caught.value.code == "feature_validation_target_exposed"


def test_feature_postcondition_rejects_partition_overlap() -> None:
    training = pd.DataFrame({"row_id": ["same"], "feature": [1], "target": [0]})
    validation = pd.DataFrame({"row_id": ["same"], "feature": [2]})
    with pytest.raises(FrameContractError) as caught:
        validate_feature_partition(
            training=training,
            validation_features=validation,
            target_column="target",
            row_id_column="row_id",
        )
    assert caught.value.code == "feature_partition_overlap"
