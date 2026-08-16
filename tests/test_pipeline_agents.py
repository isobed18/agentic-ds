"""Agent-backed pipeline orchestration with scripted structured LLM responses."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ads.contracts.agents import AgentAudit
from ads.contracts.base import ArtifactType
from ads.contracts.comprehension import ComprehensionBrief
from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict
from ads.contracts.integration import IntegrationPlan
from ads.contracts.problem import ProblemCandidateSet, ProblemDefinition
from ads.contracts.validation import ValidationStrategy
from ads.gates import GatePolicy
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration import RunState, linear_spec, run_workflow
from ads.pipeline import (
    FINAL_MARKDOWN_KEY,
    FinalReport,
    build_full_spec,
    build_full_spec_definition,
    build_pipeline_rubrics,
    configure_full_pipeline_state,
)
from ads.store import ArtifactStore


class FakeLLM:
    """Replay one response per orchestration attempt; never contacts Ollama."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        index = len(self.calls)
        if index >= len(self.responses):
            raise AssertionError(f"Unexpected LLM call {index + 1}; script is exhausted.")
        self.calls.append(
            {
                "system": system,
                "prompt": prompt,
                "schema": json_schema,
                "profile": profile,
            }
        )
        item = self.responses[index]
        text = item if isinstance(item, str) else json.dumps(item)
        parsed: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            candidate = json.loads(text)
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                parse_error = "Expected an object."
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
        return LLMResponse(
            text=text,
            model=profile.name,
            latency_s=0.0,
            parsed=parsed,
            parse_error=parse_error,
        )


class InterpretationUnavailableLLM(FakeLLM):
    """Fail only advisory interpretation calls; planning calls remain scripted."""

    def __init__(
        self,
        responses: list[Any],
        failure_type: type[Exception] = ConnectionError,
    ) -> None:
        super().__init__(responses)
        self.interpretation_calls = 0
        self.failure_type = failure_type

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        if json_schema.get("title") == "InterpretationBatchProposal":
            self.interpretation_calls += 1
            raise self.failure_type("local interpretation model failed")
        return super().generate_structured(
            system=system,
            prompt=prompt,
            json_schema=json_schema,
            profile=profile,
        )


def test_full_spec_definition_needs_no_llm_and_exposes_planner_agents() -> None:
    spec = build_full_spec_definition()

    assert spec.name == "agent-backed-full"
    assert spec.version == "2"
    assert spec.stage_ids()[:5] == (
        "intake",
        "schema_discovery",
        "integration",
        "problem_discovery",
        "validation_strategy",
    )
    assert "source_comprehension" not in spec.stage_ids()
    assert "analysis_comprehension" not in spec.stage_ids()


def _schema_response() -> dict[str, Any]:
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
                "how": "left",
                "rationale": "Measured one-to-one physician id relationship.",
            }
        ],
        "warnings": [],
    }


def _problem_response() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "title": "Predict annual physician compensation",
                "task_type": "regression",
                "target_column": "annual_comp",
                "business_rationale": (
                    "Estimate compensation from pre-outcome physician attributes."
                ),
                "evidence_columns": [
                    "years_experience",
                    "specialty",
                    "city",
                    "hire_date",
                ],
                "primary_metric": "rmse",
            }
        ]
    }


def _validation_response() -> dict[str, Any]:
    return {
        "strategy": "temporal",
        "n_folds": 3,
        "test_size": 0.2,
        "group_column": None,
        "time_column": "hire_date",
        "holdout_cutoff": "2019-01-01",
        "rationale": "Later hires simulate the future deployment population.",
    }


def _state(tmp_path: Path, sample_dir: Path, run_id: str) -> RunState:
    state = RunState(
        run_id=run_id,
        store=ArtifactStore(tmp_path / run_id),
        profile=BUILTIN_PROFILES["full_auto"],
        user_intent="Build a compensation baseline.",
    )
    configure_full_pipeline_state(
        state,
        source_path=sample_dir,
        candidate_limit=2,
        validation_folds=3,
    )
    return state


