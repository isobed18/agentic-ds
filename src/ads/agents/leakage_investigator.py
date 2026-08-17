"""Bounded tool loop for falsifiable challenges to deterministic leakage findings."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from ads.agents.base import AgentSpec
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.contracts.leakage import (
    LeakageChallenge,
    LeakageChallengeAction,
    LeakageChallengeActionKind,
    LeakageChallengeProposal,
    LeakageReport,
    leakage_finding_fingerprint,
)
from ads.contracts.validation import ValidationStrategy
from ads.llm import LARGE, StructuredLLM
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

MAX_TURNS = 8
MAX_TRANSCRIPT_CHARS = 20_000

SYSTEM_PROMPT = """\
You are investigating whether a deterministic leakage warning has a legitimate domain
explanation. You cannot dismiss, relabel, or clear a finding. You may inspect the ABT with
registered tools and, when isolated execution is available, write exploratory Python. Results
from authored Python are never gate evidence.

The only registered challenge currently available tests a narrow claim: a suspect feature was
recorded no later than a row-level prediction time. Use it only when the dataset contains BOTH a
timestamp genuinely proposed as that feature's recording time and a prediction-time column. The
executor measures ordering and coverage. Even a successful result requires a human to confirm
that the recording timestamp is semantically tied to the feature. If those columns do not exist,
abandon honestly instead of inventing them.

