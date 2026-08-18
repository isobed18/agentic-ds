"""Configurable investigator budgets with fixed safety ceilings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.agents.problem_discovery import build_context
from ads.agents.problem_investigator import investigate_problem_context
from ads.agents.runtime import AgentRuntimePolicy, InvestigationBudget
from ads.contracts.gates import BUILTIN_PROFILES
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration import RunState
from ads.pipeline import agent_runtime_policy, configure_full_pipeline_state
from ads.store import ArtifactStore
from ads.tools import ToolRuntime


class _LLM:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.calls = 0

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        del system, prompt, json_schema
        action = self.actions[self.calls]
        self.calls += 1
        return LLMResponse(
            text=json.dumps(action),
            parsed=action,
            model=profile.name,
            latency_s=0.01,
        )


class _AvailableBackend:
    def __init__(self, root: Path) -> None:
        self.data_dir = root / "data"
        self.artifacts_dir = root / "artifacts"
        self.data_dir.mkdir(parents=True)
        self.artifacts_dir.mkdir(parents=True)

    def available(self) -> bool:
        return True

    def create_session(self, run_id: str) -> str:
        raise AssertionError(f"disabled execution created a session for {run_id}")

    def execute(self, session: str, code: str, timeout: float = 30.0):
        raise AssertionError("disabled execution reached the backend")

    def destroy(self, session: str) -> None:
        raise AssertionError("no disabled session should need destruction")


def _inputs():
    frame = pd.DataFrame({"target": [index % 2 for index in range(120)], "feature": range(120)})
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    return frame, card, build_context(card)


def test_policy_rejects_unknown_stages_and_cannot_configure_permissions() -> None:
    with pytest.raises(ValidationError, match="Unknown investigation stages"):
        AgentRuntimePolicy(
            budgets={
                "not_a_stage": InvestigationBudget(
                    max_turns=2,
                    max_transcript_chars=2_000,
                    max_tool_calls=1,
                )
            }
        )
    with pytest.raises(ValidationError, match="extra"):
        AgentRuntimePolicy.model_validate({"max_permission_tier": "mutate_source"})


def test_pipeline_state_preserves_the_injected_policy(tmp_path: Path) -> None:
    policy = AgentRuntimePolicy(
        budgets={
            "schema_investigation": InvestigationBudget(
                max_turns=3,
                max_transcript_chars=4_000,
                max_tool_calls=2,
            )
        },
        code_execution_disabled=frozenset({"schema_investigation"}),
    )
    state = RunState(
        run_id="runtime-policy",
        store=ArtifactStore(tmp_path / "store"),
        profile=BUILTIN_PROFILES["checkpointed"],
    )

    configure_full_pipeline_state(
        state,
        source_path=tmp_path / "sources",
        agent_runtime_policy=policy,
    )

    assert agent_runtime_policy(state) == policy
    assert agent_runtime_policy(state).budget("schema_investigation").max_turns == 3
    assert agent_runtime_policy(state).code_enabled("schema_investigation") is False


def test_custom_tool_budget_has_behavioral_effect() -> None:
    frame, card, context = _inputs()
    result = investigate_problem_context(
        context=context,
        llm=_LLM(
            [
                {
                    "action": "call_tool",
                    "tool_id": "null_rate",
                    "arguments": {"table": "abt", "column": "target"},
                    "reason": "Measure target coverage.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "cardinality",
                    "arguments": {"table": "abt", "column": "target"},
                    "reason": "Try a second call beyond the configured budget.",
                },
                {
                    "action": "finish",
                    "reason": "The available measurement is enough for the proposal panel.",
                },
            ]
        ),
        runtime=ToolRuntime.from_sources([card], {"abt": frame}),
        budget=InvestigationBudget(
            max_turns=3,
            max_transcript_chars=2_000,
            max_tool_calls=1,
        ),
    )

    assert result.audit.valid_members == 1
    assert result.audit.evidence_tools == ["null_rate"]
    assert "tool_call_budget_exhausted" in result.audit.members[0].validation_failures


def test_failed_tool_attempt_consumes_the_configured_budget() -> None:
    frame, card, context = _inputs()
    result = investigate_problem_context(
        context=context,
        llm=_LLM(
            [
                {
                    "action": "call_tool",
                    "tool_id": "null_rate",
                    "arguments": {"table": "abt", "column": "does_not_exist"},
                    "reason": "This attempted measurement must still consume budget.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "cardinality",
                    "arguments": {"table": "abt", "column": "target"},
                    "reason": "This valid second call must now be refused.",
                },
                {
                    "action": "finish",
                    "reason": "No successful measurement remains.",
                },
            ]
        ),
        runtime=ToolRuntime.from_sources([card], {"abt": frame}),
        budget=InvestigationBudget(
            max_turns=3,
            max_transcript_chars=2_000,
            max_tool_calls=1,
        ),
    )

    failures = result.audit.members[0].validation_failures
    assert result.audit.valid_members == 0
    assert result.audit.evidence_tools == []
    assert "tool_error:null_rate" in failures
    assert "tool_call_budget_exhausted" in failures


def test_disabling_code_removes_execute_tool_and_never_touches_backend(
    tmp_path: Path,
) -> None:
    frame, card, context = _inputs()
    result = investigate_problem_context(
        context=context,
        llm=_LLM(
            [
                {
                    "action": "call_tool",
                    "tool_id": "execute_python",
                    "arguments": {"code": "print('must not run')", "timeout": 30},
                    "reason": "Attempt disabled authored execution.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "null_rate",
                    "arguments": {"table": "abt", "column": "target"},
                    "reason": "Use an allowed deterministic measurement instead.",
                },
                {
                    "action": "finish",
                    "reason": "A deterministic target measurement is available.",
                },
            ]
        ),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            execution_backend=_AvailableBackend(tmp_path),
        ),
        code_execution_enabled=False,
    )

    assert result.audit.valid_members == 1
    assert "execute_python" not in result.audit.allowed_tools
    assert result.audit.evidence_tools == ["null_rate"]
    assert result.audit.raw_rows_shared is False
