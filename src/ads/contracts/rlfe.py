"""Contract for the external RL feature-engineering search.

The service returns a *recipe*, not a dataset: which raw columns to keep and
which depth-one transformations to build from them. Persisting the recipe rather
than the resulting frame is what makes this artifact worth having -- run
snapshots drop DataFrames, so the enhanced frame is rebuilt deterministically
from the model frame plus this report whenever it is needed again, and the same
recipe is what would be replayed on new data at inference time.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel

#: The search found a usable feature set.
APPLICABLE = "applicable"
#: The data cannot support the search; `reasons` carries the service's codes.
NOT_APPLICABLE = "not_applicable"
#: The service could not be reached, failed, or outran the poll budget.
UNAVAILABLE = "unavailable"

RlfeStatus = Literal["applicable", "not_applicable", "unavailable"]


class GeneratedFeature(FrozenModel):
    """One depth-one transformation, in the form the service can replay."""

    name: str
    operation: str
    inputs: list[str] = Field(min_length=1)
    expression: str = ""


class RlFeatureReport(Artifact):
    """What the external feature search concluded for one run.

    Always produced, including when the service was unreachable. A stage that
    emitted nothing on failure would stall the run, because training declares
    this type among its inputs and the runner checks the next stage's required
    artifacts before it advances.
    """

    artifact_type: ClassVar[ArtifactType] = ArtifactType.RL_FEATURE_REPORT
    schema_version: ClassVar[str] = "1"

    status: RlfeStatus
    reasons: list[str] = Field(default_factory=list)
    detail: str | None = None
    target_column: str | None = None

    #: What actually left this host, so the egress is auditable after the fact.
    sent_row_count: int = Field(default=0, ge=0)
    sent_column_count: int = Field(default=0, ge=0)

    selected_features: list[str] = Field(default_factory=list)
    removed_features: list[str] = Field(default_factory=list)
    generated_features: list[GeneratedFeature] = Field(default_factory=list)
    #: Named in the recipe but not rebuildable here -- an unknown operation, a
    #: missing input, or no finite value to impute from. Recorded rather than
    #: raised so the service can grow operations without breaking a run.
    skipped_features: list[str] = Field(default_factory=list)

    #: The service's own cross-validated scores, measured on the rows we sent
    #: (outer-train only). These are not comparable with our holdout numbers and
    #: must not be presented as if they were.
    api_baseline_score: float | None = None
    api_optimized_score: float | None = None
    api_score_improvement: float | None = None
    primary_metric: str | None = None
    primary_direction: str | None = None

    excluded_detected_columns: list[str] = Field(default_factory=list)
    search_termination_reason: str | None = None

    @model_validator(mode="after")
    def _only_applicable_carries_a_recipe(self) -> RlFeatureReport:
        if self.status != APPLICABLE and (self.selected_features or self.generated_features):
            raise ValueError(
                "Only an applicable report may carry a feature recipe; a "
                "not-applicable or unavailable search produced none."
            )
        if self.status == APPLICABLE and not self.selected_features:
            raise ValueError("An applicable report must name at least one retained feature.")
        return self

    @property
    def generated_feature_names(self) -> list[str]:
        return [feature.name for feature in self.generated_features]

    def recipe(self) -> list[dict[str, Any]]:
        """The generated features in the plain-dict shape the replay helper takes."""
        return [feature.model_dump(exclude={"created_at"}) for feature in self.generated_features]

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_column": self.target_column,
            "n_selected_features": len(self.selected_features),
            "n_removed_features": len(self.removed_features),
            "n_generated_features": len(self.generated_features),
            "api_score_improvement": self.api_score_improvement,
            "primary_metric": self.primary_metric,
            "sent_row_count": self.sent_row_count,
            "sent_column_count": self.sent_column_count,
        }


__all__ = [
    "APPLICABLE",
    "NOT_APPLICABLE",
    "UNAVAILABLE",
    "GeneratedFeature",
    "RlFeatureReport",
    "RlfeStatus",
]
