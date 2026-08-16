"""Agent execution: typed invocation, layered validation, corrective retry.

An agent here is not a prompt. It is a bundle — input/output contracts, a model
profile, a closed toolset, a context policy and a set of validators — invoked as
a typed function. That is what makes agents unit-testable against fixtures
rather than only evaluable by reading their output.

Validation runs in the four layers from the architecture report:

1. **Schema** — Pydantic. Rare, because decoding is grammar-constrained.
2. **Semantic** — hand-written per contract. Catches the failures that actually
   happen: a referenced column that does not exist, a task type inconsistent
   with the target's dtype.
3. **Executable** — smoke-run generated code (added with the code-writing agents).
4. **Consistency** — cross-artifact agreement.

Layers 2 and 4 are frequently **auto-repairable**: a hallucinated column name
that fuzzy-matches a real one is corrected deterministically with an audit note,
which avoids a full retry. On local models that saves real wall-clock time.
"""

from __future__ import annotations

import difflib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ads.contracts.gates import PermissionTier
from ads.llm.client import LLMResponse, ModelProfile, StructuredLLM

#: Similarity above which a hallucinated name is auto-corrected to a real one.
#: Tuned high on purpose: silently rewriting a genuinely different name would be
#: worse than escalating.
AUTO_REPAIR_THRESHOLD = 0.85


@dataclass(frozen=True)
class ValidationFailure:
    layer: str
    code: str
    detail: str
    field_path: str | None = None
    repair_suggestion: str | None = None

    @property
    def repairable(self) -> bool:
        return self.repair_suggestion is not None


@dataclass
class AgentContext:
    """Deterministically assembled input for one agent invocation.

    Built by projecting run state through the agent's ContextPolicy — never
    accumulated from history. Same run state plus same spec always yields the
    same context, which is what makes agent behaviour reproducible.
    """

    sections: dict[str, str] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    evidence_tools: list[ToolEvidence] = field(default_factory=list)
    """Structured values validators can check against (e.g. known column names)."""

    def render(self) -> str:
        sections = [f"## {title}\n{body}" for title, body in self.sections.items()]
        if self.evidence_tools:
            measured = "\n".join(
                f"- {evidence.tool_id}: {evidence.summary}"
                for evidence in self.evidence_tools
            )
            sections.append(f"## Measured tool evidence\n{measured}")
        return "\n\n".join(sections)

    def admit_tool_result(
        self,
        tool_id: str,
        summary: str,
        *,
        tier: PermissionTier | None = None,
    ) -> None:
        """Project only a broker-returned row-free summary into model context."""
        self.evidence_tools.append(ToolEvidence(tool_id=tool_id, summary=summary, tier=tier))

    def estimated_tokens(self) -> int:
        return len(self.render()) // 4


@dataclass(frozen=True)
class ToolEvidence:
    """A deterministic tool result admitted to an agent's row-free context."""

    tool_id: str
    summary: str
    tier: PermissionTier | None = None


#: A validator inspects a parsed output against the context and reports failures.
Validator = Callable[[Any, AgentContext], list[ValidationFailure]]
EvidenceProvider = Callable[[int, AgentContext], Sequence[ToolEvidence]]


@dataclass
class AgentAttempt:
    """One trip to the model, kept for audit and for gate quality signals."""

    attempt: int
    response: LLMResponse
    failures: list[ValidationFailure] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    accepted: bool = False


@dataclass
class AgentResult[TOut: BaseModel]:
    """Outcome of running an agent, successful or not."""

    agent_id: str
    output: TOut | None
    attempts: list[AgentAttempt]
    succeeded: bool

    @property
    def n_attempts(self) -> int:
        return len(self.attempts)

    @property
    def total_latency_s(self) -> float:
        return round(sum(a.response.latency_s for a in self.attempts), 3)

    @property
    def first_pass_valid(self) -> bool:
        """Whether attempt 1 was accepted with no repairs — the key spike metric."""
        return bool(self.attempts) and self.attempts[0].accepted and not self.attempts[0].repairs

    @property
    def all_failures(self) -> list[ValidationFailure]:
        return [f for a in self.attempts for f in a.failures]

    def require(self) -> TOut:
        if self.output is None:
            failures = "; ".join(f"{f.code}: {f.detail}" for f in self.all_failures) or "unknown"
            raise AgentFailedError(f"Agent {self.agent_id!r} failed after "
                                   f"{self.n_attempts} attempt(s): {failures}")
        return self.output


