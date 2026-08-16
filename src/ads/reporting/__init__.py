"""Evaluation artifact construction and human-facing rendering."""

from ads.contracts.reporting import (
    CandidateComparison,
    DecisionAuthority,
    DecisionRecord,
    EvaluationAlert,
    EvaluationReport,
    GateHistoryRecord,
    HoldoutMetric,
    LeakageDisposition,
)
from ads.reporting.evaluation import build_evaluation_report, load_gate_decisions
from ads.reporting.markdown import render_markdown
from ads.store import register_artifact_type

register_artifact_type(EvaluationReport)

__all__ = [
    "CandidateComparison",
    "DecisionAuthority",
    "DecisionRecord",
    "EvaluationAlert",
    "EvaluationReport",
    "GateHistoryRecord",
    "HoldoutMetric",
    "LeakageDisposition",
    "build_evaluation_report",
    "load_gate_decisions",
    "render_markdown",
]
