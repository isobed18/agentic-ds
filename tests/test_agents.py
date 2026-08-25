"""Agent layer tests.

Every test here runs against a scripted fake LLM. Agent *logic* — validation,
repair, corrective retry — must be testable without a GPU, a model download, or
network access, otherwise it will not be tested at all in CI. Tests that need a
real model are marked ``llm`` and skipped by default.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ads.agents.base import (
    AgentContext,
    AgentFailedError,
    AgentSpec,
    ToolEvidence,
    ValidationFailure,
    format_failures,
    run_agent,
    run_agent_panel,
    suggest_name,
)
from ads.agents.schema_discovery import (
    build_context_with_evidence,
    build_spec,
    validate_columns_exist,
    validate_fan_out_is_aggregated,
    validate_joins_supported_by_evidence,
    validate_tables_exist,
)
from ads.contracts import IntegrationPlan, IntegrationPlanProposal
from ads.llm.client import LARGE, LLMResponse, ModelProfile, dereference_schema


class FakeLLM:
    """Replays scripted responses so agent logic is testable without a model."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self, *, system: str, prompt: str, json_schema: dict, profile: ModelProfile
    ) -> LLMResponse:
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema})
        item = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        text = item if isinstance(item, str) else json.dumps(item)
        parsed = None
        error = None
        try:
            candidate = json.loads(text)
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                error = "not an object"
        except json.JSONDecodeError as exc:
            error = str(exc)
        return LLMResponse(
            text=text, model=profile.name, latency_s=0.0, parsed=parsed, parse_error=error
        )


def _valid_proposal() -> dict[str, Any]:
    return {
        "base_table": "physicians",
        "base_grain": ["physician_id"],
        "grain_description": "One row per physician.",
        "aggregations": [
            {
                "source_table": "transactions",
                "output_name": "txn_by_physician",
                "group_by": ["physician_id"],
                "aggregations": {"total_amount": "SUM(amount)"},
                "rationale": "N:1 fan-out must be rolled up.",
            }
        ],
        "joins": [
            {
                "left_table": "physicians",
                "right_table": "txn_by_physician",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": "left",
                "rationale": "Attach aggregated transaction metrics.",
            }
        ],
        "warnings": [],
    }


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        sections={"Tables": "..."},
        facts={
            "table_names": ["physicians", "transactions"],
            "columns_by_table": {
                "physicians": ["physician_id", "specialty"],
                "transactions": ["txn_id", "physician_id", "amount"],
            },
            "known_columns": ["physician_id", "specialty", "txn_id", "amount"],
            "measured_pairs": {("transactions", "physician_id", "physicians", "physician_id")},
            "cardinality_by_pair": {("transactions", "physicians"): "N:1"},
        },
    )


def _spec(**overrides: Any) -> AgentSpec[IntegrationPlanProposal]:
    base = build_spec()
    return AgentSpec(
        id=base.id,
        system_prompt=base.system_prompt,
        output_contract=base.output_contract,
        profile=overrides.get("profile", LARGE),
        validators=overrides.get("validators", base.validators),
        max_attempts=overrides.get("max_attempts", base.max_attempts),
        rubric=base.rubric,
        column_fields=base.column_fields,
    )


class TestSchemaGeneration:
    def test_generated_schema_has_no_dangling_refs(self) -> None:
        """Grammar compilers handle inlined schemas far more reliably than $ref."""
        schema = dereference_schema(IntegrationPlanProposal.model_json_schema())
        rendered = json.dumps(schema)
        assert "$ref" not in rendered
        assert "$defs" not in rendered

    def test_agent_contract_excludes_measured_evidence(self) -> None:
        """The agent must not author evidence or bookkeeping fields."""
        fields = IntegrationPlanProposal.model_fields
        assert "evidence" not in fields
        assert "created_at" not in fields
        assert "evidence" in IntegrationPlan.model_fields


