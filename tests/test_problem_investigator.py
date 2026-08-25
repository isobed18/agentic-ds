"""Live checks for agent-directed problem investigation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ads.agents.problem_discovery import attach_support, build_context
from ads.agents.problem_investigator import investigate_problem_context
from ads.contracts import Metric, ProblemDiscoveryProposal, TaskType
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.sandbox import SandboxConfig, SandboxManager, materialize_frame_copies
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


def test_agent_authored_analysis_cannot_supply_problem_support() -> None:
    frame = pd.DataFrame(
        {
            "feature": list(range(150)),
            "churned": [index % 5 == 0 for index in range(150)],
        }
    )
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    proposal = ProblemDiscoveryProposal.model_validate(
        {
            "candidates": [
                {
                    "title": "Churn prediction",
                    "title_tr": "Müşteri kaybı tahmini",
                    "task_type": TaskType.BINARY_CLASSIFICATION,
                    "target_column": "churned",
                    "business_rationale": "Identify accounts likely to churn.",
                    "business_rationale_tr": "Kaybetme olasılığı yüksek hesapları belirle.",
                    "evidence_columns": ["feature"],
                    "primary_metric": Metric.ROC_AUC,
                }
            ]
        }
    )

    measured = attach_support(proposal, card, frame)

    assert measured.candidates[0].support.minority_class_count == 30
    assert measured.candidates[0].support.is_viable is False
    assert any(
        "minority_class_below_floor" in reason
        for reason in measured.candidates[0].support.blocking_reasons
    )


def test_live_problem_scout_chooses_tools_and_executes_read_only_code(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    artifacts_dir = tmp_path / "artifacts"
    data_dir.mkdir()
    artifacts_dir.mkdir()
    backend = SandboxManager(SandboxConfig(data_dir=data_dir, artifacts_dir=artifacts_dir))
    if not backend.docker_available():
        pytest.skip("Docker daemon is unavailable")
    if not backend.image_available():
        pytest.skip("ads-sandbox:latest is not built")

    frame = pd.DataFrame(
        {
            "account_age_days": list(range(150)),
            "churned": [index % 5 == 0 for index in range(150)],
            "revenue": [float(100 + index * 3) for index in range(150)],
        }
    )
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    materialize_frame_copies(backend, {"abt": frame})
    context = build_context(card, user_intent="Find a useful supervised business problem.")
    result = investigate_problem_context(
        context=context,
        llm=_LLM(
            [
                {
                    "action": "call_tool",
                    "tool_id": "column_profile",
                    "arguments": {"table": "abt", "column": "churned"},
                    "reason": "Check the semantic shape of a plausible churn target.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "value_counts",
                    "arguments": {"table": "abt", "column": "churned"},
                    "reason": "Measure whether both churn classes have support.",
                },
                {
                    "action": "call_tool",
                    "tool_id": "execute_python",
                    "arguments": {
                        "code": (
                            "import pandas as pd\n"
                            "frame = pd.read_csv('/data/abt.csv')\n"
                            "print({'rows': len(frame), "
                            "'churn_rate': round(float(frame['churned'].mean()), 4)})"
                        ),
                        "timeout": 30,
                    },
                    "reason": "Check a compact target-support calculation in the sandbox.",
                },
                {
                    "action": "finish",
                    "reason": "The proposal panel now has target type and support context.",
                },
            ]
        ),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            run_id="live-problem-scout",
            execution_backend=backend,
            artifacts_dir=artifacts_dir,
        ),
    )

    assert result.degraded_reason is None
    assert result.audit.valid_members == 1
    assert result.audit.evidence_tools == [
        "column_profile",
        "value_counts",
        "execute_python",
    ]
    assert result.audit.raw_rows_shared is True
    assert "Agent-directed investigation" in context.sections
    assert "churn_rate" in context.sections["Agent-directed investigation"]
