"""Execute an IntegrationPlan into an analytical base table.

Fully deterministic — no LLM. The agent decided *what* to join; this module does
it, and independently verifies the result.

The plan is compiled to **readable SQL** rather than a chain of pandas calls.
That is a deliberate design choice: SQL is the artifact a human domain expert can
actually review at the stage-1 gate, and DuckDB executes it over registered
DataFrames without a server.

The executor also enforces the property the agent was told to respect: joining a
one-to-many table without aggregating first multiplies base rows. Instead of
trusting the plan, we count rows before and after and fail loudly if the grain
was broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from ads.contracts.integration import (
    AggregationStep,
    IntegrationPlan,
    IntegrationPlanProposal,
    JoinStep,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_AGGREGATES = frozenset(
    {"SUM", "COUNT", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV", "VAR_POP", "COUNT_DISTINCT"}
)
_AGG_EXPR_RE = re.compile(
    r"^\s*(?P<fn>[A-Za-z_]+)\s*\(\s*(?P<arg>\*|DISTINCT\s+[A-Za-z_][A-Za-z0-9_]*"
    r"|[A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$",
    re.IGNORECASE,
)


class IntegrationError(RuntimeError):
    """Raised when a plan cannot be executed safely."""


@dataclass
class IntegrationResult:
    """The ABT plus everything needed to audit how it was built."""

    frame: pd.DataFrame
    sql: str
    base_rows: int
    result_rows: int
    warnings: list[str] = field(default_factory=list)
    step_row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def grain_preserved(self) -> bool:
        """Whether the ABT still has exactly one row per base row."""
        return self.result_rows == self.base_rows


def _quote(identifier: str) -> str:
    """Quote an identifier after validating it, to keep generated SQL injection-free.

    Names reaching here originate from file headers and from LLM output, so they
    are untrusted. Anything that is not a plain identifier is rejected outright
    rather than escaped.
    """
    if not _IDENTIFIER_RE.match(identifier):
        raise IntegrationError(
            f"Refusing to build SQL with unsafe identifier {identifier!r}. "
            "Identifiers must match [A-Za-z_][A-Za-z0-9_]*."
        )
    return f'"{identifier}"'


def _validate_aggregate(expression: str, source_columns: set[str]) -> str:
    """Allow only simple single-column aggregates over known columns."""
    match = _AGG_EXPR_RE.match(expression)
    if not match:
        raise IntegrationError(
            f"Unsupported aggregate expression {expression!r}. "
            "Expected a single call such as SUM(amount) or COUNT(*)."
        )

    fn = match.group("fn").upper()
    if fn not in _ALLOWED_AGGREGATES:
        raise IntegrationError(
            f"Aggregate function {fn!r} is not allowed. Permitted: {sorted(_ALLOWED_AGGREGATES)}"
        )

    arg = match.group("arg").strip()
    if arg == "*":
        return f"{fn}(*)"

    distinct = ""
    if arg.upper().startswith("DISTINCT"):
        distinct = "DISTINCT "
        arg = arg.split(None, 1)[1]

    if arg not in source_columns:
        raise IntegrationError(
            f"Aggregate {expression!r} references unknown column {arg!r}. "
            f"Available: {sorted(source_columns)[:15]}"
        )
    return f"{fn}({distinct}{_quote(arg)})"


def _build_aggregation_cte(agg: AggregationStep, source_columns: set[str]) -> str:
    missing = [c for c in agg.group_by if c not in source_columns]
    if missing:
        raise IntegrationError(
            f"Aggregation on {agg.source_table!r} groups by unknown column(s) {missing}."
        )

    selects = [_quote(c) for c in agg.group_by]
    for out_name, expression in agg.aggregations.items():
        selects.append(f"{_validate_aggregate(expression, source_columns)} AS {_quote(out_name)}")

    group_by = ", ".join(_quote(c) for c in agg.group_by)
    return (
        f"{_quote(agg.output_name)} AS (\n"
        f"    SELECT {', '.join(selects)}\n"
        f"    FROM {_quote(agg.source_table)}\n"
        f"    GROUP BY {group_by}\n"
        f")"
    )


def _build_join_clause(join: JoinStep, alias: str) -> str:
    conditions = " AND ".join(
        f"{_quote(join.left_table)}.{_quote(left)} = {alias}.{_quote(right)}"
        for left, right in zip(join.left_columns, join.right_columns, strict=True)
    )
    return f"{join.how.upper()} JOIN {_quote(join.right_table)} AS {alias}\n        ON {conditions}"


def build_sql(
    plan: IntegrationPlan | IntegrationPlanProposal, columns_by_table: dict[str, set[str]]
) -> str:
    """Compile a plan into a single readable SELECT statement."""
    if plan.base_table not in columns_by_table:
        raise IntegrationError(f"Base table {plan.base_table!r} was not provided.")

    derived_columns: dict[str, set[str]] = {}
    ctes: list[str] = []
    for agg in plan.aggregations:
        source_columns = columns_by_table.get(agg.source_table)
        if source_columns is None:
            raise IntegrationError(f"Aggregation source {agg.source_table!r} was not provided.")
        ctes.append(_build_aggregation_cte(agg, source_columns))
        derived_columns[agg.output_name] = set(agg.output_columns())

    available = {**{k: set(v) for k, v in columns_by_table.items()}, **derived_columns}

    # Select base columns explicitly, then only the new columns each join adds, so
    # duplicate key columns do not collide in the output.
    selects = [f"{_quote(plan.base_table)}.*"]
    join_clauses: list[str] = []

    for i, join in enumerate(plan.joins):
        right_columns = available.get(join.right_table)
        if right_columns is None:
            raise IntegrationError(f"Join references unknown table {join.right_table!r}.")

        alias = f"j{i}"
        join_clauses.append(_build_join_clause(join, alias))
        for column in sorted(right_columns - set(join.right_columns)):
            selects.append(f"{alias}.{_quote(column)} AS {_quote(column)}")

    sql = ""
    if ctes:
        sql += "WITH " + ",\n".join(ctes) + "\n"
    sql += "SELECT\n    " + ",\n    ".join(selects) + "\n"
    sql += f"FROM {_quote(plan.base_table)}"
    if join_clauses:
        sql += "\n    " + "\n    ".join(join_clauses)
    return sql


def execute_plan(
    plan: IntegrationPlan | IntegrationPlanProposal,
    frames: dict[str, pd.DataFrame],
) -> IntegrationResult:
    """Materialize the analytical base table described by ``plan``."""
    columns_by_table = {name: set(frame.columns) for name, frame in frames.items()}
    sql = build_sql(plan, columns_by_table)

    connection = duckdb.connect()
    try:
        for name, frame in frames.items():
            connection.register(_quote(name).strip('"'), frame)
        try:
            result = connection.execute(sql).fetch_df()
        except duckdb.Error as exc:  # pragma: no cover - surfaced with context
            raise IntegrationError(
                f"Failed to execute integration SQL: {exc}\n\nSQL:\n{sql}"
            ) from exc

        step_counts = {
            agg.output_name: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM (SELECT DISTINCT "
                    f"{', '.join(_quote(c) for c in agg.group_by)} "
                    f"FROM {_quote(agg.source_table)})"
                ).fetchone()[0]
            )
            for agg in plan.aggregations
        }
    finally:
        connection.close()

    base_rows = int(len(frames[plan.base_table]))
    result_rows = int(len(result))

    warnings: list[str] = []
    if result_rows > base_rows:
        # Deterministic detection of the failure the agent was warned about. We
        # do not trust the plan's claim that it aggregated correctly.
        raise IntegrationError(
            f"Join fan-out detected: base table {plan.base_table!r} has {base_rows:,} rows "
            f"but the ABT has {result_rows:,}. A one-to-many table was joined without "
            "being aggregated to the base grain, which would corrupt every later metric."
        )
    if result_rows < base_rows:
        warnings.append(
            f"ABT has {result_rows:,} rows against a {base_rows:,}-row base table; "
            f"{base_rows - result_rows:,} base rows were dropped by an inner join."
        )

    return IntegrationResult(
        frame=result,
        sql=sql,
        base_rows=base_rows,
        result_rows=result_rows,
        warnings=warnings,
        step_row_counts=step_counts,
    )


__all__ = [
    "IntegrationError",
    "IntegrationResult",
    "build_sql",
    "execute_plan",
]
