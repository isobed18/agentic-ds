"""Active validation-strategy trial loop tests."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ads.agents.validation_investigator import investigate_validation_context
from ads.agents.validation_strategy import build_context
from ads.contracts import ValidationSignals
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
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


def test_agent_trials_an_exact_strategy_before_finishing() -> None:
    frame = pd.DataFrame(
        {
            "entity_id": [f"e{entity:03d}" for entity in range(60) for _ in range(2)],
            "target": [entity % 2 for entity in range(60) for _ in range(2)],
        }
    )
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    signals = ValidationSignals(
        n_rows=len(frame),
        n_usable_rows=len(frame),
        n_folds=3,
        target_column="target",
    )
    context = build_context(card, signals)
    proposal = {
        "strategy": "grouped",
        "n_folds": 3,
        "test_size": 0.2,
        "group_column": "entity_id",
        "time_column": None,
        "holdout_cutoff": None,
        "rationale": "Keep entities isolated.",
    }

    audit = investigate_validation_context(
        context=context,
        llm=_LLM(
            [
                {
                    "action": "call_tool",
                    "tool_id": "trial_validation_strategy",
                    "arguments": {"table": "abt", "proposal": proposal},
                    "reason": "Measure whether grouped folds are usable and isolated.",
                },
                {
                    "action": "finish",
                    "reason": "The strongest applicable strategy has a passing trial.",
                },
            ]
        ),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            resources={"validation_signals": signals},
        ),
    )

    assert audit.valid_members == 1
    assert audit.evidence_tools == ["trial_validation_strategy"]
    assert "passed=True" in context.sections["Agent-directed validation trials"]
    assert "group_overlap=0" in context.sections["Agent-directed validation trials"]
