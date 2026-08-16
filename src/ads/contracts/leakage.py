"""Leakage audit contracts.

The detection logic lives in :mod:`ads.discovery.leakage`; the types live here
with every other inter-stage boundary, so the contracts package stays the single
place to look for what crosses a stage boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field

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


class LeakageReport(Artifact):
    """The audit result. Blocking findings must clear before training."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.LEAKAGE_REPORT
    schema_version: ClassVar[str] = "1"

    target_column: str | None
    n_features_checked: int = Field(ge=0)
    findings: list[LeakageFinding] = Field(default_factory=list)
    split_strategy: SplitStrategy | None = None

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
    def separator_suspects(self) -> list[str]:
        """Features that determine the target but may be legitimately available.

        A perfect separator is a *suspect*, not a verdict. Statistical strength
        cannot establish temporal provenance: `years_experience >= 20` defining
        `senior_physician` scores AUC 1.0 and is a perfectly legitimate feature.
        Only a human knows whether a column is recorded before the outcome, so
        these escalate for confirmation rather than blocking automatically.
        """
        return sorted(
            {
                f.column
                for f in self.findings
                if f.kind is LeakageKind.PERFECT_SEPARATOR
            }
        )

    def to_quality_signals(self) -> QualitySignals:
        """Project into the signal shape the Gate Evaluator consumes.

        This is the seam between measurement and policy: the audit measures, the
        gate decides. Nothing here knows about autonomy profiles.
        """
        return QualitySignals(
            max_target_correlation=self.max_target_correlation,
            leakage_suspect_columns=self.suspect_columns,
            separator_suspect_columns=self.separator_suspects,
        )

    def drop_recommendations(self) -> list[str]:
        """Machine-generated correction instructions for the retry loop."""
        return [
            f"drop_feature: {f.column}  # {f.kind.value}" for f in self.blocking_findings
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "target_column": self.target_column,
            "n_features_checked": self.n_features_checked,
            "n_findings": len(self.findings),
            "n_blocking": len(self.blocking_findings),
            "suspect_columns": self.suspect_columns,
            "is_clean": self.is_clean,
        }


__all__ = ["LeakageFinding", "LeakageKind", "LeakageReport"]
