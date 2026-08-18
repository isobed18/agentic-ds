"""Configurable memory and tool budgets for active investigation loops."""

from __future__ import annotations

from pydantic import Field, model_validator

from ads.contracts.base import FrozenModel

ACTIVE_INVESTIGATION_STAGES = frozenset(
    {
        "schema_investigation",
        "problem_investigation",
        "validation_investigation",
        "eda_investigation",
        "leakage_investigation",
        "feature_investigation",
        "model_investigation",
        "sensitivity_investigation",
    }
)


class InvestigationBudget(FrozenModel):
    """Bounded rolling context and action budget for one active agent."""

    max_turns: int = Field(ge=1, le=30)
    max_transcript_chars: int = Field(ge=1_000, le=100_000)
    max_tool_calls: int = Field(ge=1, le=30)


DEFAULT_INVESTIGATION_BUDGETS = {
    "schema_investigation": InvestigationBudget(
        max_turns=12, max_transcript_chars=30_000, max_tool_calls=12
    ),
    "problem_investigation": InvestigationBudget(
        max_turns=8, max_transcript_chars=20_000, max_tool_calls=8
    ),
    "validation_investigation": InvestigationBudget(
        max_turns=8, max_transcript_chars=20_000, max_tool_calls=8
    ),
    "eda_investigation": InvestigationBudget(
        max_turns=10, max_transcript_chars=24_000, max_tool_calls=12
    ),
    "leakage_investigation": InvestigationBudget(
        max_turns=8, max_transcript_chars=20_000, max_tool_calls=8
    ),
    "feature_investigation": InvestigationBudget(
        max_turns=10, max_transcript_chars=24_000, max_tool_calls=12
    ),
    "sensitivity_investigation": InvestigationBudget(
        max_turns=1, max_transcript_chars=8_000, max_tool_calls=1
    ),
    "model_investigation": InvestigationBudget(
        max_turns=10, max_transcript_chars=24_000, max_tool_calls=12
    ),
}


class AgentRuntimePolicy(FrozenModel):
    """Per-run investigator configuration; safety permissions are not configurable.

    Disabling authored code removes ``execute_python`` from the affected agent's
    tool vocabulary. It cannot add tools, raise a permission tier, enable source
    mutation, or introduce a host-execution fallback.
    """

    budgets: dict[str, InvestigationBudget] = Field(default_factory=dict)
    code_execution_disabled: frozenset[str] = Field(default_factory=frozenset)
    #: Investigators switched off entirely. Distinct from disabling code: that
    #: keeps the agent and removes one tool, this removes the agent. The
    #: mandatory deterministic floor for the stage runs either way, so turning
    #: an investigator off costs analysis, never safety.
    investigators_disabled: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _known_stages_only(self) -> AgentRuntimePolicy:
        unknown = (
            set(self.budgets)
            | set(self.code_execution_disabled)
            | set(self.investigators_disabled)
        ) - set(ACTIVE_INVESTIGATION_STAGES)
        if unknown:
            raise ValueError(f"Unknown investigation stages: {sorted(unknown)}")
        return self

    def budget(self, stage_id: str) -> InvestigationBudget:
        if stage_id not in ACTIVE_INVESTIGATION_STAGES:
            raise ValueError(f"Unknown investigation stage {stage_id!r}.")
        return self.budgets.get(stage_id, DEFAULT_INVESTIGATION_BUDGETS[stage_id])

    def code_enabled(self, stage_id: str) -> bool:
        if stage_id not in ACTIVE_INVESTIGATION_STAGES:
            raise ValueError(f"Unknown investigation stage {stage_id!r}.")
        return stage_id not in self.code_execution_disabled

    def investigator_enabled(self, stage_id: str) -> bool:
        if stage_id not in ACTIVE_INVESTIGATION_STAGES:
            raise ValueError(f"Unknown investigation stage {stage_id!r}.")
        return stage_id not in self.investigators_disabled


DEFAULT_AGENT_RUNTIME_POLICY = AgentRuntimePolicy()


__all__ = [
    "ACTIVE_INVESTIGATION_STAGES",
    "AgentRuntimePolicy",
    "DEFAULT_AGENT_RUNTIME_POLICY",
    "DEFAULT_INVESTIGATION_BUDGETS",
    "InvestigationBudget",
]
