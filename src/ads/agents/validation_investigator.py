"""Active tool loop for comparing realized validation strategies."""

from __future__ import annotations

import json

from pydantic import ValidationError

from ads.agents.base import AgentContext, AgentSpec
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.gates import PermissionTier
from ads.contracts.validation import (
    ValidationInvestigationAction,
    ValidationInvestigationActionKind,
)
from ads.llm import LARGE, StructuredLLM
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

MAX_TURNS = 8

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
    *, context: AgentContext, llm: StructuredLLM, runtime: ToolRuntime
) -> AgentAudit:
    """Add agent-selected trial evidence; failures preserve the mandatory floor."""
    spec = build_spec(
        allow_code=runtime.backend() is not None and runtime.execution_available()
    )
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tools: list[str] = []
    failures: list[str] = []
    latency = 0.0
    model = spec.profile.name
    accepted = False
    attempts = 0
    try:
        for turn in range(1, MAX_TURNS + 1):
            attempts = turn
            prompt = context.render()
            if transcript:
                prompt += "\n\n## Trial transcript\n" + "\n".join(transcript)[-20_000:]
            response = llm.generate_structured(
                system=spec.system_prompt,
                prompt=prompt,
                json_schema=ValidationInvestigationAction.model_json_schema(),
                profile=spec.profile,
            )
            latency += response.latency_s
            model = response.model
            if response.parsed is None:
                failures.append("invalid_json")
                continue
            try:
                action = ValidationInvestigationAction.model_validate(response.parsed)
            except ValidationError as exc:
                failures.append("invalid_action")
                transcript.append(f"Turn {turn} rejected: {exc}")
                continue
            if action.action is ValidationInvestigationActionKind.ABANDON:
                failures.append("agent_abandoned")
                break
            if action.action is ValidationInvestigationActionKind.FINISH:
                if "trial_validation_strategy" not in tools:
                    failures.append("finished_without_strategy_trial")
                    transcript.append("Finish rejected: trial at least one exact strategy.")
                    continue
                accepted = True
                context.sections["Agent-directed validation trials"] = "\n".join(transcript)[
                    -20_000:
                ]
                break
            assert action.tool_id is not None
            try:
                result = broker.invoke(spec, action.tool_id, runtime, **action.arguments)
            except ToolError as exc:
                failures.append(f"tool_error:{action.tool_id}")
                transcript.append(
                    f"Turn {turn} {action.tool_id} failed: {type(exc).__name__}: {exc}"
                )
                continue
            tools.append(action.tool_id)
            transcript.append(
                f"Turn {turn} {action.tool_id}: {result.summary}; data="
                + json.dumps(result.data, default=str, separators=(",", ":"))[:8_000]
            )
        else:
            failures.append("turn_budget_exhausted")
    except Exception as exc:  # noqa: BLE001 - advisory investigation boundary
        failures.append(f"investigation_error:{type(exc).__name__}")
    finally:
        runtime.close()
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
                model=model,
                attempts=max(attempts, 1),
                accepted=accepted,
                validation_failures=failures,
                repairs=[],
                latency_s=round(latency, 3),
            )
        ],
    )


__all__ = ["investigate_validation_context"]
