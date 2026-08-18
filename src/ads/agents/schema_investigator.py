"""Active, tool-driven schema discovery with deterministic plan trials."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from ads.agents.base import AgentContext, AgentSpec, ValidationFailure
from ads.agents.runtime import DEFAULT_INVESTIGATION_BUDGETS, InvestigationBudget
from ads.agents.schema_discovery import build_spec as build_plan_spec
from ads.contracts.gates import PermissionTier
from ads.contracts.integration import (
    IntegrationPlanProposal,
    IntegrationTrial,
    SchemaActionKind,
    SchemaInvestigationAction,
    integration_plan_fingerprint,
)
from ads.llm import LARGE, StructuredLLM
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

MAX_TURNS = DEFAULT_INVESTIGATION_BUDGETS["schema_investigation"].max_turns

SYSTEM_PROMPT = """\
You are actively investigating how to integrate several messy tables into one analytical base
table. Do not fill a plan from first impressions. Use the available deterministic tools to check
keys, cardinality and joins. When isolated execution is available you may also write exploratory
Python against the read-only table copies, but numbers produced by your code are exploratory and
do not support the final plan.

The final plan must be tested with trial_integration_plan. That tool runs the proposed operations
through the deterministic DuckDB engine and measures the realized grain. Submit only the exact
executable plan semantics that received a successful trial. Changing a join, aggregation, base
table, base grain, or join type after the trial requires another trial.

