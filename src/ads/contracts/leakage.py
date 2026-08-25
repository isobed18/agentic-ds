"""Leakage audit contracts.

The detection logic lives in :mod:`ads.discovery.leakage`; the types live here
with every other inter-stage boundary, so the contracts package stays the single
place to look for what crosses a stage boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, JsonValue, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel
from ads.contracts.gates import QualitySignals
from ads.contracts.validation import SplitStrategy


class LeakageKind(StrEnum):
    """How a feature leaks. Each kind needs a different fix, so they are distinct."""

    TARGET_CORRELATION = "target_correlation"
    TARGET_MUTUAL_INFORMATION = "target_mutual_information"
    PERFECT_SEPARATOR = "perfect_separator"
    MISSINGNESS_SEPARATOR = "missingness_separator"
    UNWINDOWED_AGGREGATE = "unwindowed_aggregate"
    POST_CUTOFF_DATETIME = "post_cutoff_datetime"
    IDENTIFIER_PROXY = "identifier_proxy"


class LeakageChallengeTestKind(StrEnum):
    """Registered deterministic tests an agent may request against a finding."""

    RECORDED_BEFORE_PREDICTION = "recorded_before_prediction"


class LeakageChallengeOutcome(StrEnum):
    """What the registered executor observed, not an agent verdict."""

    SUPPORTS_HUMAN_REVIEW = "supports_human_review"
    REFUTES_CHALLENGE = "refutes_challenge"
    INDETERMINATE = "indeterminate"


class LeakageChallengeActionKind(StrEnum):
    CALL_TOOL = "call_tool"
    FINISH = "finish"
    ABANDON = "abandon"


class LeakageFinding(FrozenModel):
    """One leaking feature, with the measurement that condemned it."""

    column: str
    kind: LeakageKind
    score: float = Field(
        description=(
            "Measured strength: correlation, mutual information, directional "
            "separation information, or uniqueness by kind."
        )
    )
    threshold: float
    blocking: bool
    detail: str = Field(max_length=400)
    suggested_action: str = Field(max_length=200)


def leakage_finding_fingerprint(finding: LeakageFinding) -> str:
    """Stable id for the exact deterministic finding a challenge addresses."""
    import hashlib
    import json

    payload = finding.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class LeakageChallengeProposal(FrozenModel):
    """Agent-authored falsifiable test request; deliberately contains no results."""

    finding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_kind: LeakageChallengeTestKind
    recorded_at_column: str
    prediction_time_column: str
    hypothesis: str = Field(min_length=10, max_length=500)
    interpretation: str = Field(
        min_length=10,
        max_length=800,
        description="Proposed model interpretation, never a measured fact.",
    )
    why_it_matters: str = Field(min_length=10, max_length=500)
    verification_question: str = Field(
        min_length=10,
        max_length=500,
        description="Mandatory question a human must resolve before accepting the challenge.",
    )

    @model_validator(mode="after")
    def _distinct_time_columns(self) -> LeakageChallengeProposal:
        if self.recorded_at_column == self.prediction_time_column:
            raise ValueError("recorded_at_column and prediction_time_column must differ.")
        return self


def leakage_challenge_fingerprint(proposal: LeakageChallengeProposal) -> str:
    """Stable id for exact test parameters and proposed interpretation."""
    import hashlib
    import json

    canonical = json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class LeakageChallengeAction(FrozenModel):
    """One bounded turn in the leakage challenge investigation."""

    action: LeakageChallengeActionKind
    tool_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    challenge_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def _shape(self) -> LeakageChallengeAction:
        if self.action is LeakageChallengeActionKind.CALL_TOOL:
            if self.tool_id is None:
                raise ValueError("call_tool requires tool_id.")
            if self.challenge_fingerprint is not None:
                raise ValueError("call_tool cannot finish a challenge.")
            if self.tool_id == "execute_python":
                code = self.arguments.get("code")
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("execute_python requires non-empty code.")
        else:
            if self.tool_id is not None or self.arguments:
                raise ValueError(f"{self.action.value} cannot carry a tool call.")
            if (
                self.action is LeakageChallengeActionKind.FINISH
                and self.challenge_fingerprint is None
            ):
                raise ValueError("finish requires challenge_fingerprint.")
            if (
                self.action is LeakageChallengeActionKind.ABANDON
                and self.challenge_fingerprint is not None
            ):
                raise ValueError("abandon cannot finish a challenge.")
        return self


class LeakageChallenge(FrozenModel):
    """Executor-owned result of a typed challenge to one leakage finding.

    The test proves only what it measures: timestamps in the supplied data place
    populated values no later than the stated prediction time. It does not prove
    that the timestamp is semantically the feature's true recording time. For
    that reason this first registered test can improve a human decision but can
    never clear a gate without that human.
    """

    evidence_class: Literal["deterministic_registered_test"] = "deterministic_registered_test"
    gate_effect: Literal["human_review_required"] = "human_review_required"
    finding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    finding_column: str
    finding_kind: LeakageKind
    test_kind: LeakageChallengeTestKind
    recorded_at_column: str
    prediction_time_column: str
    hypothesis: str
    interpretation: str
    why_it_matters: str
    verification_question: str
    candidate_rows: int = Field(ge=0)
    tested_rows: int = Field(ge=0)
    missing_timestamp_rows: int = Field(ge=0)
    recorded_after_prediction_rows: int = Field(ge=0)
    outcome: LeakageChallengeOutcome
    result_summary: str = Field(max_length=500)


class LeakageReport(Artifact):
    """The audit result. Blocking findings must clear before training."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.LEAKAGE_REPORT
    schema_version: ClassVar[str] = "1"

    target_column: str | None
    n_features_checked: int = Field(ge=0)
    findings: list[LeakageFinding] = Field(default_factory=list)
    challenges: list[LeakageChallenge] = Field(default_factory=list)
    split_strategy: SplitStrategy | None = None

    @model_validator(mode="after")
    def _challenges_bind_to_exact_findings(self) -> LeakageReport:
        findings = {leakage_finding_fingerprint(finding): finding for finding in self.findings}
        seen: set[str] = set()
        for challenge in self.challenges:
            finding = findings.get(challenge.finding_fingerprint)
            if finding is None:
                raise ValueError("Leakage challenge is not bound to a finding in this report.")
            if challenge.finding_fingerprint in seen:
                raise ValueError("A leakage finding may carry at most one selected challenge.")
            if (
                challenge.finding_column != finding.column
                or challenge.finding_kind is not finding.kind
            ):
                raise ValueError("Leakage challenge subject does not match its cited finding.")
            seen.add(challenge.finding_fingerprint)
        return self

    def with_challenge(self, challenge: LeakageChallenge) -> LeakageReport:
        """Return a revalidated report with one executor-produced challenge attached."""
        payload = self.model_dump(mode="python")
        payload["challenges"] = [*self.challenges, challenge]
        return LeakageReport.model_validate(payload)

    @property
    def blocking_findings(self) -> list[LeakageFinding]:
        return [f for f in self.findings if f.blocking]

    @property
    def is_clean(self) -> bool:
        return not self.blocking_findings

    @property
    def max_target_correlation(self) -> float | None:
        """Highest correlation-family score, for the gate's hard leakage rule."""
        scores = [
            f.score
            for f in self.findings
            if f.kind
            in (
                LeakageKind.TARGET_CORRELATION,
                LeakageKind.TARGET_MUTUAL_INFORMATION,
            )
        ]
        return max(scores) if scores else None

    @property
    def suspect_columns(self) -> list[str]:
        return sorted({f.column for f in self.blocking_findings})

    @property
    def target_relationship_suspects(self) -> list[str]:
        return sorted(
            {
                finding.column
                for finding in self.blocking_findings
                if finding.kind
                in (
                    LeakageKind.TARGET_CORRELATION,
                    LeakageKind.TARGET_MUTUAL_INFORMATION,
                )
            }
        )

    @property
    def structural_suspects(self) -> list[str]:
        return sorted(
            {
                finding.column
                for finding in self.blocking_findings
                if finding.kind
                not in (
                    LeakageKind.TARGET_CORRELATION,
                    LeakageKind.TARGET_MUTUAL_INFORMATION,
                )
            }
        )

    @property
    def separator_suspects(self) -> list[str]:
        """Features that determine the target but may be legitimately available.

        A perfect separator is a *suspect*, not a verdict. Statistical strength
        cannot establish temporal provenance: `years_experience >= 20` defining
        `senior_physician` scores AUC 1.0 and is a perfectly legitimate feature.
        Only a human knows whether a column is recorded before the outcome, so
        these escalate for confirmation rather than blocking automatically.
        """
        return sorted({f.column for f in self.findings if f.kind is LeakageKind.PERFECT_SEPARATOR})

    def to_quality_signals(self) -> QualitySignals:
        """Project into the signal shape the Gate Evaluator consumes.

        This is the seam between measurement and policy: the audit measures, the
        gate decides. Nothing here knows about autonomy profiles.
        """
        return QualitySignals(
            max_target_correlation=self.max_target_correlation,
            leakage_suspect_columns=self.suspect_columns,
            target_relationship_suspect_columns=self.target_relationship_suspects,
            structural_leakage_suspect_columns=self.structural_suspects,
            separator_suspect_columns=self.separator_suspects,
            leakage_challenge_review_columns=sorted(
                {
                    challenge.finding_column
                    for challenge in self.challenges
                    if challenge.outcome is LeakageChallengeOutcome.SUPPORTS_HUMAN_REVIEW
                }
            ),
        )

    def drop_recommendations(self) -> list[str]:
        """Machine-generated correction instructions for the retry loop."""
        return [f"drop_feature: {f.column}  # {f.kind.value}" for f in self.blocking_findings]

    def summary(self) -> dict[str, Any]:
        return {
            "target_column": self.target_column,
            "n_features_checked": self.n_features_checked,
            "n_findings": len(self.findings),
            "n_blocking": len(self.blocking_findings),
            "suspect_columns": self.suspect_columns,
            "n_challenges": len(self.challenges),
            "is_clean": self.is_clean,
        }


__all__ = [
    "LeakageChallenge",
    "LeakageChallengeAction",
    "LeakageChallengeActionKind",
    "LeakageChallengeOutcome",
    "LeakageChallengeProposal",
    "LeakageChallengeTestKind",
    "LeakageFinding",
    "LeakageKind",
    "LeakageReport",
    "leakage_challenge_fingerprint",
    "leakage_finding_fingerprint",
]
