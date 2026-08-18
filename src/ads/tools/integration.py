"""Deterministic integration-plan execution tools."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.contracts.integration import (
    IntegrationPlanProposal,
    IntegrationTrial,
    integration_plan_fingerprint,
)
from ads.integration import execute_plan
from ads.tools.models import ToolPayload, ToolRuntime
from ads.tools.registry import ToolDefinition, ToolRegistry


def trial_integration_plan(
    runtime: ToolRuntime,
    arguments: Mapping[str, Any],
) -> ToolPayload:
    """Execute an agent-supplied plan and return executor-owned measurements."""
    raw_plan = arguments.get("plan")
    if not isinstance(raw_plan, Mapping):
        raise ValueError("trial_integration_plan requires a plan object.")
    plan = IntegrationPlanProposal.model_validate(dict(raw_plan))
    result = execute_plan(plan, runtime.frames)
    trial = IntegrationTrial(
        plan_fingerprint=integration_plan_fingerprint(plan),
        base_table=plan.base_table,
        base_grain=plan.base_grain,
        base_rows=result.base_rows,
        result_rows=result.result_rows,
        base_duplicate_grain_rows=result.base_duplicate_grain_rows,
        result_duplicate_grain_rows=result.result_duplicate_grain_rows,
        base_null_grain_rows=result.base_null_grain_rows,
        result_null_grain_rows=result.result_null_grain_rows,
        grain_preserved=result.grain_preserved,
        result_columns=list(result.frame.columns),
        step_row_counts=result.step_row_counts,
        warnings=result.warnings,
        sql_hash=hashlib.sha256(result.sql.encode()).hexdigest(),
    )
    data = trial.model_dump(mode="json", exclude={"created_at"})
    summary = (
        f"trial_integration_plan measured base_rows={trial.base_rows}, "
        f"result_rows={trial.result_rows}, grain_preserved={trial.grain_preserved}, "
        f"base_duplicate_grain_rows={trial.base_duplicate_grain_rows}, "
        f"result_duplicate_grain_rows={trial.result_duplicate_grain_rows}, "
        f"base_null_grain_rows={trial.base_null_grain_rows}, "
        f"result_null_grain_rows={trial.result_null_grain_rows}"
    )
    return ToolPayload(summary=summary, data=data)


def register_integration_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(
        ToolDefinition(
            tool_id="trial_integration_plan",
            arguments={
                'plan': (
                    'IntegrationPlanProposal object: base_table, base_grain, '
                    'joins[], aggregations[]'
                ),
            },
            tier=PermissionTier.READ_DATA,
            description=(
                "Execute a proposed integration plan in DuckDB and measure its realized grain."
            ),
            handler=trial_integration_plan,
        )
    )
    return registry


__all__ = ["register_integration_tools", "trial_integration_plan"]
