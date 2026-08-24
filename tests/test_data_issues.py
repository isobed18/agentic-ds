import numpy as np

from ads.training.data_issues import measure_classification_label_issues


def test_cleanlab_summary_measures_known_oof_label_conflicts_without_rows() -> None:
    labels = np.asarray([0] * 20 + [1] * 20)
    probabilities = np.asarray(
        [[0.95, 0.05]] * 18 + [[0.01, 0.99]] * 2 + [[0.05, 0.95]] * 18 + [[0.99, 0.01]] * 2
    )
    result = measure_classification_label_issues(
        labels=labels,
        pred_probs=probabilities,
        eligible_row_count=40,
    )
    assert result.code == "classification_label_issue_candidates"
    assert result.provider == "cleanlab"
    assert result.candidate_issue_count == 4
    assert result.candidate_issue_rate == 0.1
    assert result.evaluated_row_count == 40
    assert "row" not in result.model_dump()
