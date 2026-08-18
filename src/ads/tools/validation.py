"""Registered deterministic validation-strategy trial tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ads.contracts.gates import PermissionTier
from ads.contracts.validation import ValidationSignals, ValidationStrategyProposal
from ads.splitting import execute_validation_trial
from ads.tools.models import ToolPayload, ToolRuntime
from ads.tools.registry import ToolDefinition, ToolRegistry


def trial_validation_strategy(
    runtime: ToolRuntime, arguments: Mapping[str, Any]
) -> ToolPayload:
    table = str(arguments["table"])
    if table not in runtime.frames:
        raise ValueError(f"Unknown table {table!r}.")
    signals = runtime.resources.get("validation_signals")
    if not isinstance(signals, ValidationSignals):
        raise ValueError("Host validation signals are unavailable.")
    proposal = ValidationStrategyProposal.model_validate(arguments["proposal"])
    trial = execute_validation_trial(runtime.frames[table], proposal, signals)
    data = trial.model_dump(mode="json", exclude={"created_at"})
    return ToolPayload(
        summary=(
            f"trial_validation_strategy measured strategy={trial.strategy.value}, "
            f"passed={trial.passed}, train={trial.n_train_rows}, "
            f"holdout={trial.n_holdout_rows}, folds={len(trial.fold_sizes)}, "
            f"group_overlap={trial.group_overlap_count}, "
            f"temporal_violations={trial.temporal_order_violation_count}"
        ),
        data=data,
    )


def register_validation_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(
        ToolDefinition(
            tool_id="trial_validation_strategy",
            arguments={
                'table': 'table name to split',
                'proposal': (
                    'ValidationStrategyProposal object: strategy, n_folds, '
                    'test_size, group_column, time_column'
                ),
            },
            tier=PermissionTier.READ_DATA,
            description="Execute and measure one exact validation strategy proposal.",
            handler=trial_validation_strategy,
        )
    )
    return registry


__all__ = ["register_validation_tools", "trial_validation_strategy"]
