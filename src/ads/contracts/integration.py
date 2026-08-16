"""Schema and relationship contracts.

Split deliberately along the LLM/deterministic boundary from the report:

* :class:`KeyCandidate` and :class:`RelationshipCandidate` are **computed**
  (set operations over column values — no judgment involved).
* :class:`IntegrationPlan` is **proposed** by the SchemaDiscoveryAgent, which
  supplies the semantic interpretation the statistics cannot: what the table
  means, what its grain is, and whether a 94% overlap is a real foreign key or
  a coincidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel


class Cardinality(StrEnum):
    ONE_TO_ONE = "1:1"
    MANY_TO_ONE = "N:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_MANY = "N:N"
    UNKNOWN = "unknown"


class KeyCandidate(FrozenModel):
    """A column set that may uniquely identify rows in a table."""

    table: str
    columns: list[str] = Field(min_length=1)
    is_unique: bool
    null_rate: float = Field(ge=0.0, le=1.0)
    n_distinct: int = Field(ge=0)

    @property
    def is_clean_key(self) -> bool:
        return self.is_unique and self.null_rate == 0.0


class RowsPerParentStats(FrozenModel):
    """Fan-out measurements with every denominator named explicitly."""

    child_non_null_rows: int = Field(ge=0)
    matched_child_rows: int = Field(ge=0)
    referenced_parent_count: int = Field(ge=0)
    all_parent_count: int = Field(ge=0)
    mean_rows_per_referenced_parent: float = Field(ge=0.0)
    mean_rows_per_all_parents: float = Field(ge=0.0)
    median_rows_per_referenced_parent: float = Field(ge=0.0)
    p90_rows_per_referenced_parent: float = Field(ge=0.0)
    median_rows_per_all_parents: float = Field(ge=0.0)
    p90_rows_per_all_parents: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _named_denominators_are_consistent(self) -> RowsPerParentStats:
        if self.matched_child_rows > self.child_non_null_rows:
            raise ValueError("matched_child_rows cannot exceed child_non_null_rows.")
        if self.referenced_parent_count > self.all_parent_count:
            raise ValueError("referenced_parent_count cannot exceed all_parent_count.")
        expected_referenced_mean = (
            self.matched_child_rows / self.referenced_parent_count
            if self.referenced_parent_count
            else 0.0
        )
        expected_all_mean = (
            self.matched_child_rows / self.all_parent_count
            if self.all_parent_count
            else 0.0
        )
        if abs(self.mean_rows_per_referenced_parent - expected_referenced_mean) > 1e-6:
            raise ValueError("Referenced-parent mean does not match its named denominator.")
        if abs(self.mean_rows_per_all_parents - expected_all_mean) > 1e-6:
            raise ValueError("All-parent mean does not match its named denominator.")
        return self


class RelationshipCandidate(FrozenModel):
    """Deterministically measured overlap between two column sets.

    ``overlap_rate`` is the fraction of non-null child values found in the parent
    key. ``orphan_rate`` is its complement and is the number a human actually
    needs at the review gate: a 5.8% orphan rate on a financial join is a
    business question, not a technical one.
    """

    from_table: str
    from_columns: list[str] = Field(min_length=1)
    to_table: str
    to_columns: list[str] = Field(min_length=1)

    overlap_rate: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of non-null child ROWS whose value exists in the parent key. "
            "This is the business-meaningful number: its complement is the share of "
            "rows that would be lost to an inner join."
        ),
    )
    orphan_rate: float = Field(
        ge=0.0, le=1.0, description="Complement of overlap_rate, at row level."
    )
    distinct_overlap_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of distinct child values found in the parent. Diverges sharply "
            "from overlap_rate when orphans are many but rare, which is itself a "
            "signal worth showing a reviewer."
        ),
    )
    parent_coverage: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of parent keys referenced by the child. Low values mean the "
            "child merely sits inside the parent's numeric range rather than "
            "referencing it — the main source of false positives."
        ),
    )
    name_affinity: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Token overlap between the two column names."
    )
    n_from_distinct: int = Field(ge=0)
    n_to_distinct: int = Field(ge=0)
    cardinality: Cardinality = Cardinality.UNKNOWN
    dtype_compatible: bool = True
    rows_per_parent: RowsPerParentStats | None = None

    @model_validator(mode="after")
    def _check_widths(self) -> RelationshipCandidate:
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError("from_columns and to_columns must have equal length")
        return self

    @property
    def confidence(self) -> float:
        """Deterministic strength score for ranking candidates.

        Not an LLM self-assessment — it is a function of measured set overlap,
        parent coverage and name similarity only. Used to order candidates for
        human review, never to auto-approve a join.
        """
        if not self.dtype_compatible:
            return 0.0
        score = 0.6 * self.overlap_rate + 0.3 * self.parent_coverage + 0.1 * self.name_affinity
        return round(score, 4)


class JoinStep(FrozenModel):
    """One join in the integration plan, in execution order."""

    left_table: str
    right_table: str
    left_columns: list[str] = Field(min_length=1)
    right_columns: list[str] = Field(min_length=1)
    how: str = Field(default="left", pattern="^(inner|left|right|outer)$")
    rationale: str = Field(max_length=500)

    @model_validator(mode="after")
    def _check_widths(self) -> JoinStep:
        if len(self.left_columns) != len(self.right_columns):
            raise ValueError("left_columns and right_columns must have equal length")
        return self


class AggregationStep(FrozenModel):
    """Roll a one-to-many table up to the base grain before joining.

    Without this, joining a transactions table to a physician table silently
    multiplies rows and inflates every downstream metric.

    ``output_name`` names the resulting derived table so later joins can
    reference it. Omitting it was an early contract bug: the agent had no legal
    way to express "aggregate, then join the aggregate", so it invented table
    names that failed validation.
    """

    source_table: str
    output_name: str = Field(
        description="Name for the aggregated result; reference this in joins."
    )
    group_by: list[str] = Field(min_length=1)
    aggregations: dict[str, str] = Field(
        description="Mapping of output column name -> SQL aggregate expression."
    )
    rationale: str = Field(max_length=500)

    def output_columns(self) -> list[str]:
        """Columns available on the derived table: the grain plus the aggregates."""
        return [*self.group_by, *self.aggregations.keys()]


class IntegrationPlanProposal(FrozenModel):
    """What the SchemaDiscoveryAgent actually emits.

    Separated from :class:`IntegrationPlan` on purpose. The agent must not
    author ``evidence`` (that is measured, not judged) or ``created_at``
    (bookkeeping). Excluding them from the generated schema removes a large
    block of tokens the model would otherwise be forced to reproduce, which on a
    local model is a direct latency saving as well as a correctness one.
    """

    base_table: str
    base_grain: list[str] = Field(
        min_length=1, description="Columns defining one row of the output ABT."
    )
    grain_description: str = Field(max_length=300)

    aggregations: list[AggregationStep] = Field(default_factory=list)
    joins: list[JoinStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def derived_tables(self) -> dict[str, list[str]]:
        """Aggregation outputs and the columns each exposes."""
        return {a.output_name: a.output_columns() for a in self.aggregations}

    def source_of(self, table: str) -> str:
        """Resolve a derived table back to the real table it aggregates."""
        for agg in self.aggregations:
            if agg.output_name == table:
                return agg.source_table
        return table

    def tables_used(self) -> set[str]:
        tables = {self.base_table}
        for j in self.joins:
            tables.update({j.left_table, j.right_table})
        for a in self.aggregations:
            tables.add(a.source_table)
        return tables


class IntegrationPlan(Artifact):
    """The persisted plan: the agent's proposal plus the evidence behind it."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.INTEGRATION_PLAN
    schema_version: ClassVar[str] = "3"

    base_table: str
    base_grain: list[str] = Field(min_length=1)
    grain_description: str = Field(max_length=300)

    aggregations: list[AggregationStep] = Field(default_factory=list)
    joins: list[JoinStep] = Field(default_factory=list)

    evidence: list[RelationshipCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_proposal(
        cls, proposal: IntegrationPlanProposal, evidence: list[RelationshipCandidate]
    ) -> IntegrationPlan:
        """Attach measured evidence to an agent proposal."""
        return cls(
            base_table=proposal.base_table,
            base_grain=proposal.base_grain,
            grain_description=proposal.grain_description,
            aggregations=proposal.aggregations,
            joins=proposal.joins,
            evidence=evidence,
            warnings=proposal.warnings,
        )

    def tables_used(self) -> set[str]:
        tables = {self.base_table}
        for j in self.joins:
            tables.update({j.left_table, j.right_table})
        for a in self.aggregations:
            tables.add(a.source_table)
        return tables

    def summary(self) -> dict[str, Any]:
        return {
            "base_table": self.base_table,
            "base_grain": self.base_grain,
            "n_joins": len(self.joins),
            "n_aggregations": len(self.aggregations),
            "tables_used": sorted(self.tables_used()),
        }
