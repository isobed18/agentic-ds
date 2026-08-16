"""SchemaDiscoveryAgent — semantic interpretation of measured structure.

The deterministic layer has already done the measurable work: profiled every
table, found candidate keys, and measured overlap for every plausible column
pair. What remains genuinely needs judgment and world knowledge:

* What does each table represent, and what is one row of it?
* Is a 94.2% overlap a real foreign key, or two unrelated integer columns?
* Which table should be the base of the analytical base table?
* Does a one-to-many join need aggregating first to avoid multiplying rows?

Keeping the agent's job to *interpretation over a short ranked list* — rather
than inference over data it cannot see — is what makes a 27B local model
sufficient here.
"""

from __future__ import annotations

from ads.agents.base import AgentContext, AgentSpec, ValidationFailure, suggest_name
from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.contracts.integration import IntegrationPlanProposal, RelationshipCandidate
from ads.intake.keys import relationships_digest
from ads.intake.profiler import datacard_digest
from ads.llm.client import LARGE

SYSTEM_PROMPT = """\
You are a data integration specialist working on messy enterprise data.

You are given profiles of several tables and a ranked list of MEASURED column \
overlaps. The measurements are already correct — do not recompute or doubt them. \
Your job is to interpret what they MEAN and produce a plan to build a single \
analytical base table (ABT).

Rules:
- Choose as `base_table` the table whose grain matches the entity to be analysed. \
Prefer a table with a clean primary key.
- `base_grain` must be columns that uniquely identify one row of the base table.
- Only propose a join when the measured evidence supports it. A high overlap \
between two unrelated columns (for example a small integer range sitting inside \
a large row counter) is NOT a foreign key.
- If a table has MANY rows per base row, you MUST aggregate it first. Joining it \
directly would multiply rows and corrupt every later metric.

How to aggregate and then join:
1. Add an entry to `aggregations` with `source_table` set to the real table, \
`output_name` set to a new name for the aggregated result (for example \
"transactions_by_physician"), `group_by` set to the foreign key column that \
matches the base grain, and `aggregations` mapping new column names to SQL \
aggregates.
2. Then add a join whose `right_table` is that `output_name`, and whose \
`right_columns` are the `group_by` columns.
Never join a raw one-to-many table directly.

- Aggregation expressions must be valid SQL aggregates over real columns of the \
source table, for example "SUM(amount)", "COUNT(*)", "AVG(amount)", "MAX(txn_date)".
- Use exact column names as given. Do not invent columns.
- Record any concern a human should review in `warnings`, especially orphan rates \
above 1% on a join.

Be concise. Every rationale must cite the measured evidence it relies on.\
"""


def build_context(
    cards: list[DataCard], relationships: list[RelationshipCandidate]
) -> AgentContext:
    """Project profiling output into the agent's context.

    This is the whole context — no raw rows, no conversation history, no other
    stage's output.
    """
    tables_section = "\n\n".join(datacard_digest(c) for c in cards)
    known_columns = sorted({col.name for card in cards for col in card.columns})
    table_names = sorted(c.table_name for c in cards)

    return AgentContext(
        sections={
            "Tables": tables_section,
            "Measured relationships": relationships_digest(relationships),
            "Task": (
                "Produce an IntegrationPlan that builds one analytical base table "
                "from these tables."
            ),
        },
        facts={
            "known_columns": known_columns,
            "table_names": table_names,
            "columns_by_table": {c.table_name: c.column_names for c in cards},
        },
    )