class TestRunAgent:
    def test_valid_first_response_accepted(self, context: AgentContext) -> None:
        llm = FakeLLM([_valid_proposal()])
        result = run_agent(_spec(), context, llm)
        assert result.succeeded
        assert result.first_pass_valid
        assert result.n_attempts == 1
        assert result.require().base_table == "physicians"

    def test_invalid_json_triggers_retry(self, context: AgentContext) -> None:
        llm = FakeLLM(["not json at all", _valid_proposal()])
        result = run_agent(_spec(), context, llm)
        assert result.succeeded
        assert result.n_attempts == 2
        assert not result.first_pass_valid
        assert result.attempts[0].failures[0].code == "invalid_json"

    def test_retry_budget_exhausted_returns_failure(self, context: AgentContext) -> None:
        llm = FakeLLM(["broken"])
        result = run_agent(_spec(max_attempts=2), context, llm)
        assert not result.succeeded
        assert result.output is None
        assert result.n_attempts == 2

    def test_require_raises_when_failed(self, context: AgentContext) -> None:
        result = run_agent(_spec(max_attempts=1), context, FakeLLM(["broken"]))
        with pytest.raises(AgentFailedError, match="schema_discovery"):
            result.require()

    def test_correction_is_fed_back_into_prompt(self, context: AgentContext) -> None:
        llm = FakeLLM(["broken", _valid_proposal()])
        run_agent(_spec(), context, llm)
        assert "Correction required" in llm.calls[1]["prompt"]
        assert "invalid_json" in llm.calls[1]["prompt"]

    def test_semantic_failure_triggers_retry(self, context: AgentContext) -> None:
        bad = _valid_proposal()
        bad["base_table"] = "totally_unknown_table"
        llm = FakeLLM([bad, _valid_proposal()])
        result = run_agent(_spec(), context, llm)
        assert result.succeeded
        assert result.n_attempts == 2
        assert any(f.code == "unknown_table" for f in result.attempts[0].failures)

    def test_undeclared_evidence_tool_is_rejected(self, context: AgentContext) -> None:
        context.evidence_tools.append(
            ToolEvidence(tool_id="read_raw_database", summary="Should never be admitted.")
        )
        with pytest.raises(ValueError, match="undeclared tools"):
            run_agent(_spec(), context, FakeLLM([_valid_proposal()]))

    def test_panel_reports_exact_contract_agreement(self, context: AgentContext) -> None:
        alternative = _valid_proposal()
        alternative["warnings"] = ["Review key ownership."]
        spec = _spec()
        panel = run_agent_panel(
            spec,
            context,
            FakeLLM([_valid_proposal(), alternative, _valid_proposal()]),
            panel_size=3,
        )

        assert panel.succeeded
        assert panel.agreement == pytest.approx(2 / 3)
        assert panel.require().warnings == []