def _policy_with_leakage_retry() -> GatePolicy:
    policy = GatePolicy.load()
    return replace(
        policy,
        stages={
            **policy.stages,
            "leakage_audit": replace(
                policy.stage("leakage_audit"), max_attempts=2
            ),
        },
    )


def test_full_agent_backed_spec_runs_end_to_end_without_ollama(
    tmp_path: Path, sample_dir: Path
) -> None:
    llm = FakeLLM(
        [
            _schema_response(),
            {"items": []},
            _problem_response(),
            _validation_response(),
            {"items": []},
        ]
    )
    spec, registry = build_full_spec(llm)
    state = _state(tmp_path, sample_dir, "full-agent-pipeline")

    outcome = run_workflow(
        spec,
        registry,
        state,
        policy=_policy_with_leakage_retry(),
        rubrics=build_pipeline_rubrics(),
    )

    assert outcome.completed, outcome.error
    assert len(llm.calls) == 5
    assert state.attempt_count("schema_discovery") == 1
    assert state.attempt_count("problem_discovery") == 1
    assert state.attempt_count("validation_strategy") == 1
    assert state.attempt_count("leakage_audit") == 2
    assert state.attempts_for("leakage_audit")[0].decision.verdict is GateVerdict.RETRY

    plan = state.require(ArtifactType.INTEGRATION_PLAN, IntegrationPlan)
    candidates = state.require(ArtifactType.PROBLEM_CANDIDATES, ProblemCandidateSet)
    problem = state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)
    strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
    final = state.require(ArtifactType.FINAL_REPORT, FinalReport, name="final_report")
    assert plan.base_table == "physicians__physician_master"
    assert candidates.viable()
    assert problem.target_column == "annual_comp"
    assert problem.excluded_columns == ["total_comp_ytd"]
    assert strategy.strategy.value == "temporal"
    assert final.markdown == state.blackboard[FINAL_MARKDOWN_KEY]


@pytest.mark.parametrize("failure_type", [ConnectionError, ValueError])
def test_unavailable_interpretation_model_does_not_block_report(
    tmp_path: Path, sample_dir: Path, failure_type: type[Exception]
) -> None:
    llm = InterpretationUnavailableLLM(
        [_schema_response(), _problem_response(), _validation_response()],
        failure_type=failure_type,
    )
    spec, registry = build_full_spec(llm)
    state = _state(
        tmp_path, sample_dir, f"interpretation-{failure_type.__name__}"
    )

    outcome = run_workflow(
        spec,
        registry,
        state,
        policy=_policy_with_leakage_retry(),
        rubrics=build_pipeline_rubrics(),
    )

    assert outcome.completed, outcome.error
    assert llm.interpretation_calls == 2
    state.require(ArtifactType.FINAL_REPORT, FinalReport, name="final_report")
    briefs = state.store.load_all(
        state.run_id, ArtifactType.COMPREHENSION_BRIEF, ComprehensionBrief
    )
    assert len(briefs) == 2
    assert {brief.scope.value for brief in briefs} == {"source", "analysis"}
    assert all(brief.degraded and not brief.items for brief in briefs)
    assert all(
        failure_type.__name__ in (brief.degradation_reason or "")
        for brief in briefs
    )
    audits = state.store.load_all(
        state.run_id, ArtifactType.AGENT_AUDIT, AgentAudit
    )
    by_stage = {audit.stage_id: audit for audit in audits}
    for stage_id in ("source_comprehension", "analysis_comprehension"):
        audit = by_stage[stage_id]
        assert audit.valid_members == 0
        assert audit.members[0].accepted is False
        assert audit.members[0].validation_failures == [
            f"interpretation_error:{failure_type.__name__}"
        ]