Every response is one typed action: call_tool, finish, or abandon. To finish, reference the exact
challenge fingerprint returned by a registered challenge tool in this investigation.
"""


@dataclass(frozen=True)
class LeakageInvestigationResult:
    challenge: LeakageChallenge | None
    audit: AgentAudit
    degraded_reason: str | None = None


def build_spec(*, allow_code: bool) -> AgentSpec[LeakageChallengeAction]:
    tools = {
        "column_profile",
        "value_counts",
        "correlation",
        "cardinality",
        "null_rate",
        "test_recorded_before_prediction",
    }
    if allow_code:
        tools.add("execute_python")
    return AgentSpec(
        id="leakage_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=LeakageChallengeAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=frozenset(tools),
        max_tool_tier=(PermissionTier.EXECUTE if allow_code else PermissionTier.READ_DATA),
        narration_fields=frozenset({"reason"}),
    )


def _audit(
    *,
    spec: AgentSpec[LeakageChallengeAction],
    model: str,
    attempts: int,
    accepted: bool,
    tools: list[str],
    failures: list[str],
    latency_s: float,
) -> AgentAudit:
    return AgentAudit(
        stage_id="leakage_challenge",
        agent_id=spec.id,
        output_contract=LeakageChallenge.__name__,
        panel_size=1,
        valid_members=int(accepted),
        agreement=None,
        verbatim_agreement=None,
        allowed_tools=sorted(spec.allowed_tools),
        evidence_tools=tools,
        validator_count=4,
        raw_rows_shared="execute_python" in tools,
        members=[
            AgentMemberAudit(
                member=1,
                model=model,
                attempts=attempts,
                accepted=accepted,
                validation_failures=failures,
                repairs=[],
                latency_s=round(latency_s, 3),
            )
        ],
    )


def _context(
    card: DataCard,
    report: LeakageReport,
    strategy: ValidationStrategy,
    *,
    allow_code: bool,
) -> str:
    findings = [
        {
            "finding_fingerprint": leakage_finding_fingerprint(finding),
            **finding.model_dump(mode="json"),
        }
        for finding in report.findings
    ]
    columns = [
        {
            "name": column.name,
            "semantic_type": column.semantic_type.value,
            "null_rate": column.null_rate,
        }
        for column in card.columns
    ]
    tools = [
        '- column_profile: {"table":"abt","column":"..."}',
        '- value_counts: {"table":"abt","column":"..."}',
        '- correlation: {"table":"abt","left_column":"...","right_column":"..."}',
        '- cardinality: {"table":"abt","column":"..."}',
        '- null_rate: {"table":"abt","column":"..."}',
        (
            '- test_recorded_before_prediction: {"proposal": '
            "<LeakageChallengeProposal object>}"
        ),
    ]
    if allow_code:
        tools.append('- execute_python: {"code":"...","timeout":30}; ABT=/data/abt.csv')
    return (
        "## Deterministic leakage findings\n"
        + json.dumps(findings, separators=(",", ":"))
        + "\n\n## ABT columns\n"
        + json.dumps(columns, separators=(",", ":"))
        + "\n\n## Validation strategy\n"
        + json.dumps(strategy.model_dump(mode="json"), separators=(",", ":"))
        + "\n\n## Available tools\n"
        + "\n".join(tools)
        + "\n\n## Challenge proposal schema\n"
        + json.dumps(
            LeakageChallengeProposal.model_json_schema(), separators=(",", ":")
        )
    )


def investigate_leakage(
    *,
    card: DataCard,
    report: LeakageReport,
    strategy: ValidationStrategy,
    llm: StructuredLLM,
    runtime: ToolRuntime,
) -> LeakageInvestigationResult:
    """Seek one registered challenge; all model/runtime failures preserve the floor."""
    allow_code = runtime.execution_available()
    spec = build_spec(allow_code=allow_code)
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tool_calls: list[str] = []
    challenges: dict[str, LeakageChallenge] = {}
    failures: list[str] = []
    attempts = 0
    latency = 0.0
    model = spec.profile.name
    prompt_base = _context(card, report, strategy, allow_code=allow_code)

    try:
        for turn in range(1, MAX_TURNS + 1):
            attempts += 1
            history = "\n\n".join(transcript)[-MAX_TRANSCRIPT_CHARS:]
            prompt = prompt_base + (
                "\n\n## Investigation transcript\n" + history if history else ""
            )
            response = llm.generate_structured(
                system=spec.system_prompt,
                prompt=prompt,
                json_schema=LeakageChallengeAction.model_json_schema(),
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
                action = LeakageChallengeAction.model_validate(response.parsed)
            except ValidationError as exc:
                failures.append("invalid_action")
                transcript.append(f"Turn {turn} rejected: {exc}.")
                continue

            if action.action is LeakageChallengeActionKind.ABANDON:
                return LeakageInvestigationResult(
                    challenge=None,
                    audit=_audit(
                        spec=spec,
                        model=model,
                        attempts=attempts,
                        accepted=False,
                        tools=tool_calls,
                        failures=[*failures, "agent_abandoned"],
                        latency_s=latency,
                    ),
                    degraded_reason=action.reason,
                )
            if action.action is LeakageChallengeActionKind.CALL_TOOL:
                assert action.tool_id is not None
                try:
                    result = broker.invoke(spec, action.tool_id, runtime, **action.arguments)
                except ToolError as exc:
                    failures.append(f"tool_error:{action.tool_id}")
                    transcript.append(
                        f"Turn {turn} tool {action.tool_id} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                tool_calls.append(action.tool_id)
                data = dict(result.data)
                challenge_fingerprint = data.pop("challenge_fingerprint", None)
                if action.tool_id == "test_recorded_before_prediction":
                    if not isinstance(challenge_fingerprint, str):
                        failures.append("missing_challenge_fingerprint")
                    else:
                        challenges[challenge_fingerprint] = LeakageChallenge.model_validate(
                            data
                        )
                transcript.append(
                    f"Turn {turn} tool {action.tool_id} result: {result.summary}; "
                    f"data={json.dumps(result.data, default=str, separators=(',', ':'))[:8000]}"
                )
                continue

            assert action.action is LeakageChallengeActionKind.FINISH
            assert action.challenge_fingerprint is not None
            challenge = challenges.get(action.challenge_fingerprint)
            if challenge is None:
                failures.append("unexecuted_challenge")
                transcript.append(
                    f"Turn {turn} rejected: that exact challenge was not executed "
                    "by a registered tool in this investigation."
                )
                continue
            return LeakageInvestigationResult(
                challenge=challenge,
                audit=_audit(
                    spec=spec,
                    model=model,
                    attempts=attempts,
                    accepted=True,
                    tools=tool_calls,
                    failures=failures,
                    latency_s=latency,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - advisory challenge cannot weaken the floor
        failures.append(f"investigation_error:{type(exc).__name__}")
        reason = f"Leakage challenge failed: {type(exc).__name__}: {exc}"[:500]
        return LeakageInvestigationResult(
            challenge=None,
            audit=_audit(
                spec=spec,
                model=model,
                attempts=attempts,
                accepted=False,
                tools=tool_calls,
                failures=failures,
                latency_s=latency,
            ),
            degraded_reason=reason,
        )
    finally:
        runtime.close()

    failures.append("turn_budget_exhausted")
    return LeakageInvestigationResult(
        challenge=None,
        audit=_audit(
            spec=spec,
            model=model,
            attempts=attempts,
            accepted=False,
            tools=tool_calls,
            failures=failures,
            latency_s=latency,
        ),
        degraded_reason=f"Leakage investigation exhausted {MAX_TURNS} turns.",
    )


__all__ = [
    "LeakageInvestigationResult",
    "build_spec",
    "investigate_leakage",
]
