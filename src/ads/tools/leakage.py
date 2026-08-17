"""Registered deterministic tests for agent-proposed leakage challenges."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from ads.contracts.gates import PermissionTier
from ads.contracts.leakage import (
    LeakageChallenge,
    LeakageChallengeOutcome,
    LeakageChallengeProposal,
    LeakageChallengeTestKind,
    LeakageFinding,
    LeakageKind,
    leakage_challenge_fingerprint,
)
from ads.tools.models import ToolPayload, ToolRuntime
from ads.tools.registry import ToolDefinition, ToolRegistry

_TIME_TEST_KINDS = frozenset(
    {
        LeakageKind.TARGET_CORRELATION,
        LeakageKind.TARGET_MUTUAL_INFORMATION,
        LeakageKind.PERFECT_SEPARATOR,
        LeakageKind.POST_CUTOFF_DATETIME,
    }
)


def _finding(runtime: ToolRuntime, fingerprint: str) -> LeakageFinding:
    catalog = runtime.resources.get("leakage_findings")
    if not isinstance(catalog, dict):
        raise ValueError("No host-owned leakage finding catalog is available.")
    finding = catalog.get(fingerprint)
    if not isinstance(finding, LeakageFinding):
        raise ValueError("Challenge does not reference a finding from this audit.")
    return finding


def recorded_before_prediction(
    runtime: ToolRuntime,
    arguments: Mapping[str, Any],
) -> ToolPayload:
    """Compare host-bound feature recording timestamps with prediction times."""
    raw = arguments.get("proposal")
    if not isinstance(raw, Mapping):
        raise ValueError("test_recorded_before_prediction requires a proposal object.")
    proposal = LeakageChallengeProposal.model_validate(dict(raw))
    if proposal.test_kind is not LeakageChallengeTestKind.RECORDED_BEFORE_PREDICTION:
        raise ValueError("Proposal requested the wrong registered test kind.")
    finding = _finding(runtime, proposal.finding_fingerprint)
    if finding.kind not in _TIME_TEST_KINDS:
        raise ValueError(
            f"{proposal.test_kind.value} cannot challenge {finding.kind.value}; "
            "timestamp availability does not address that failure mechanism."
        )
    try:
        frame = runtime.frames["abt"]
    except KeyError as exc:
        raise ValueError("Leakage challenge requires the ABT runtime frame.") from exc
    required = {
        finding.column,
        proposal.recorded_at_column,
        proposal.prediction_time_column,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Challenge references unknown ABT columns: {missing}.")

    feature_present = frame[finding.column].notna()
    recorded = pd.to_datetime(
        frame[proposal.recorded_at_column], errors="coerce", format="mixed", utc=True
    )
    prediction = pd.to_datetime(
        frame[proposal.prediction_time_column], errors="coerce", format="mixed", utc=True
    )
    testable = feature_present & recorded.notna() & prediction.notna()
    candidate_rows = int(feature_present.sum())
    tested_rows = int(testable.sum())
    missing_timestamp_rows = candidate_rows - tested_rows
    recorded_after = int((recorded[testable] > prediction[testable]).sum())

    if candidate_rows == 0 or tested_rows == 0 or missing_timestamp_rows:
        outcome = LeakageChallengeOutcome.INDETERMINATE
        summary = (
            f"Only {tested_rows} of {candidate_rows} populated feature rows had both "
            "timestamps; incomplete coverage cannot support the challenge."
        )
    elif recorded_after:
        outcome = LeakageChallengeOutcome.REFUTES_CHALLENGE
        summary = (
            f"{recorded_after} of {tested_rows} tested rows record the feature after "
            "prediction time, contradicting the proposed availability claim."
        )
    else:
        outcome = LeakageChallengeOutcome.SUPPORTS_HUMAN_REVIEW
        summary = (
            f"All {tested_rows} populated feature rows have a supplied recording time "
            "at or before the supplied prediction time. A human must still confirm that "
            "the recording timestamp truly belongs to this feature."
        )

    challenge = LeakageChallenge(
        **proposal.model_dump(),
        finding_column=finding.column,
        finding_kind=finding.kind,
        candidate_rows=candidate_rows,
        tested_rows=tested_rows,
        missing_timestamp_rows=missing_timestamp_rows,
        recorded_after_prediction_rows=recorded_after,
        outcome=outcome,
        result_summary=summary,
    )
    data = challenge.model_dump(mode="json")
    data["challenge_fingerprint"] = leakage_challenge_fingerprint(proposal)
    return ToolPayload(
        summary=f"test_recorded_before_prediction measured: {summary}",
        data=data,
    )


def register_leakage_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(
        ToolDefinition(
            tool_id="test_recorded_before_prediction",
            tier=PermissionTier.READ_DATA,
            description=(
                "Measure whether a leakage-suspect feature was recorded no later "
                "than a stated row-level prediction time."
            ),
            handler=recorded_before_prediction,
        )
    )
    return registry


__all__ = ["recorded_before_prediction", "register_leakage_tools"]