def test_malformed_interpretation_output_does_not_block_report(
    tmp_path: Path, sample_dir: Path
) -> None:
    llm = FakeLLM(
        [
            _schema_response(),
            "not JSON",
            _problem_response(),
            _validation_response(),
            "also not JSON",
        ]
    )
    spec, registry = build_full_spec(llm)
    state = _state(tmp_path, sample_dir, "interpretation-malformed")

    outcome = run_workflow(
        spec,
        registry,
        state,
        policy=_policy_with_leakage_retry(),
        rubrics=build_pipeline_rubrics(),
    )

    assert outcome.completed, outcome.error
    state.require(ArtifactType.FINAL_REPORT, FinalReport, name="final_report")
    briefs = state.store.load_all(
        state.run_id, ArtifactType.COMPREHENSION_BRIEF, ComprehensionBrief
    )
    assert len(briefs) == 2
    assert all(brief.degraded and not brief.items for brief in briefs)
    assert all("invalid_json" in (brief.degradation_reason or "") for brief in briefs)


def test_invalid_agent_contract_retries_through_orchestrator(
    tmp_path: Path, sample_dir: Path
) -> None:
    llm = FakeLLM(["not JSON", _schema_response(), {"items": []}])
    full_spec, registry = build_full_spec(llm)
    observed_validation_failures: list[int] = []
    schema_component = registry.resolve("pipeline.schema_discovery")

    def capture_signals(state: RunState, correction=None):
        result = schema_component(state, correction)
        observed_validation_failures.append(result.signals.validation_failures)
        return result

    registry.components["pipeline.schema_discovery"] = capture_signals
    selected = tuple(
        stage
        for stage in full_spec.stages
        if stage.id in {"intake", "schema_discovery"}
    )
    spec = linear_spec(
        "agent-contract-retry",
        "1",
        selected,
        retry_stages=("schema_discovery",),
    )
    state = _state(tmp_path, sample_dir, "agent-contract-retry")

    outcome = run_workflow(spec, registry, state, rubrics=build_pipeline_rubrics())

    assert outcome.completed, outcome.error
    attempts = state.attempts_for("schema_discovery")
    assert len(attempts) == 2
    assert attempts[0].error is None, "invalid model output is evidence, not a crash"
    assert attempts[0].critique is not None
    assert attempts[0].critique.rubric_version == "schema_discovery.v1"
    assert attempts[0].decision is not None
    assert attempts[0].decision.verdict is GateVerdict.RETRY
    assert attempts[0].decision.reason_code in {
        "unmet_mandatory_criteria",
        "critique_errors",
    }
    assert attempts[1].decision is not None
    assert attempts[1].decision.verdict is GateVerdict.AUTO_PROCEED
    assert len(llm.calls) == 3
    assert observed_validation_failures == [1, 0]
    assert "Orchestrator correction" in llm.calls[1]["prompt"]
    assert "schema.base_grain_declared" in llm.calls[1]["prompt"]


def test_agent_panel_persists_contract_tool_and_agreement_audit(
    tmp_path: Path, sample_dir: Path
) -> None:
    llm = FakeLLM([_schema_response(), _schema_response(), {"items": []}])
    full_spec, registry = build_full_spec(llm, panel_size=2)
    selected = tuple(
        stage for stage in full_spec.stages if stage.id in {"intake", "schema_discovery"}
    )
    spec = linear_spec("agent-panel-audit", "1", selected)
    state = _state(tmp_path, sample_dir, "agent-panel-audit")

    outcome = run_workflow(spec, registry, state, rubrics=build_pipeline_rubrics())

    assert outcome.completed, outcome.error
    audit = state.require(ArtifactType.AGENT_AUDIT, AgentAudit, name="agent_audit")
    assert audit.panel_size == 2
    assert audit.valid_members == 2
    assert audit.agreement == 1.0
    assert audit.allowed_tools == ["candidate_keys", "join_overlap"]
    assert "candidate_keys" in audit.evidence_tools
    assert set(audit.evidence_tools) <= {"candidate_keys", "join_overlap"}
    assert audit.pydantic_contract_enforced is True
    assert audit.raw_rows_shared is False
    assert len(llm.calls) == 3
