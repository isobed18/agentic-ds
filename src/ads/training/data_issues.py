"""Deterministic, row-free data-issue measurements from OOF predictions."""

from __future__ import annotations

import numpy as np
from cleanlab.filter import find_label_issues
from cleanlab.rank import get_label_quality_scores

from ads.contracts.training import LabelIssueMeasurement


def measure_classification_label_issues(
    *,
    labels: np.ndarray,
    pred_probs: np.ndarray,
    eligible_row_count: int,
) -> LabelIssueMeasurement:
    """Summarize possible label issues without retaining row-level rankings."""
    labels = np.asarray(labels)
    pred_probs = np.asarray(pred_probs, dtype=float)
    if labels.ndim != 1 or pred_probs.ndim != 2 or len(labels) != len(pred_probs):
        raise ValueError("OOF labels and probabilities have incompatible shapes.")
    if len(labels) == 0 or eligible_row_count < len(labels):
        raise ValueError("OOF measurement population is invalid.")
    if not np.isfinite(pred_probs).all() or not np.allclose(pred_probs.sum(axis=1), 1.0):
        raise ValueError("OOF class probabilities must be finite and sum to one.")
    issues = find_label_issues(
        labels=labels.astype(int),
        pred_probs=pred_probs,
        n_jobs=1,
        verbose=False,
    )
    quality = get_label_quality_scores(labels.astype(int), pred_probs)
    issue_count = int(np.asarray(issues, dtype=bool).sum())
    evaluated = int(len(labels))
    return LabelIssueMeasurement(
        evaluated_row_count=evaluated,
        eligible_row_count=eligible_row_count,
        candidate_issue_count=issue_count,
        candidate_issue_rate=round(issue_count / evaluated, 6),
        mean_label_quality=round(float(np.mean(quality)), 6),
        p10_label_quality=round(float(np.quantile(quality, 0.10)), 6),
    )


__all__ = ["measure_classification_label_issues"]
