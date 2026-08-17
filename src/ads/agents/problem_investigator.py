"""Active, bounded investigation before the typed problem proposal panel."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from ads.agents.base import AgentContext, AgentSpec
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.gates import PermissionTier
from ads.contracts.problem import (
    ProblemInvestigationAction,
    ProblemInvestigationActionKind,
)
from ads.llm import LARGE, StructuredLLM
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

MAX_TURNS = 8
MAX_TRANSCRIPT_CHARS = 20_000

SYSTEM_PROMPT = """\
You are the investigative scout for problem discovery. Do not choose the final problem yet.
Use the registered tools to examine plausible targets, class support, missingness, cardinality,
and relationships that matter to the user's intent. When useful, execute read-only Python against
/data/abt.csv for an analysis not covered by the registered tools. Source data is immutable.

Finish only when the subsequent proposal panel has enough measured context to compare plausible
problems. Tool output can guide judgment but cannot establish feasibility: the host recomputes
support for every submitted target, and the critical gate still belongs to deterministic policy
and the human. Abandon honestly if investigation cannot proceed.
"""


@dataclass(frozen=True)
class ProblemInvestigationResult:
    audit: AgentAudit
    degraded_reason: str | None = None


def build_spec(*, allow_code: bool) -> AgentSpec[ProblemInvestigationAction]:
    tools = {"cardinality", "column_profile", "correlation", "null_rate", "value_counts"}
    if allow_code:
        tools.add("execute_python")
    return AgentSpec(
        id="problem_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=ProblemInvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=frozenset(tools),
        max_tool_tier=PermissionTier.EXECUTE if allow_code else PermissionTier.READ_DATA,
        narration_fields=frozenset({"reason"}),
    )


def _audit(
    spec: AgentSpec[ProblemInvestigationAction],
    *,
    model: str,
    attempts: int,
    accepted: bool,
    tools: list[str],
    failures: list[str],
    latency: float,
) -> AgentAudit:
    return AgentAudit(
        stage_id="problem_investigation",
        agent_id=spec.id,
        output_contract=ProblemInvestigationAction.__name__,
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
                attempts=attempts,
                accepted=accepted,
                validation_failures=failures,
                repairs=[],
                latency_s=round(latency, 3),
            )
        ],
    )


def investigate_problem_context(
    *,
    context: AgentContext,
    llm: StructuredLLM,
    runtime: ToolRuntime,
) -> ProblemInvestigationResult:
    """Let the scout choose tool calls; failures leave deterministic context intact."""
    allow_code = runtime.backend() is not None and runtime.execution_available()
    spec = build_spec(allow_code=allow_code)
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tools: list[str] = []
    failures: list[str] = []
    model = spec.profile.name
    latency = 0.0
    try:
        for turn in range(1, MAX_TURNS + 1):
            prompt = context.render()
            if transcript:
                prompt += "\n\n## Investigation transcript\n" + "\n".join(transcript)[
                    -MAX_TRANSCRIPT_CHARS:
                ]
            response = llm.generate_structured(
                system=spec.system_prompt,
                prompt=prompt,
                json_schema=ProblemInvestigationAction.model_json_schema(),
                profile=spec.profile,
            )
            latency += response.latency_s
            model = response.model
            if response.parsed is None:
                failures.append("invalid_json")
                transcript.append(
                    f"Turn {turn} rejected: {response.parse_error or 'invalid JSON'}."
                )
                continue
            try:
                action = ProblemInvestigationAction.model_validate(response.parsed)
            except ValidationError as exc:
                failures.append("invalid_action")
                transcript.append(f"Turn {turn} rejected: {exc}.")
                continue
            if action.action is ProblemInvestigationActionKind.ABANDON:
                return ProblemInvestigationResult(
                    audit=_audit(
                        spec,
                        model=model,
                        attempts=turn,
                        accepted=False,
                        tools=tools,
                        failures=[*failures, "agent_abandoned"],
                        latency=latency,
                    ),
                    degraded_reason=action.reason,
                )
            if action.action is ProblemInvestigationActionKind.FINISH:
                if not tools:
                    failures.append("finished_without_tool_evidence")
                    transcript.append("Finish rejected: call at least one measurement tool.")
                    continue
                context.sections["Agent-directed investigation"] = "\n".join(transcript)[
                    -MAX_TRANSCRIPT_CHARS:
                ]
                return ProblemInvestigationResult(
                    audit=_audit(
                        spec,
                        model=model,
                        attempts=turn,
                        accepted=True,
                        tools=tools,
                        failures=failures,
                        latency=latency,
                    )
                )
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
            context.admit_tool_result(result.tool_id, result.summary, tier=result.tier)
            transcript.append(
                f"Turn {turn} {action.tool_id}: {result.summary}; data="
                + json.dumps(result.data, default=str, separators=(",", ":"))[:8_000]
            )
        return ProblemInvestigationResult(
            audit=_audit(
                spec,
                model=model,
                attempts=MAX_TURNS,
                accepted=False,
                tools=tools,
                failures=[*failures, "turn_budget_exhausted"],
                latency=latency,
            ),
            degraded_reason="turn_budget_exhausted",
        )
    except Exception as exc:  # noqa: BLE001 - scout is advisory to the critical floor
        return ProblemInvestigationResult(
            audit=_audit(
                spec,
                model=model,
                attempts=max(1, len(transcript)),
                accepted=False,
                tools=tools,
                failures=[*failures, f"investigation_error:{type(exc).__name__}"],
                latency=latency,
            ),
            degraded_reason=f"{type(exc).__name__}: {exc}",
        )
    finally:
        runtime.close()


__all__ = ["ProblemInvestigationResult", "investigate_problem_context"]