@dataclass
class AgentPanelResult[TOut: BaseModel]:
    """Independent typed proposals plus their deterministic majority agreement."""

    agent_id: str
    members: list[AgentResult[TOut]]
    output: TOut | None
    agreement: float
    """Fraction of the panel that reached the same decision. Feeds the gate."""
    verbatim_agreement: float = 0.0
    """Fraction that produced byte-identical contracts, prose included.

    Recorded but never gated on. When it sits far below ``agreement`` the panel
    agreed on the decision and differed only in wording, which is worth seeing
    in the audit and is not a reason to stop a run.
    """

    @property
    def succeeded(self) -> bool:
        return self.output is not None

    @property
    def all_failures(self) -> list[ValidationFailure]:
        return [failure for member in self.members for failure in member.all_failures]

    def require(self) -> TOut:
        if self.output is None:
            failures = "; ".join(f"{f.code}: {f.detail}" for f in self.all_failures)
            raise AgentFailedError(
                f"Agent panel {self.agent_id!r} produced no valid contract: "
                f"{failures or 'unknown'}"
            )
        return self.output


class AgentFailedError(RuntimeError):
    """Raised when an agent exhausts its retry budget without a valid output."""


@dataclass(frozen=True)
class AgentSpec[TOut: BaseModel]:
    """The full definition of a specialized agent."""

    id: str
    system_prompt: str
    output_contract: type[TOut]
    profile: ModelProfile
    validators: Sequence[Validator] = ()
    max_attempts: int = 3
    rubric: str | None = None
    column_fields: frozenset[str] = frozenset()
    """Field names whose string values are column references.

    Declared explicitly rather than inferred from the name: guessing by suffix
    both misses real fields (``base_grain``, ``group_by``) and risks rewriting
    free-text fields like ``rationale``, which would be far worse than a failed
    repair.
    """
    allowed_tools: frozenset[str] = frozenset()
    """Closed set of deterministic evidence tools allowed into this agent's context."""
    max_tool_tier: PermissionTier = PermissionTier.READ_META
    """Highest permission tier this agent may exercise through the broker."""
    narration_fields: frozenset[str] = frozenset()
    """Field names that explain a decision rather than being one.

    Panel agreement is the measured input to the ``candidate_disagreement``
    gate rule, so it has to mean "the members chose differently" and nothing
    else. Comparing whole contracts does not: these contracts carry free prose
    (``business_rationale`` allows 800 characters), and three samples that pick
    the same target, task and metric but word the rationale differently
    fingerprint as three distinct answers. That reports 33% agreement for a
    unanimous decision and escalates to a human citing ambiguity that does not
    exist.

    Narration is named rather than decisions for two reasons. Prose fields are
    few and stable while decision fields are many, and — more importantly — the
    failure modes are not symmetric. Forgetting to list a prose field means the
    panel escalates when it need not, which costs a human a look. Forgetting to
    list a decision field would mean a real disagreement is silently ignored,
    which is the failure this signal exists to prevent.

    Names match at any depth, so ``rationale`` covers the one on every join and
    aggregation step without naming each path.
    """

    def build_prompt(self, context: AgentContext, correction: str | None = None) -> str:
        parts = [context.render()]
        if correction:
            parts.append(f"## Correction required\n{correction}")
        return "\n\n".join(parts)


def suggest_name(candidate: str, known: Sequence[str]) -> str | None:
    """Fuzzy-match a possibly-hallucinated name to a known one."""
    matches = difflib.get_close_matches(candidate, known, n=1, cutoff=AUTO_REPAIR_THRESHOLD)
    return matches[0] if matches else None


