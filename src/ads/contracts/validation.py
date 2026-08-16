"""Validation strategy contract.

Added as a first-class stage during the architecture review: a random
``train_test_split`` over temporal or entity-grouped enterprise data silently
invalidates every number the pipeline later produces, and nothing downstream can
detect it.

The agent chooses from a **closed set** of strategies rather than describing one
in prose. A closed enum is what lets the executor be deterministic and lets a
small local model handle the decision reliably.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class SplitStrategy(StrEnum):
    RANDOM = "random"
    STRATIFIED = "stratified"
    GROUPED = "grouped"
    TEMPORAL = "temporal"
    GROUPED_TEMPORAL = "grouped_temporal"


class IdentifierContainmentSignal(FrozenModel):
    """Whether grouping on one identifier contains another repeating domain.

    This is a row-free contingency measurement. `spanning_value_count` counts
    repeated values of `column` observed under more than one value of the
    candidate group column. `missing_group_value_count` counts repeated values
    whose rows include an unavailable candidate group. Both must be zero for the
    candidate to protect this domain.
    """

    column: str
    repeated_value_count: int = Field(ge=1)
    spanning_value_count: int = Field(ge=0)
    missing_group_value_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _counts_cannot_exceed_domain(self) -> IdentifierContainmentSignal:
        if self.spanning_value_count > self.repeated_value_count:
            raise ValueError("spanning_value_count cannot exceed repeated_value_count")
        if self.missing_group_value_count > self.repeated_value_count:
            raise ValueError("missing_group_value_count cannot exceed repeated_value_count")
        return self


class RepeatedEntitySignal(FrozenModel):
    """Measured recurrence of an identifier and its grouping containment.

    Recurrence makes a column a possible entity boundary; it does not prove the
    business semantics. Join fan-out can also make row identifiers recur. The
    containment matrix determines which candidates are safe grouping keys while
    a human or agent supplies the semantic interpretation.

    The optional fields keep older persisted artifacts readable. `None` means
    the measurement was not made; it must never be interpreted as zero.
    """

    column: str
    n_entities: int = Field(ge=1)
    unique_rate: float = Field(ge=0.0, le=1.0)
    rows_per_entity: float = Field(ge=1.0)
    repeated_value_count: int | None = Field(default=None, ge=1)
    repeated_row_count: int | None = Field(default=None, ge=2)
    repeated_row_rate: float | None = Field(default=None, gt=0.0, le=1.0)
    containment: list[IdentifierContainmentSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recurrence_counts_are_consistent(self) -> RepeatedEntitySignal:
        supplied = (
            self.repeated_value_count,
            self.repeated_row_count,
            self.repeated_row_rate,
        )
        if any(value is None for value in supplied) and any(
            value is not None for value in supplied
        ):
            raise ValueError("repeat count and rate measurements must be supplied together")
        if (
            self.repeated_value_count is not None
            and self.repeated_row_count is not None
            and self.repeated_row_count < 2 * self.repeated_value_count
        ):
            raise ValueError("each repeated value must account for at least two rows")
        if self.containment and self.repeated_value_count is None:
            raise ValueError("containment cannot exist without recurrence measurements")
        containment_columns = [item.column for item in self.containment]
        if len(containment_columns) != len(set(containment_columns)):
            raise ValueError("containment columns must be unique")
        return self


class TemporalSpanSignal(FrozenModel):
    """Observed range of a datetime column."""

    column: str
    min_date: str
    max_date: str
    span_days: int = Field(ge=0)


class ValidationSignals(FrozenModel):
    """Facts measured from the ABT that constrain validation design."""

    n_rows: int = Field(ge=0)
    n_usable_rows: int = Field(ge=0)
    n_folds: int = Field(ge=2, le=20)
    target_column: str | None = None
    repeated_entity_keys: list[RepeatedEntitySignal] = Field(default_factory=list)
    temporal_spans: list[TemporalSpanSignal] = Field(default_factory=list)
    minority_class_count: int | None = Field(default=None, ge=0)
    minority_class_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    small_sample_warnings: list[str] = Field(default_factory=list)


class ValidationStrategyProposal(FrozenModel):
    """The split judgment authored by the agent, without measured facts."""

    strategy: SplitStrategy
    n_folds: int = Field(default=5, ge=2, le=20)
    test_size: float = Field(default=0.2, gt=0.0, lt=0.9)

    group_column: str | None = Field(
        default=None, description="Required for grouped strategies."
    )
    time_column: str | None = Field(
        default=None, description="Required for temporal strategies."
    )
    holdout_cutoff: str | None = Field(
        default=None, description="ISO date; rows at or after this go to holdout."
    )
    rationale: str = Field(max_length=800)

    @model_validator(mode="after")
    def _require_supporting_columns(self) -> ValidationStrategyProposal:
        needs_group = {SplitStrategy.GROUPED, SplitStrategy.GROUPED_TEMPORAL}
        needs_time = {SplitStrategy.TEMPORAL, SplitStrategy.GROUPED_TEMPORAL}
        if self.strategy in needs_group and not self.group_column:
            raise ValueError(f"group_column is required for strategy={self.strategy}")
        if self.strategy in needs_time and not self.time_column:
            raise ValueError(f"time_column is required for strategy={self.strategy}")
        return self


class ValidationStrategy(Artifact):
    """How the data will be split for honest evaluation."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.VALIDATION_STRATEGY
    schema_version: ClassVar[str] = "2"

    strategy: SplitStrategy
    n_folds: int = Field(default=5, ge=2, le=20)
    test_size: float = Field(default=0.2, gt=0.0, lt=0.9)

    group_column: str | None = Field(
        default=None, description="Required for grouped strategies."
    )
    time_column: str | None = Field(
        default=None, description="Required for temporal strategies."
    )
    holdout_cutoff: str | None = Field(
        default=None, description="ISO date; rows at or after this go to holdout."
    )

    rationale: str = Field(max_length=800)
    detected_signals: ValidationSignals = Field(
        default_factory=lambda: ValidationSignals(
            n_rows=0,
            n_usable_rows=0,
            n_folds=5,
        ),
        description="Deterministic findings that motivated this choice.",
    )

    @model_validator(mode="after")
    def _require_supporting_columns(self) -> ValidationStrategy:
        needs_group = {SplitStrategy.GROUPED, SplitStrategy.GROUPED_TEMPORAL}
        needs_time = {SplitStrategy.TEMPORAL, SplitStrategy.GROUPED_TEMPORAL}
        if self.strategy in needs_group and not self.group_column:
            raise ValueError(f"group_column is required for strategy={self.strategy}")
        if self.strategy in needs_time and not self.time_column:
            raise ValueError(f"time_column is required for strategy={self.strategy}")
        return self

    @classmethod
    def from_proposal(
        cls,
        proposal: ValidationStrategyProposal,
        detected_signals: ValidationSignals,
    ) -> ValidationStrategy:
        """Attach system-measured signals to the agent's split judgment."""
        return cls(**proposal.model_dump(), detected_signals=detected_signals)

    def summary(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "n_folds": self.n_folds,
            "group_column": self.group_column,
            "time_column": self.time_column,
        }