class TestAutoRepair:
    def test_near_miss_column_repaired_without_retry(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["base_grain"] = ["physician_i"]  # single-character typo
        llm = FakeLLM([proposal])
        result = run_agent(_spec(), context, llm)
        assert result.succeeded
        assert result.n_attempts == 1, "a repairable typo must not cost a model round-trip"
        assert result.require().base_grain == ["physician_id"]
        assert result.attempts[0].repairs

    def test_repair_marks_result_as_not_first_pass(self, context: AgentContext) -> None:
        """Repaired output still counts against the spike metric."""
        proposal = _valid_proposal()
        proposal["base_grain"] = ["physician_i"]
        result = run_agent(_spec(), context, FakeLLM([proposal]))
        assert result.succeeded
        assert not result.first_pass_valid

    def test_unrelated_name_is_not_silently_rewritten(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["base_grain"] = ["completely_different"]
        result = run_agent(_spec(max_attempts=1), context, FakeLLM([proposal]))
        assert not result.succeeded
        assert any(f.code == "unknown_column" for f in result.all_failures)

    def test_repair_disabled_surfaces_failure(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["base_grain"] = ["physician_i"]
        result = run_agent(_spec(max_attempts=1), context, FakeLLM([proposal]), auto_repair=False)
        assert not result.succeeded

    def test_suggest_name_threshold(self) -> None:
        known = ["physician_id", "specialty"]
        assert suggest_name("physician_i", known) == "physician_id"
        assert suggest_name("amount", known) is None


class TestValidators:
    def test_unknown_table_rejected(self, context: AgentContext) -> None:
        plan = IntegrationPlanProposal.model_validate({**_valid_proposal(), "base_table": "ghost"})
        failures = validate_tables_exist(plan, context)
        assert any(f.code == "unknown_table" for f in failures)

    def test_aggregation_output_is_a_valid_join_target(self, context: AgentContext) -> None:
        """Regression: the contract originally had no way to name an aggregation."""
        plan = IntegrationPlanProposal.model_validate(_valid_proposal())
        assert validate_tables_exist(plan, context) == []
        assert validate_columns_exist(plan, context) == []

    def test_aggregation_source_must_be_a_real_table(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["aggregations"][0]["source_table"] = "txn_by_physician"
        plan = IntegrationPlanProposal.model_validate(proposal)
        assert any(f.code == "unknown_table" for f in validate_tables_exist(plan, context))

    def test_unknown_column_rejected(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["joins"][0]["left_columns"] = ["nonexistent_column"]
        plan = IntegrationPlanProposal.model_validate(proposal)
        assert any(f.code == "unknown_column" for f in validate_columns_exist(plan, context))

    def test_unsupported_join_rejected(self, context: AgentContext) -> None:
        proposal = _valid_proposal()
        proposal["aggregations"] = []
        proposal["joins"] = [
            {
                "left_table": "physicians",
                "right_table": "transactions",
                "left_columns": ["specialty"],
                "right_columns": ["amount"],
                "how": "left",
                "rationale": "invented",
            }
        ]
        plan = IntegrationPlanProposal.model_validate(proposal)
        failures = validate_joins_supported_by_evidence(plan, context)
        assert any(f.code == "unsupported_join" for f in failures)

    def test_join_via_aggregation_resolves_to_source_evidence(self, context: AgentContext) -> None:
        plan = IntegrationPlanProposal.model_validate(_valid_proposal())
        assert validate_joins_supported_by_evidence(plan, context) == []

    def test_raw_fan_out_join_rejected(self, context: AgentContext) -> None:
        """The leakage-adjacent bug: joining N:1 raw multiplies base rows."""
        proposal = _valid_proposal()
        proposal["aggregations"] = []
        proposal["joins"][0]["right_table"] = "transactions"
        plan = IntegrationPlanProposal.model_validate(proposal)
        failures = validate_fan_out_is_aggregated(plan, context)
        assert any(f.code == "unaggregated_fan_out" for f in failures)

    def test_aggregated_fan_out_accepted(self, context: AgentContext) -> None:
        plan = IntegrationPlanProposal.model_validate(_valid_proposal())
        assert validate_fan_out_is_aggregated(plan, context) == []


class TestPlanConstruction:
    def test_from_proposal_attaches_evidence(self) -> None:
        proposal = IntegrationPlanProposal.model_validate(_valid_proposal())
        plan = IntegrationPlan.from_proposal(proposal, evidence=[])
        assert plan.base_table == proposal.base_table
        assert plan.evidence == []
        assert plan.summary()["n_aggregations"] == 1

    def test_derived_tables_expose_grain_and_aggregates(self) -> None:
        proposal = IntegrationPlanProposal.model_validate(_valid_proposal())
        derived = proposal.derived_tables()
        assert derived["txn_by_physician"] == ["physician_id", "total_amount"]

    def test_source_of_resolves_derived_names(self) -> None:
        proposal = IntegrationPlanProposal.model_validate(_valid_proposal())
        assert proposal.source_of("txn_by_physician") == "transactions"
        assert proposal.source_of("physicians") == "physicians"


class TestFailureFormatting:
    def test_correction_includes_codes_and_suggestions(self) -> None:
        text = format_failures(
            [
                ValidationFailure(
                    layer="semantic",
                    code="unknown_column",
                    detail="Column 'foo' does not exist.",
                    field_path="base_grain",
                    repair_suggestion="physician_id",
                )
            ]
        )
        assert "unknown_column" in text
        assert "base_grain" in text
        assert "physician_id" in text


class TestContextAssembly:
    def test_context_is_built_from_cards_not_raw_data(self, cards, relationships) -> None:
        context = build_context_with_evidence(cards, relationships)
        rendered = context.render()
        assert "Dr. Physician" not in rendered, "PII must never reach agent context"
        assert "example-clinic.test" not in rendered
        assert "physician_id" in rendered

    def test_context_fits_local_model_budget(self, cards, relationships) -> None:
        context = build_context_with_evidence(cards, relationships)
        assert context.estimated_tokens() < 4000

    def test_context_exposes_facts_validators_need(self, cards, relationships) -> None:
        context = build_context_with_evidence(cards, relationships)
        assert context.facts["measured_pairs"]
        assert context.facts["cardinality_by_pair"]
        assert "columns_by_table" in context.facts


class TestPanelAgreementMeasuresDecisions:
    """Agreement feeds the `candidate_disagreement` gate, so it must mean one
    thing: the members chose differently.

    Comparing whole contracts did not. These contracts carry free prose —
    `business_rationale` allows 800 characters, every join and aggregation
    carries a `rationale` — so three samples that pick the same tables, keys and
    join direction but word the explanation differently fingerprinted as three
    distinct answers. That reported 33% agreement for a unanimous decision and
    escalated to a human citing ambiguity that did not exist, which made the
    panel feature actively harmful to turn on.
    """

    def _reworded(self) -> dict[str, Any]:
        """Same plan; only the human-facing explanations differ."""
        proposal = _valid_proposal()
        proposal["grain_description"] = "Exactly one row for each physician."
        proposal["aggregations"][0]["rationale"] = "Roll up the many-to-one fan-out."
        proposal["joins"][0]["rationale"] = "Bring the aggregated metrics onto the base."
        return proposal

    def test_rewording_the_same_plan_is_not_disagreement(self, context: AgentContext) -> None:
        spec = build_spec()  # the real spec, which declares narration_fields
        panel = run_agent_panel(
            spec,
            context,
            FakeLLM([_valid_proposal(), self._reworded(), _valid_proposal()]),
            panel_size=3,
        )

        assert panel.succeeded
        assert panel.agreement == pytest.approx(1.0)
        # The divergence is still recorded — it is just not gated on.
        assert panel.verbatim_agreement == pytest.approx(2 / 3)

    def test_a_different_join_direction_is_disagreement(self, context: AgentContext) -> None:
        """The counter-test. An inner join drops rows a left join keeps."""
        divergent = _valid_proposal()
        divergent["joins"][0]["how"] = "inner"

        panel = run_agent_panel(
            build_spec(),
            context,
            FakeLLM([_valid_proposal(), divergent, divergent]),
            panel_size=3,
        )

        assert panel.agreement == pytest.approx(2 / 3)

    def test_a_different_base_grain_is_disagreement(self, context: AgentContext) -> None:
        a, b, c = _valid_proposal(), _valid_proposal(), _valid_proposal()
        b["base_grain"] = ["physician_id", "specialty"]
        c["base_grain"] = ["specialty"]

        panel = run_agent_panel(build_spec(), context, FakeLLM([a, b, c]), panel_size=3)

        assert panel.agreement == pytest.approx(1 / 3)

    def test_an_agent_that_declares_nothing_compares_the_whole_contract(
        self, context: AgentContext
    ) -> None:
        """The default must not silently ignore anything.

        `_spec()` builds a spec that declares no narration, so nothing is
        stripped and the comparison stays strict.
        """
        assert _spec().narration_fields == frozenset()

        panel = run_agent_panel(
            _spec(),
            context,
            FakeLLM([_valid_proposal(), self._reworded(), _valid_proposal()]),
            panel_size=3,
        )

        assert panel.agreement == pytest.approx(2 / 3)

    def test_an_invalid_member_counts_against_agreement(self, context: AgentContext) -> None:
        """A member that produced nothing valid is not a member that agreed."""
        panel = run_agent_panel(
            build_spec(),
            context,
            FakeLLM([_valid_proposal(), _valid_proposal(), "not json at all"]),
            panel_size=3,
        )

        # The third member retries within its own budget, so script exhaustion
        # keeps returning the same unparseable text and it never validates.
        assert panel.succeeded
        assert panel.agreement == pytest.approx(2 / 3)
