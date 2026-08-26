"""Registered leakage challenges: typed proposals, measured results, no agent veto."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest

from ads.agents.leakage_investigator import investigate_leakage
from ads.contracts.leakage import (
    LeakageChallenge,
    LeakageChallengeOutcome,
    LeakageChallengeProposal,
    LeakageFinding,
    LeakageKind,
    LeakageReport,
    leakage_challenge_fingerprint,
    leakage_finding_fingerprint,
)
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.tools import ToolRuntime
from ads.tools.leakage import recorded_before_prediction


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "outcome": [100.0, 200.0, 300.0],
            "leaky_total": [99.0, 201.0, 301.0],
            "feature_recorded_at": [
                "2024-01-01T09:00:00Z",
                "2024-01-02T09:00:00Z",
                "2024-01-03T09:00:00Z",
            ],
            "prediction_time": [
                "2024-01-01T10:00:00Z",
                "2024-01-02T10:00:00Z",
                "2024-01-03T10:00:00Z",
            ],
        }
    )


def _finding(kind: LeakageKind = LeakageKind.TARGET_CORRELATION) -> LeakageFinding:
    return LeakageFinding(
        column="leaky_total",
        kind=kind,
        score=0.999,
        threshold=0.95,
        blocking=True,
        detail="Measured relationship exceeds the leakage threshold.",
        suggested_action="Drop or establish prediction-time availability.",
    )


def _proposal(finding: LeakageFinding) -> LeakageChallengeProposal:
    return LeakageChallengeProposal(
        finding_fingerprint=leakage_finding_fingerprint(finding),
        test_kind="recorded_before_prediction",
        recorded_at_column="feature_recorded_at",
        prediction_time_column="prediction_time",
        hypothesis="The suspect value was recorded before each prediction was made.",
        interpretation="The relationship may encode a legitimate pre-existing business rule.",
        why_it_matters="Dropping a legitimate rule would discard useful signal.",
        verification_question=(
            "Does feature_recorded_at truly record when leaky_total became available?"
        ),
    )


def _runtime(frame: pd.DataFrame, finding: LeakageFinding) -> ToolRuntime:
    return ToolRuntime(
        frames={"abt": frame},
        resources={"leakage_findings": {leakage_finding_fingerprint(finding): finding}},
    )


def _strategy() -> ValidationStrategy:
    return ValidationStrategy(
        strategy=SplitStrategy.TEMPORAL,
        n_folds=3,
        test_size=0.2,
        time_column="prediction_time",
        holdout_cutoff="2024-01-03",
        rationale="Later predictions represent deployment.",
    )


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls = 0

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        del system, prompt, json_schema
        item = self.responses[self.calls]
        self.calls += 1
        return LLMResponse(
            text=json.dumps(item),
            model=profile.name,
            latency_s=0.0,
            parsed=item,
        )


class FailingLLM:
    def generate_structured(self, **kwargs: Any) -> LLMResponse:
        del kwargs
        raise KeyError("local model response shape changed")


def test_registered_timestamp_test_supports_review_but_cannot_clear_gate() -> None:
    finding = _finding()
    proposal = _proposal(finding)

    payload = recorded_before_prediction(
        _runtime(_frame(), finding), {"proposal": proposal.model_dump()}
    )

    assert payload.data["outcome"] == "supports_human_review"
    assert payload.data["gate_effect"] == "human_review_required"
    assert payload.data["tested_rows"] == 3
    assert payload.data["recorded_after_prediction_rows"] == 0
    assert payload.data["challenge_fingerprint"] == leakage_challenge_fingerprint(proposal)
    challenge_data = dict(payload.data)
    challenge_data.pop("challenge_fingerprint")
    enriched = LeakageReport(
        target_column="outcome",
        n_features_checked=1,
        findings=[finding],
        challenges=[LeakageChallenge.model_validate(challenge_data)],
    )
    assert enriched.to_quality_signals().leakage_challenge_review_columns == ["leaky_total"]


def test_incomplete_timestamp_coverage_is_indeterminate() -> None:
    finding = _finding()
    frame = _frame()
    frame.loc[1, "feature_recorded_at"] = None

    payload = recorded_before_prediction(
        _runtime(frame, finding), {"proposal": _proposal(finding).model_dump()}
    )

    assert payload.data["outcome"] == "indeterminate"
    assert payload.data["missing_timestamp_rows"] == 1


def test_report_rejects_a_decorative_challenge_bound_to_the_wrong_subject() -> None:
    finding = _finding()
    payload = recorded_before_prediction(
        _runtime(_frame(), finding), {"proposal": _proposal(finding).model_dump()}
    )
    challenge_data = dict(payload.data)
    challenge_data.pop("challenge_fingerprint")
    challenge_data["finding_column"] = "some_other_column"
    challenge = LeakageChallenge.model_validate(challenge_data)

    with pytest.raises(ValueError, match="subject does not match"):
        LeakageReport(
            target_column="outcome",
            n_features_checked=1,
            findings=[finding],
            challenges=[challenge],
        )


def test_post_prediction_recording_refutes_the_challenge() -> None:
    finding = _finding()
    frame = _frame()
    frame.loc[2, "feature_recorded_at"] = "2024-01-03T11:00:00Z"

    payload = recorded_before_prediction(
        _runtime(frame, finding), {"proposal": _proposal(finding).model_dump()}
    )

    assert payload.data["outcome"] == "refutes_challenge"
    assert payload.data["recorded_after_prediction_rows"] == 1


def test_timestamp_test_cannot_be_used_for_missingness_leakage() -> None:
    finding = _finding(LeakageKind.MISSINGNESS_SEPARATOR)

    try:
        recorded_before_prediction(
            _runtime(_frame(), finding), {"proposal": _proposal(finding).model_dump()}
        )
    except ValueError as exc:
        assert "cannot challenge missingness_separator" in str(exc)
    else:  # pragma: no cover - the test exists to make this bypass impossible
        raise AssertionError("incompatible challenge test was accepted")


def test_agent_must_finish_the_exact_registered_challenge() -> None:
    finding = _finding()
    proposal = _proposal(finding)
    frame = _frame()
    card = profile_table(LoadedTable("abt", frame, "memory://abt", "csv"))
    report = LeakageReport(
        target_column="outcome",
        n_features_checked=1,
        findings=[finding],
        split_strategy=SplitStrategy.TEMPORAL,
    )
    llm = ScriptedLLM(
        [
            {
                "action": "call_tool",
                "tool_id": "test_recorded_before_prediction",
                "arguments": {"proposal": proposal.model_dump(mode="json")},
                "reason": "Run a registered timestamp ordering test.",
            },
            {
                "action": "finish",
                "challenge_fingerprint": leakage_challenge_fingerprint(proposal),
                "reason": "Attach the exact registered result to the leakage finding.",
            },
        ]
    )

    result = investigate_leakage(
        card=card,
        report=report,
        strategy=_strategy(),
        llm=llm,
        runtime=_runtime(frame, finding),
    )

    assert result.challenge is not None
    assert result.challenge.outcome is LeakageChallengeOutcome.SUPPORTS_HUMAN_REVIEW
    assert result.audit.evidence_tools == ["test_recorded_before_prediction"]
    assert result.audit.raw_rows_shared is False


def test_agent_cannot_finish_with_an_unexecuted_challenge_reference() -> None:
    finding = _finding()
    proposal = _proposal(finding)
    frame = _frame()
    card = profile_table(LoadedTable("abt", frame, "memory://abt", "csv"))
    report = LeakageReport(
        target_column="outcome",
        n_features_checked=1,
        findings=[finding],
        split_strategy=SplitStrategy.TEMPORAL,
    )
    llm = ScriptedLLM(
        [
            {
                "action": "call_tool",
                "tool_id": "test_recorded_before_prediction",
                "arguments": {"proposal": proposal.model_dump(mode="json")},
                "reason": "Run the registered timestamp ordering test.",
            },
            {
                "action": "finish",
                "challenge_fingerprint": "0" * 64,
                "reason": "Try to reference a result this investigation did not execute.",
            },
            {
                "action": "abandon",
                "reason": "The host correctly rejected the unexecuted reference.",
            },
        ]
    )

    result = investigate_leakage(
        card=card,
        report=report,
        strategy=_strategy(),
        llm=llm,
        runtime=_runtime(frame, finding),
    )

    assert result.challenge is None
    assert "unexecuted_challenge" in result.audit.members[0].validation_failures


def test_model_failure_preserves_the_deterministic_floor() -> None:
    finding = _finding()
    frame = _frame()
    card = profile_table(LoadedTable("abt", frame, "memory://abt", "csv"))
    report = LeakageReport(
        target_column="outcome",
        n_features_checked=1,
        findings=[finding],
        split_strategy=SplitStrategy.TEMPORAL,
    )

    result = investigate_leakage(
        card=card,
        report=report,
        strategy=_strategy(),
        llm=FailingLLM(),
        runtime=_runtime(frame, finding),
    )

    assert result.challenge is None
    assert result.audit.valid_members == 0
    assert result.audit.members[0].validation_failures == ["investigation_error:KeyError"]
