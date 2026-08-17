"""Tool-driven schema discovery and executor-owned plan evidence."""

from __future__ import annotations

import json
from typing import Any

from ads.agents.schema_discovery import build_context_with_evidence
from ads.agents.schema_investigator import investigate_schema
from ads.contracts import DataCard, RelationshipCandidate
from ads.contracts.integration import (
    IntegrationPlan,
    IntegrationPlanProposal,
    IntegrationTrial,
)
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration import CritiqueContext
from ads.pipeline import build_pipeline_rubrics
from ads.store import compute_artifact_id
from ads.tools import ToolRuntime
from ads.tools.integration import trial_integration_plan


def _plan(*, how: str = "left") -> dict[str, Any]:
    return {
        "base_table": "physicians__physician_master",
        "base_grain": ["physician_id"],
        "grain_description": "One row per physician.",
        "aggregations": [],
        "joins": [
            {
                "left_table": "physicians__physician_master",
                "right_table": "physicians__compensation",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": how,
                "rationale": "Measured physician identifier relationship.",
            }
        ],
        "warnings": [],
    }


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
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
        response = self.responses[self.calls]
        self.calls += 1
        return LLMResponse(
            text=json.dumps(response),
            model=profile.name,
            latency_s=0.0,
            parsed=response,
        )


def _context(
    cards: list[DataCard], relationships: list[RelationshipCandidate]
):
    return build_context_with_evidence(cards, relationships)


def test_trial_tool_returns_executor_owned_grain_measurements(
    cards: list[DataCard], frames: dict[str, Any]
) -> None:
    runtime = ToolRuntime.from_sources(cards, frames)

    payload = trial_integration_plan(runtime, {"plan": _plan()})
    trial = IntegrationTrial.model_validate(payload.data)

    assert trial.evidence_class == "deterministic"
    assert trial.execution_engine == "duckdb"
    assert trial.base_rows == 800
    assert trial.result_rows == 800
    assert trial.grain_preserved
    assert trial.base_duplicate_grain_rows == 0
    assert trial.result_duplicate_grain_rows == 0


def test_agent_must_submit_the_exact_plan_that_passed_the_trial(
    cards: list[DataCard],
    frames: dict[str, Any],
    relationships: list[RelationshipCandidate],
) -> None:
    tested = _plan(how="left")
    changed_after_trial = _plan(how="inner")
    llm = ScriptedLLM(
        [
            {
                "action": "call_tool",
                "tool_id": "trial_integration_plan",
                "arguments": {"plan": tested},
                "reason": "Measure the realized grain for this exact plan.",
            },
            {
                "action": "submit_plan",
                "plan": changed_after_trial,
                "reason": "Try to submit a semantically different join type.",
            },
            {
                "action": "abandon",
                "reason": "No tested plan remains to submit in this script.",
            },
        ]
    )

    result = investigate_schema(
        context=_context(cards, relationships),
        llm=llm,
        runtime=ToolRuntime.from_sources(cards, frames),
    )

    assert not result.succeeded
    assert "untested_plan" in {failure.code for failure in result.failures}


def test_agent_claim_cannot_replace_a_deterministic_plan_trial(
    cards: list[DataCard],
    frames: dict[str, Any],
    relationships: list[RelationshipCandidate],
) -> None:
    plan = _plan()
    llm = ScriptedLLM(
        [
            {
                "action": "submit_plan",
                "plan": plan,
                "reason": "I claim this plan preserves grain without executing it.",
            },
            {
                "action": "abandon",
                "reason": "The host correctly refused an unsupported claim.",
            },
        ]
    )

    result = investigate_schema(
        context=_context(cards, relationships),
        llm=llm,
        runtime=ToolRuntime.from_sources(cards, frames),
    )

    assert not result.succeeded
    assert "untested_plan" in {failure.code for failure in result.failures}
    assert IntegrationPlanProposal.model_validate(plan)


def test_schema_gate_binds_plan_to_the_exact_persisted_trial(
    cards: list[DataCard],
    frames: dict[str, Any],
    relationships: list[RelationshipCandidate],
) -> None:
    proposal = IntegrationPlanProposal.model_validate(_plan())
    trial = IntegrationTrial.model_validate(
        trial_integration_plan(
            ToolRuntime.from_sources(cards, frames), {"plan": proposal.model_dump()}
        ).data
    )
    criterion = next(
        item
        for item in build_pipeline_rubrics().get("schema_discovery").criteria
        if item.id == "schema.plan_trial_passed"
    )
    assert criterion.check is not None

    bound = IntegrationPlan.from_proposal(
        proposal,
        relationships,
        trial_artifact_id=compute_artifact_id(trial),
    )
    substituted = IntegrationPlan.from_proposal(
        proposal,
        relationships,
        trial_artifact_id="0" * 64,
    )

    assert criterion.check(CritiqueContext("schema_discovery", [bound, trial]))
    assert not criterion.check(
        CritiqueContext("schema_discovery", [substituted, trial])
    )