def format_failures(failures: Sequence[ValidationFailure]) -> str:
    """Render failures as an imperative correction the model can act on.

    Deliberately terse and specific. Handing a local model a wall of prose about
    what went wrong reliably produces a differently-wrong answer.
    """
    lines = ["Your previous response was rejected. Fix exactly these problems:"]
    for i, failure in enumerate(failures, 1):
        location = f" (field: {failure.field_path})" if failure.field_path else ""
        lines.append(f"{i}. [{failure.code}]{location} {failure.detail}")
        if failure.repair_suggestion:
            lines.append(f"   Use instead: {failure.repair_suggestion}")
    lines.append("Return the corrected object. Do not repeat the rejected values.")
    return "\n".join(lines)


def run_agent[TOut: BaseModel](
    spec: AgentSpec[TOut],
    context: AgentContext,
    llm: StructuredLLM,
    *,
    auto_repair: bool = True,
    evidence_provider: EvidenceProvider | None = None,
) -> AgentResult[TOut]:
    """Invoke an agent with layered validation and corrective retries."""
    _validate_tool_evidence(spec, context)
    schema = spec.output_contract.model_json_schema()
    attempts: list[AgentAttempt] = []
    correction: str | None = None

    for attempt_no in range(1, spec.max_attempts + 1):
        if evidence_provider is not None:
            context.evidence_tools.extend(evidence_provider(attempt_no, context))
            _validate_tool_evidence(spec, context)
        response = llm.generate_structured(
            system=spec.system_prompt,
            prompt=spec.build_prompt(context, correction),
            json_schema=schema,
            profile=spec.profile,
        )
        attempt = AgentAttempt(attempt=attempt_no, response=response)
        attempts.append(attempt)

        # Layer 1 — schema.
        if response.parsed is None:
            attempt.failures.append(
                ValidationFailure(
                    layer="schema",
                    code="invalid_json",
                    detail=response.parse_error or "Response was not valid JSON.",
                )
            )
            correction = format_failures(attempt.failures)
            continue

        payload = dict(response.parsed)
        if auto_repair:
            payload, repairs = _repair_payload(payload, spec, context)
            attempt.repairs.extend(repairs)

        try:
            output = spec.output_contract.model_validate(payload)
        except ValidationError as exc:
            attempt.failures.extend(_from_pydantic_error(exc))
            correction = format_failures(attempt.failures)
            continue

        # Layers 2 and 4 — semantic and consistency.
        for validator in spec.validators:
            attempt.failures.extend(validator(output, context))

        if not attempt.failures:
            attempt.accepted = True
            return AgentResult(spec.id, output, attempts, succeeded=True)

        correction = format_failures(attempt.failures)

    return AgentResult(spec.id, None, attempts, succeeded=False)


def require_tool_evidence(*tool_ids: str) -> Validator:
    """Build a validator that rejects claims lacking named measured evidence."""
    required = frozenset(tool_ids)
    if not required:
        raise ValueError("At least one tool id is required.")

    def validate(_output: Any, context: AgentContext) -> list[ValidationFailure]:
        supplied = {evidence.tool_id for evidence in context.evidence_tools}
        missing = sorted(required - supplied)
        if not missing:
            return []
        return [
            ValidationFailure(
                layer="evidence",
                code="missing_tool_evidence",
                detail=f"Claim requires measured tool evidence from: {missing}.",
            )
        ]

    return validate


def _validate_tool_evidence[TOut: BaseModel](
    spec: AgentSpec[TOut], context: AgentContext
) -> None:
    supplied_tools = {evidence.tool_id for evidence in context.evidence_tools}
    undeclared = supplied_tools - spec.allowed_tools
    if undeclared:
        raise ValueError(
            f"Agent {spec.id!r} received evidence from undeclared tools: {sorted(undeclared)}"
        )
    excessive = sorted(
        evidence.tool_id
        for evidence in context.evidence_tools
        if evidence.tier is not None and evidence.tier > spec.max_tool_tier
    )
    if excessive:
        raise ValueError(
            f"Agent {spec.id!r} received evidence above its permission tier: {excessive}"
        )


