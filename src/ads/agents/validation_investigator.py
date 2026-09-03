"""Active tool loop for comparing realized validation strategies."""

from __future__ import annotations

import json

from pydantic_ai import Agent, ModelRetry, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from ads.agents.base import AgentContext, AgentSpec
from ads.agents.pydantic_runtime import (
    InvestigationCompletion,
    StructuredLLMFunctionModel,
)
from ads.agents.runtime import DEFAULT_INVESTIGATION_BUDGETS, InvestigationBudget
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.gates import PermissionTier
from ads.contracts.validation import ValidationInvestigationAction
from ads.llm import LARGE, StructuredLLM
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

SYSTEM_PROMPT = """\
You are a validation-strategy investigator. Compare plausible split designs by calling
trial_validation_strategy with complete proposal objects. Use other measurement tools or read-only
Python when they answer a specific uncertainty. A trial is realized evidence about feasibility,
not permission to weaken the deterministic protections inferred from entity and time signals.
Finish after testing the strongest plausible strategy. The proposal panel decides afterward; the
host then re-executes that exact final proposal and the gate rejects a failed or mismatched trial.
"""


def build_spec(*, allow_code: bool) -> AgentSpec[ValidationInvestigationAction]:
    tools = {
        "cardinality",
        "column_profile",
        "null_rate",
        "trial_validation_strategy",
        "validation_signals",
        "value_counts",
    }
    if allow_code:
        tools.add("execute_python")
    return AgentSpec(
        id="validation_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=ValidationInvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=frozenset(tools),
        max_tool_tier=PermissionTier.EXECUTE if allow_code else PermissionTier.READ_DATA,
        narration_fields=frozenset({"reason"}),
    )


def investigate_validation_context(
    *,
    context: AgentContext,
    llm: StructuredLLM,
    runtime: ToolRuntime,
    budget: InvestigationBudget | None = None,
    code_execution_enabled: bool = True,
) -> AgentAudit:
    """Add agent-selected trial evidence; failures preserve the mandatory floor."""
    budget = budget or DEFAULT_INVESTIGATION_BUDGETS["validation_investigation"]
    spec = build_spec(
        allow_code=(
            code_execution_enabled
            and runtime.backend() is not None
            and runtime.execution_available()
        )
    )
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tools: list[str] = []
    tool_attempts = 0
    failures: list[str] = []
    accepted = False
    adapter = StructuredLLMFunctionModel(
        llm=llm,
        profile=spec.profile,
        action_contract=ValidationInvestigationAction,
        max_transcript_chars=budget.max_transcript_chars,
    )

    def invoke_tool(
        tool_id: str,
        arguments: dict[str, object],
        reason: str,
    ) -> str:
        """Execute one admitted ADS tool through the permission broker."""
        del reason
        nonlocal tool_attempts
        if tool_attempts >= budget.max_tool_calls:
            if "tool_call_budget_exhausted" not in failures:
                failures.append("tool_call_budget_exhausted")
            raise ModelRetry("tool_call_budget_exhausted")
        tool_attempts += 1
        try:
            result = broker.invoke(spec, tool_id, runtime, **arguments)
        except ToolError as exc:
            failures.append(f"tool_error:{tool_id}")
            transcript.append(
                f"Turn {adapter.request_count} {tool_id} failed: {type(exc).__name__}: {exc}"
            )
            # The retry message is the only feedback the model actually sees on
            # its next turn -- the detailed transcript line above is discarded
            # unless the run later succeeds. A bare "tool_error:<id>:<type>"
            # (e.g. "tool_error:trial_validation_strategy:ToolExecutionError")
            # carries no information about *why* a proposal was rejected --
            # nothing to correct from. A model asked to fix a grouped split on
            # a column with nulls, told only that its call errored, proposes
            # the same column again. Surface the exception text so a genuinely
            # correctable mistake (bad column, missing field) can be fixed.
            raise ModelRetry(f"tool_error:{tool_id}: {exc}") from exc
        tools.append(tool_id)
        transcript.append(
            f"Turn {adapter.request_count} {tool_id}: {result.summary}; data="
            + json.dumps(result.data, default=str, separators=(",", ":"))[:8_000]
        )
        return transcript[-1]

    agent = Agent(
        adapter.model(),
        output_type=InvestigationCompletion,
        instructions=spec.system_prompt_with_tools(),
        tools=[invoke_tool],
        retries=budget.max_turns,
    )

    @agent.output_validator
    def validate_completion(output: InvestigationCompletion) -> InvestigationCompletion:
        if output.status == "finished" and "trial_validation_strategy" not in tools:
            if "finished_without_strategy_trial" not in failures:
                failures.append("finished_without_strategy_trial")
            raise ModelRetry("finished_without_strategy_trial")
        return output

    try:
        run = agent.run_sync(
            context.render(),
            usage_limits=UsageLimits(request_limit=budget.max_turns),
        )
        if run.output.status == "abandoned":
            failures.append("agent_abandoned")
        else:
            accepted = True
            context.sections["Agent-directed validation trials"] = "\n".join(transcript)[
                -budget.max_transcript_chars :
            ]
    except UsageLimitExceeded:
        failures.append("turn_budget_exhausted")
    except UnexpectedModelBehavior as exc:
        failures.append(f"investigation_error:{type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - advisory investigation boundary
        failures.append(f"investigation_error:{type(exc).__name__}")
    finally:
        runtime.close()
    failures.extend(adapter.validation_failures)
    return AgentAudit(
        stage_id="validation_investigation",
        agent_id=spec.id,
        output_contract=ValidationInvestigationAction.__name__,
        panel_size=1,
        valid_members=int(accepted),
        allowed_tools=sorted(spec.allowed_tools),
        evidence_tools=tools,
        validator_count=2,
        raw_rows_shared="execute_python" in tools,
        members=[
            AgentMemberAudit(
                member=1,
                model=adapter.model_name,
                attempts=max(adapter.request_count, 1),
                accepted=accepted,
                validation_failures=failures,
                repairs=[],
                latency_s=adapter.latency_s,
            )
        ],
    )


__all__ = ["investigate_validation_context"]