Every response is one typed action: call_tool, submit_plan, or abandon. Tool failures and plan
validation failures are returned in the transcript so you can correct them. Never claim that
agent-authored code proves a join or grain property; only host-created tool measurements do.
"""


@dataclass(frozen=True)
class SchemaMemberResult:
    proposal: IntegrationPlanProposal | None
    trial: IntegrationTrial | None
    attempts: int
    model: str
    latency_s: float
    tool_calls: tuple[str, ...]
    failures: tuple[ValidationFailure, ...]
    abandoned_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.proposal is not None and self.trial is not None

    @property
    def raw_rows_shared(self) -> bool:
        return "execute_python" in self.tool_calls

    @property
    def decision_fingerprint(self) -> str | None:
        return integration_plan_fingerprint(self.proposal) if self.proposal is not None else None

    @property
    def verbatim_fingerprint(self) -> str | None:
        if self.proposal is None:
            return None
        payload = json.dumps(
            self.proposal.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def build_action_spec(*, allow_code: bool) -> AgentSpec[SchemaInvestigationAction]:
    tools = {
        "candidate_keys",
        "cardinality",
        "column_profile",
        "join_overlap",
        "trial_integration_plan",
    }
    if allow_code:
        tools.add("execute_python")
    return AgentSpec(
        id="schema_discovery",
        system_prompt=SYSTEM_PROMPT,
        output_contract=SchemaInvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=frozenset(tools),
        max_tool_tier=(PermissionTier.EXECUTE if allow_code else PermissionTier.READ_DATA),
        narration_fields=frozenset({"reason"}),
    )


def _tool_help(data_paths: dict[str, str]) -> str:
    lines = [
        '- candidate_keys: {"table":"..."}',
        '- cardinality: {"table":"...","column":"..."}',
        '- column_profile: {"table":"...","column":"..."}',
        (
            '- join_overlap: {"from_table":"...","from_column":"...",'
            '"to_table":"...","to_column":"..."}'
        ),
        '- trial_integration_plan: {"plan": <IntegrationPlanProposal object>}',
    ]
    if data_paths:
        lines.extend(
            [
                '- execute_python: {"code":"...","timeout":30}',
                "  Read-only table copies: "
                + json.dumps(data_paths, sort_keys=True, separators=(",", ":")),
            ]
        )
    else:
        lines.append(
            "- execute_python is unavailable because no isolated backend is available; "
            "continue with deterministic tools."
        )
    return "\n".join(lines)


def _render_failures(failures: list[ValidationFailure]) -> str:
    return "; ".join(
        f"{failure.code}"
        + (f" at {failure.field_path}" if failure.field_path else "")
        + f": {failure.detail}"
        for failure in failures
    )


def investigate_schema(
    *,
    context: AgentContext,
    llm: StructuredLLM,
    runtime: ToolRuntime,
    data_paths: dict[str, str] | None = None,
    budget: InvestigationBudget | None = None,
    code_execution_enabled: bool = True,
) -> SchemaMemberResult:
    """Run one bounded investigator member and require an exact successful trial."""
    budget = budget or DEFAULT_INVESTIGATION_BUDGETS["schema_investigation"]
    data_paths = data_paths or {}
    allow_code = code_execution_enabled and bool(data_paths) and runtime.execution_available()
    spec = build_action_spec(allow_code=allow_code)
    plan_spec = build_plan_spec()
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tool_calls: list[str] = []
    tool_attempts = 0
    failures: list[ValidationFailure] = []
    trials: dict[str, IntegrationTrial] = {}
    model = spec.profile.name
    latency = 0.0
    attempts = 0
    base_prompt = (
        context.render()
        + "\n\n## Available tools\n"
        + _tool_help(data_paths if allow_code else {})
        + "\n\n## Final plan schema\n"
        + json.dumps(
            IntegrationPlanProposal.model_json_schema(),
            separators=(",", ":"),
        )
    )

    try:
        for turn in range(1, budget.max_turns + 1):
            attempts += 1
            history = "\n\n".join(transcript)[-budget.max_transcript_chars :]
            prompt = base_prompt + (
                "\n\n## Investigation transcript\n" + history if history else ""
            )
            response = llm.generate_structured(
                system=spec.system_prompt_with_tools(),
                prompt=prompt,
                json_schema=SchemaInvestigationAction.model_json_schema(),
                profile=spec.profile,
            )
            latency += response.latency_s
            model = response.model
            if response.parsed is None:
                failure = ValidationFailure(
                    layer="schema",
                    code="invalid_json",
                    detail=response.parse_error or "Response was not valid JSON.",
                )
                failures.append(failure)
                transcript.append(f"Turn {turn} rejected: {_render_failures([failure])}")
                continue
            try:
                action = SchemaInvestigationAction.model_validate(response.parsed)
            except ValidationError as exc:
                failure = ValidationFailure(
                    layer="schema",
                    code="invalid_action",
                    detail=str(exc),
                )
                failures.append(failure)
                transcript.append(f"Turn {turn} rejected: {_render_failures([failure])}")
                continue

            if action.action is SchemaActionKind.ABANDON:
                return SchemaMemberResult(
                    proposal=None,
                    trial=None,
                    attempts=attempts,
                    model=model,
                    latency_s=latency,
                    tool_calls=tuple(tool_calls),
                    failures=tuple(failures),
                    abandoned_reason=action.reason,
                )

            if action.action is SchemaActionKind.CALL_TOOL:
                assert action.tool_id is not None
                if tool_attempts >= budget.max_tool_calls:
                    failures.append(
                        ValidationFailure(
                            layer="tool",
                            code="tool_call_budget_exhausted",
                            detail="Configured tool call budget is exhausted.",
                        )
                    )
                    continue
                tool_attempts += 1
                try:
                    result = broker.invoke(
                        spec,
                        action.tool_id,
                        runtime,
                        **action.arguments,
                    )
                except ToolError as exc:
                    failure = ValidationFailure(
                        layer="tool",
                        code=f"tool_error:{action.tool_id}",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                    failures.append(failure)
                    transcript.append(f"Turn {turn} tool {action.tool_id} failed: {failure.detail}")
                    continue
                tool_calls.append(action.tool_id)
                context.admit_tool_result(
                    result.tool_id,
                    result.summary,
                    tier=result.tier,
                )
                rendered = json.dumps(result.data, default=str, separators=(",", ":"))
                transcript.append(
                    f"Turn {turn} tool {action.tool_id} result: {result.summary}; "
                    f"data={rendered[:10_000]}"
                )
                if action.tool_id == "trial_integration_plan":
                    trial = IntegrationTrial.model_validate(result.data)
                    trials[trial.plan_fingerprint] = trial
                continue

            assert action.action is SchemaActionKind.SUBMIT_PLAN
            assert action.plan is not None
            plan_failures: list[ValidationFailure] = []
            for validator in plan_spec.validators:
                plan_failures.extend(validator(action.plan, context))
            fingerprint = integration_plan_fingerprint(action.plan)
            trial = trials.get(fingerprint)
            if trial is None:
                plan_failures.append(
                    ValidationFailure(
                        layer="evidence",
                        code="untested_plan",
                        detail=(
                            "The submitted executable plan was not run successfully through "
                            "trial_integration_plan in this investigation."
                        ),
                    )
                )
            elif not trial.grain_preserved:
                plan_failures.append(
                    ValidationFailure(
                        layer="evidence",
                        code="trial_grain_not_preserved",
                        detail=(
                            "The deterministic trial found a non-clean or non-preserved grain: "
                            f"base duplicates={trial.base_duplicate_grain_rows}, "
                            f"result duplicates={trial.result_duplicate_grain_rows}, "
                            f"base nulls={trial.base_null_grain_rows}, "
                            f"result nulls={trial.result_null_grain_rows}."
                        ),
                    )
                )
            if plan_failures:
                failures.extend(plan_failures)
                transcript.append(f"Turn {turn} plan rejected: {_render_failures(plan_failures)}")
                continue
            return SchemaMemberResult(
                proposal=action.plan,
                trial=trial,
                attempts=attempts,
                model=model,
                latency_s=latency,
                tool_calls=tuple(tool_calls),
                failures=tuple(failures),
            )
    except Exception as exc:  # noqa: BLE001 - convert runtime failures into stage evidence
        failures.append(
            ValidationFailure(
                layer="runtime",
                code=f"investigation_error:{type(exc).__name__}",
                detail=str(exc)[:500],
            )
        )
    finally:
        runtime.close()

    if attempts >= budget.max_turns:
        failures.append(
            ValidationFailure(
                layer="budget",
                code="turn_budget_exhausted",
                detail=f"Schema investigation exhausted {budget.max_turns} turns.",
            )
        )
    return SchemaMemberResult(
        proposal=None,
        trial=None,
        attempts=attempts,
        model=model,
        latency_s=latency,
        tool_calls=tuple(tool_calls),
        failures=tuple(failures),
    )


__all__ = [
    "MAX_TURNS",
    "SYSTEM_PROMPT",
    "SchemaMemberResult",
    "build_action_spec",
    "investigate_schema",
]