def run_agent_panel[TOut: BaseModel](
    spec: AgentSpec[TOut],
    context: AgentContext,
    llm: StructuredLLM,
    *,
    panel_size: int = 1,
    auto_repair: bool = True,
) -> AgentPanelResult[TOut]:
    """Run independent panel members and select the majority decision.

    Panel members share only the same deterministic context. They do not see one
    another's prose. Agreement is the fraction of the whole panel that reached
    the most common decision, projected through ``spec.decision_fields``, so
    invalid or divergent proposals stay visible to the gate through
    ``self_consistency_agreement`` while identical decisions worded differently
    do not read as disagreement.

    The denominator is the whole panel, not the valid members: a member that
    failed validation is a member that did not agree.
    """
    if not 1 <= panel_size <= 5:
        raise ValueError("panel_size must be between 1 and 5")
    members = [
        run_agent(spec, context, llm, auto_repair=auto_repair)
        for _ in range(panel_size)
    ]
    valid = [member.output for member in members if member.output is not None]
    if not valid:
        return AgentPanelResult(spec.id, members, None, 0.0, 0.0)

    decisions = [_decision_fingerprint(output, spec.narration_fields) for output in valid]
    verbatim = [_decision_fingerprint(output, frozenset()) for output in valid]

    winner_decision, winner_count = Counter(decisions).most_common(1)[0]
    # Report the winning decision using a member that actually holds it.
    winner = valid[decisions.index(winner_decision)]
    _, verbatim_count = Counter(verbatim).most_common(1)[0]

    return AgentPanelResult(
        spec.id,
        members,
        winner,
        round(winner_count / panel_size, 6),
        round(verbatim_count / panel_size, 6),
    )


def _decision_fingerprint(output: BaseModel, narration_fields: frozenset[str]) -> str:
    """Canonical string for one proposal with its narration stripped.

    Stripping is applied at every depth rather than only to top-level keys. The
    prose in these contracts sits beside the decisions it explains — every join
    and aggregation step carries its own ``rationale`` — so a top-level-only
    filter would leave all of it in the fingerprint.
    """
    payload = _strip(output.model_dump(mode="json"), narration_fields)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _strip(node: Any, drop: frozenset[str]) -> Any:
    """Remove named keys at any depth, keeping everything else.

    Dropping by name rather than by path is deliberate. A path-qualified
    declaration would have to name ``joins.rationale`` and
    ``aggregations.rationale`` separately, and would silently stop working when
    a contract gains a new nesting level.
    """
    if isinstance(node, dict):
        return {k: _strip(v, drop) for k, v in node.items() if k not in drop}
    if isinstance(node, list):
        return [_strip(item, drop) for item in node]
    return node


def _from_pydantic_error(exc: ValidationError) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err["loc"]) or None
        failures.append(
            ValidationFailure(
                layer="schema",
                code=err["type"],
                detail=err["msg"],
                field_path=path,
            )
        )
    return failures


def _repair_payload(
    payload: dict[str, Any], spec: AgentSpec[Any], context: AgentContext
) -> tuple[dict[str, Any], list[str]]:
    """Deterministically fix near-miss column names before validating.

    Only touches string values under fields the spec declares as column
    references, and only when the fuzzy match is strong. Anything ambiguous is
    left alone so it surfaces as a validation failure instead.
    """
    known: Sequence[str] = context.facts.get("known_columns", ())
    if not known or not spec.column_fields:
        return payload, []

    repairs: list[str] = []

    def fix(value: Any, key: str) -> Any:
        if isinstance(value, str) and key in spec.column_fields:
            if value in known:
                return value
            suggestion = suggest_name(value, known)
            if suggestion:
                repairs.append(f"{key}: {value!r} -> {suggestion!r}")
                return suggestion
        return value

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item, key) for item in node]
        return fix(node, key)

    repaired = walk(payload)
    return (repaired if isinstance(repaired, dict) else payload), repairs


__all__ = [
    "AUTO_REPAIR_THRESHOLD",
    "AgentAttempt",
    "AgentContext",
    "AgentFailedError",
    "AgentPanelResult",
    "AgentResult",
    "AgentSpec",
    "EvidenceProvider",
    "ToolEvidence",
    "ValidationFailure",
    "Validator",
    "format_failures",
    "require_tool_evidence",
    "run_agent",
    "run_agent_panel",
    "suggest_name",
]