def validate_tables_exist(
    plan: IntegrationPlanProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Every referenced table must be loaded, or produced by an aggregation."""
    loaded: list[str] = list(context.facts.get("table_names", []))
    derived = list(plan.derived_tables())
    known = [*loaded, *derived]
    failures: list[ValidationFailure] = []

    # Aggregations may only read real tables, never other derived tables.
    for i, agg in enumerate(plan.aggregations):
        if agg.source_table not in loaded:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unknown_table",
                    detail=(
                        f"Aggregation source {agg.source_table!r} is not a loaded table. "
                        f"Known tables: {loaded}"
                    ),
                    field_path=f"aggregations[{i}].source_table",
                    repair_suggestion=suggest_name(agg.source_table, loaded),
                )
            )

    referenced = {plan.base_table} | {t for j in plan.joins for t in (j.left_table, j.right_table)}
    for table in sorted(referenced):
        if table not in known:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unknown_table",
                    detail=(
                        f"Table {table!r} does not exist. Known tables: {loaded}. "
                        f"Aggregation outputs declared in this plan: {derived or 'none'}. "
                        "To join an aggregated table, first declare it in `aggregations` "
                        "with that name as `output_name`."
                    ),
                    field_path="base_table" if table == plan.base_table else "joins",
                    repair_suggestion=suggest_name(table, known),
                )
            )
    return failures


def validate_columns_exist(
    plan: IntegrationPlanProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Every referenced column must exist on the table it is attributed to.

    This is the highest-yield validator in the system: column hallucination is
    by far the most common structured-output failure on local models.
    """
    by_table: dict[str, list[str]] = dict(context.facts.get("columns_by_table", {}))
    by_table.update(plan.derived_tables())
    failures: list[ValidationFailure] = []

    def check(table: str, columns: list[str], path: str) -> None:
        known = by_table.get(table)
        if known is None:
            return  # unknown table already reported by validate_tables_exist
        for column in columns:
            if column not in known:
                failures.append(
                    ValidationFailure(
                        layer="semantic",
                        code="unknown_column",
                        detail=f"Column {column!r} does not exist in {table!r}. "
                               f"Available: {known[:12]}",
                        field_path=path,
                        repair_suggestion=suggest_name(column, known),
                    )
                )

    check(plan.base_table, plan.base_grain, "base_grain")
    for i, join in enumerate(plan.joins):
        check(join.left_table, join.left_columns, f"joins[{i}].left_columns")
        check(join.right_table, join.right_columns, f"joins[{i}].right_columns")
    for i, agg in enumerate(plan.aggregations):
        check(agg.source_table, agg.group_by, f"aggregations[{i}].group_by")

    return failures


def validate_joins_supported_by_evidence(
    plan: IntegrationPlanProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Reject joins the measurements do not support.

    Without this the agent can assert any join it finds plausible. With it, the
    deterministic layer holds veto power over the LLM's proposal — the same
    principle as the Gate Evaluator, applied at the contract level.

    Derived tables are resolved back to their source before checking, since the
    evidence was measured on the raw tables.
    """
    measured: set[tuple[str, str, str, str]] = context.facts.get("measured_pairs", set())
    if not measured:
        return []

    failures: list[ValidationFailure] = []
    for i, join in enumerate(plan.joins):
        left_table = plan.source_of(join.left_table)
        right_table = plan.source_of(join.right_table)
        forward = (left_table, join.left_columns[0], right_table, join.right_columns[0])
        reverse = (right_table, join.right_columns[0], left_table, join.left_columns[0])

        if forward not in measured and reverse not in measured:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unsupported_join",
                    detail=(
                        f"Join {join.left_table}.{join.left_columns[0]} = "
                        f"{join.right_table}.{join.right_columns[0]} is not backed by a "
                        "measured relationship. Only propose joins listed in the "
                        "measured relationships section."
                    ),
                    field_path=f"joins[{i}]",
                )
            )
    return failures


def validate_fan_out_is_aggregated(
    plan: IntegrationPlanProposal, context: AgentContext
) -> list[ValidationFailure]:
    """A one-to-many source joined raw silently multiplies base rows."""
    cardinalities: dict[tuple[str, str], str] = context.facts.get("cardinality_by_pair", {})
    derived = set(plan.derived_tables())
    failures: list[ValidationFailure] = []

    for i, join in enumerate(plan.joins):
        if join.right_table in derived:
            continue  # already rolled up to the base grain
        key = (join.right_table, join.left_table)
        if cardinalities.get(key) in {"N:1", "N:N"}:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unaggregated_fan_out",
                    detail=(
                        f"{join.right_table} has many rows per {join.left_table} row. "
                        f"Add an aggregations entry with source_table="
                        f"{join.right_table!r} and an output_name, then join that "
                        "output_name instead."
                    ),
                    field_path=f"joins[{i}]",
                )
            )
    return failures


def build_spec() -> AgentSpec[IntegrationPlanProposal]:
    return AgentSpec(
        id="schema_discovery",
        system_prompt=SYSTEM_PROMPT,
        output_contract=IntegrationPlanProposal,
        profile=LARGE,
        validators=(
            validate_tables_exist,
            validate_columns_exist,
            validate_joins_supported_by_evidence,
            validate_fan_out_is_aggregated,
        ),
        max_attempts=3,
        rubric="schema_discovery.v1",
        allowed_tools=frozenset({"candidate_keys", "join_overlap"}),
        max_tool_tier=PermissionTier.READ_DATA,
        column_fields=frozenset(
            {"base_grain", "group_by", "left_columns", "right_columns"}
        ),
        # `rationale` appears on every join and aggregation step; matching by
        # name covers all of them. Everything else changes the shape of the ABT,
        # including `how` -- an inner join drops rows a left join keeps.
        narration_fields=frozenset({"grain_description", "rationale", "warnings"}),
    )


def build_context_with_evidence(
    cards: list[DataCard], relationships: list[RelationshipCandidate]
) -> AgentContext:
    """Context plus the derived facts the evidence validators need."""
    context = build_context(cards, relationships)
    context.facts["measured_pairs"] = {
        (r.from_table, r.from_columns[0], r.to_table, r.to_columns[0])
        for r in relationships
    }
    context.facts["cardinality_by_pair"] = {
        (r.from_table, r.to_table): r.cardinality.value for r in relationships
    }
    return context


__all__ = [
    "SYSTEM_PROMPT",
    "build_context",
    "build_context_with_evidence",
    "build_spec",
    "validate_columns_exist",
    "validate_fan_out_is_aggregated",
    "validate_joins_supported_by_evidence",
    "validate_tables_exist",
]
