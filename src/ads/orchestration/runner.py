"""The Orchestrator: execute a WorkflowSpec with gating and corrective retries.

This is the loop the architecture report describes in §7, and its shape is the
whole argument:

    run stage -> critique (LLM, evidence) -> gate (deterministic, verdict)
      -> AUTO_PROCEED : follow the proceed edge
      -> RETRY        : re-run the same stage with a targeted correction
      -> ESCALATE     : stop and hand a typed question to the human
      -> ABORT        : stop

The separation that matters: **critique produces evidence, the gate produces the
verdict.** The LLM is a sensor. It is never asked whether to consult the human,
and it never sees the rules. That is what makes every stop attributable to a
named rule rather than to a prompt.

Nothing here imports a graph library. Stages are plain callables and the loop is
an explicit state machine, so this module is the seam that keeps LangGraph (or
any successor) a swappable execution detail rather than the architecture.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from ads.contracts.base import Artifact
from ads.contracts.gates import CritiqueResult, GateDecision, GateVerdict, QualitySignals
from ads.gates import GatePolicy, StageHistory, evaluate_gate
from ads.llm import SeededStructuredLLM, StructuredLLM, agent_seed_scope
from ads.orchestration.critic import CritiqueContext, RubricRegistry, critique_stage
from ads.orchestration.spec import ComponentRegistry, EdgeCondition, WorkflowSpec
from ads.orchestration.state import RunState, StageAttempt

_VERDICT_TO_EDGE = {
    GateVerdict.AUTO_PROCEED: EdgeCondition.ON_PROCEED,
    GateVerdict.RETRY: EdgeCondition.ON_RETRY,
    GateVerdict.ESCALATE: EdgeCondition.ON_ESCALATE,
}


@dataclass
class StageResult:
    """What a stage hands back. Artifacts plus the measurements the gate reads."""

    artifacts: list[Artifact] = field(default_factory=list)
    signals: QualitySignals = field(default_factory=QualitySignals)
    critique: CritiqueResult | None = None
    names: dict[int, str] = field(default_factory=dict)
    """Optional logical name per artifact index, for named store lookups."""
    digest: str | None = None
    """Compact text rendering of the output, for the Orchestrator's critic.

    The critic reads this rather than the artifacts directly, for the same reason
    agents read DataCards rather than DataFrames: a summary is what a reviewer
    can actually assess, and it keeps raw rows out of model context.
    """


class Stage(Protocol):
    """A pipeline stage: a pure-ish function of run state.

    Takes the accumulated state and an optional correction from the previous
    attempt; returns artifacts and signals. Deliberately narrow — a stage cannot
    reach the runtime, decide its own retries, or talk to the gate.
    """

    def __call__(self, state: RunState, correction: list[str] | None = None) -> StageResult: ...


class RunStatus:
    COMPLETED = "completed"
    #: Every stage the caller asked for finished and the rest were never
    #: started. Distinct from COMPLETED because the run is unfinished by
    #: request, and distinct from AWAITING_HUMAN because nothing is being
    #: asked -- resuming it needs a decision to continue, not an answer.
    STAGED = "staged"
    AWAITING_HUMAN = "awaiting_human"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclass
class RunOutcome:
    """The result of executing a spec, including why it stopped."""

    run_id: str
    status: str
    final_stage: str | None
    decisions: list[GateDecision] = field(default_factory=list)
    error: str | None = None

    @property
    def pending_question(self) -> GateDecision | None:
        """The escalation the run is waiting on, if any."""
        if self.status != RunStatus.AWAITING_HUMAN:
            return None
        return self.decisions[-1] if self.decisions else None

    @property
    def completed(self) -> bool:
        return self.status == RunStatus.COMPLETED


def run_workflow(
    spec: WorkflowSpec,
    registry: ComponentRegistry,
    state: RunState,
    *,
    policy: GatePolicy | None = None,
    rubrics: RubricRegistry | None = None,
    critic_llm: object | None = None,
    max_total_steps: int = 100,
    on_event: Callable[[str, dict], None] | None = None,
    start_at: str | None = None,
    stop_after: str | None = None,
) -> RunOutcome:
    """Execute a spec until it completes, escalates, or aborts.

    ``stop_after`` runs the spec up to and including one stage and then stops
    with ``STAGED``. It exists so the first stages -- loading and profiling the
    data, and reading its schema -- can run the moment a dataset is chosen,
    which is what a person needs to see before they can configure anything. The
    stopped run is a real run with real artifacts, not a preview: continuing it
    is ``start_at`` on the same state, so nothing is recomputed.

    ``max_total_steps`` is a wall against a retry loop that never converges. The
    gate's per-stage budget should catch that first; this is the backstop for a
    spec whose edges cycle, which validation cannot fully rule out because
    retreat edges (evaluation back to feature engineering) are legitimate.
    """
    policy = policy or GatePolicy.load()
    registry.validate_spec(spec)

    decisions: list[GateDecision] = []
    current: str | None = start_at or spec.entry
    steps = 0

    def emit(event: str, **payload) -> None:
        if on_event is not None:
            on_event(event, payload)

    while current is not None:
        steps += 1
        if steps > max_total_steps:
            return RunOutcome(
                run_id=state.run_id,
                status=RunStatus.FAILED,
                final_stage=current,
                decisions=decisions,
                error=(
                    f"exceeded {max_total_steps} stage executions; the spec's edges "
                    "are cycling without converging"
                ),
            )

        definition = spec.stage(current)
        stage_spec = policy.stage(current)
        stage_ordinal = spec.stage_ids().index(current)

        # Decide self-retry from control flow *before* anything is consumed.
        # Correction text is not retry identity: a RETRY may legitimately carry
        # no instructions, and inferring identity from prose let such a retry
        # rebind to newer inputs -- the different-experiment defect through a
        # narrower door (Codex FINDING 17).
        previous = state.attempts_for(current)
        is_self_retry = _is_self_retry(spec, state, current, previous)

        # Read the previous attempt's correction *before* opening a new attempt:
        # begin_attempt appends a record whose decision is still None, which
        # would otherwise mask the instruction the retry exists to deliver.
        correction = _correction_for(state, current)

        attempt = state.begin_attempt(current)

        # Pin declared inputs for the whole attempt. A self-retry inherits the
        # rejected attempt's exact inputs so the correction is the only thing
        # that changed; a deliberate re-entry resolves afresh and that lineage
        # change becomes visible in the attempt log.
        attempt.input_bindings = state.resolve_bindings(
            definition.consumes,
            inherit=previous[-1].input_bindings if is_self_retry else None,
        )
        state.active_attempt = attempt
        emit("stage_started", stage=current, attempt=attempt.attempt)
        seed_scope = None
        try:
            if state.run_seed is None:
                result = registry.resolve(definition.component)(state, correction)
            else:
                with agent_seed_scope(
                    run_seed=state.run_seed,
                    stage_ordinal=stage_ordinal,
                    attempt=attempt.attempt,
                ) as seed_scope:
                    result = registry.resolve(definition.component)(state, correction)
        except Exception as exc:  # noqa: BLE001 - recorded, then gated
            state.active_attempt = None
            attempt.error = f"{type(exc).__name__}: {exc}"
            attempt.ended_at = datetime.now(UTC)
            emit("stage_failed", stage=current, error=attempt.error)
            return RunOutcome(
                run_id=state.run_id,
                status=RunStatus.FAILED,
                final_stage=current,
                decisions=decisions,
                error=attempt.error,
            )
        finally:
            if seed_scope is not None:
                attempt.agent_seeds.extend(seed_scope.seeds)

        state.active_attempt = None
        for index, artifact in enumerate(result.artifacts):
            ref = state.put(artifact, stage_id=current, name=result.names.get(index))
            attempt.artifact_ids.append(ref.artifact_id)
        # A stage may emit a useful audit record while failing to emit the
        # contract the graph actually needs. Counting artifacts was therefore
        # insufficient: the human could be offered Approve even though the
        # proceed edge could only fail with MissingArtifactError (#263).
        proceed_to = spec.next_stage(current, EdgeCondition.ON_PROCEED)
        missing_required_artifacts: list[str] = []
        if proceed_to is not None:
            for artifact_type in spec.stage(proceed_to).consumes:
                if state.store.latest(state.run_id, artifact_type) is None:
                    missing_required_artifacts.append(artifact_type.value)
        gate_signals = result.signals
        if missing_required_artifacts:
            gate_signals = result.signals.model_copy(
                update={"missing_required_artifacts": missing_required_artifacts}
            )
        # The Orchestrator judges the stage against a rubric the stage does not
        # own. A stage-supplied critique is kept only when no rubric exists —
        # otherwise the judged would also be the judge.
        if state.run_seed is not None and isinstance(critic_llm, StructuredLLM):
            seeded_critic = (
                critic_llm
                if isinstance(critic_llm, SeededStructuredLLM)
                else SeededStructuredLLM(critic_llm)
            )
            with agent_seed_scope(
                run_seed=state.run_seed,
                stage_ordinal=stage_ordinal,
                attempt=attempt.attempt,
                call_ordinal=len(attempt.agent_seeds),
            ) as critique_seed_scope:
                attempt.critique = (
                    _critique(rubrics, current, result, state, seeded_critic) or result.critique
                )
            attempt.agent_seeds.extend(critique_seed_scope.seeds)
        else:
            attempt.critique = (
                _critique(rubrics, current, result, state, critic_llm) or result.critique
            )
        attempt.ended_at = datetime.now(UTC)

        decision = evaluate_gate(
            stage=stage_spec,
            signals=gate_signals,
            profile=state.profile,
            history=StageHistory(
                attempts=attempt.attempt,
                prior_unmet_criteria=state.prior_unmet(current)[:-1],
            ),
            critique=attempt.critique,
            policy=policy,
            artifact_ids=attempt.artifact_ids,
            missing_required_artifacts=missing_required_artifacts,
            context_summary=definition.description,
        )
        attempt.decision = decision
        decisions.append(decision)
        state.put(decision, stage_id=current, name=f"gate_{current}")
        emit(
            "gate_decided",
            stage=current,
            verdict=decision.verdict.value,
            reason=decision.reason_code,
        )

        if decision.verdict is GateVerdict.ABORT:
            return RunOutcome(state.run_id, RunStatus.ABORTED, current, decisions)

        if decision.verdict is GateVerdict.ESCALATE:
            # Stop cleanly. The run is resumable: state and artifacts are intact,
            # and the human's answer arrives on the blackboard.
            return RunOutcome(state.run_id, RunStatus.AWAITING_HUMAN, current, decisions)

        if decision.verdict is GateVerdict.RETRY:
            retry_target = spec.next_stage(current, EdgeCondition.ON_RETRY)
            if retry_target is None:
                return RunOutcome(
                    run_id=state.run_id,
                    status=RunStatus.FAILED,
                    final_stage=current,
                    decisions=decisions,
                    error=(
                        f"gate asked to retry {current!r} but the spec declares no "
                        "ON_RETRY edge for it"
                    ),
                )
            current = retry_target
            continue

        completed = current
        current = spec.next_stage(current, _VERDICT_TO_EDGE[decision.verdict])
        if stop_after is not None and completed == stop_after:
            # Stop after the gate has spoken, not before: a stage whose gate
            # would have escalated must escalate here too, or staging would
            # quietly swallow the one thing a person needed to be asked.
            return RunOutcome(state.run_id, RunStatus.STAGED, completed, decisions)

    return RunOutcome(state.run_id, RunStatus.COMPLETED, None, decisions)


def _critique(
    rubrics: RubricRegistry | None,
    stage_id: str,
    result: StageResult,
    state: RunState,
    llm: object | None,
) -> CritiqueResult | None:
    """Run the Orchestrator's rubric, if one is declared for this stage."""
    if rubrics is None:
        return None
    rubric = rubrics.get(stage_id)
    if rubric is None:
        return None
    context = CritiqueContext(
        stage_id=stage_id,
        artifacts=list(result.artifacts),
        facts={"signals": result.signals, "run_state": state},
    )
    return critique_stage(
        rubric,
        context,
        llm=llm,
        artifact_digest=result.digest,  # type: ignore[arg-type]
    )


def _is_self_retry(
    spec: WorkflowSpec,
    state: RunState,
    stage_id: str,
    previous: Sequence[StageAttempt],
) -> bool:
    """Whether this execution is the same stage running again after a rejection.

    Determined from the graph and the previous verdict, never from whether the
    correction carried text. Two shapes count:

    * the gate returned RETRY and the spec routes this stage's retry to itself
    * a human answered an escalation with "retry", which re-enters the same
      stage carrying their instructions

    A retreat that lands on a *different* stage is not a self-retry: it is a
    deliberate re-derivation, and it should see current inputs.
    """
    if not previous:
        return False
    decision = previous[-1].decision
    if decision is None:
        return False
    if f"human_correction::{stage_id}" in state.blackboard:
        return True
    return (
        decision.verdict is GateVerdict.RETRY
        and spec.next_stage(stage_id, EdgeCondition.ON_RETRY) == stage_id
    )


def _correction_for(state: RunState, stage_id: str) -> list[str] | None:
    """The previous attempt's correction instructions, if it was told to retry.

    A re-invoked stage receives its original inputs plus this — and deliberately
    *not* a transcript of its failed attempt. Showing a model its own bad
    reasoning reliably produces an elaboration of it rather than a correction.
    """
    human = state.blackboard.pop(f"human_correction::{stage_id}", None)
    if human:
        # Consumed once: a person's instruction applies to the next attempt, not
        # to every subsequent one.
        return list(human)

    previous = state.last_decision(stage_id)
    if previous is None or previous.verdict is not GateVerdict.RETRY:
        return None
    return list(previous.correction_instructions) or None


__all__ = [
    "RunOutcome",
    "RunStatus",
    "Stage",
    "StageResult",
    "resume_workflow",
    "run_workflow",
]


def resume_workflow(
    spec: WorkflowSpec,
    registry: ComponentRegistry,
    state: RunState,
    *,
    decision: str,
    instructions: Sequence[str] = (),
    policy: GatePolicy | None = None,
    rubrics: RubricRegistry | None = None,
    critic_llm: object | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> RunOutcome:
    """Continue a run that stopped at a human gate.

    An escalation is only useful if the answer can be acted on. The run stopped
    with its artifacts and attempt log intact, so resuming is a matter of
    recording the decision and re-entering the graph — not replaying anything.

    ``decision`` is one of the option ids the gate offered:

    * ``approve``  — accept the stage and follow its proceed edge
    * ``retry``    — re-run the stage with the human's instructions attached
    * ``abort``    — stop for good

    The human's answer is recorded on the blackboard before anything else runs,
    so a later report can state which decisions a person made and which the
    system took autonomously.
    """
    stopped_at = _stopped_stage(state)
    if stopped_at is None:
        raise RuntimeError("this run is not waiting on a human decision")

    state.blackboard.setdefault("human_decisions", []).append(
        {
            "stage_id": stopped_at,
            "decision": decision,
            "instructions": list(instructions),
        }
    )

    if decision == "abort":
        return RunOutcome(state.run_id, RunStatus.ABORTED, stopped_at, [])

    if decision == "retry":
        # Record the human's instructions where the retry loop already looks, so
        # a human correction travels the same path as a machine-generated one.
        state.blackboard[f"human_correction::{stopped_at}"] = list(instructions)
        start = stopped_at
    elif decision == "approve":
        start = spec.next_stage(stopped_at, EdgeCondition.ON_PROCEED)
        if start is None:
            return RunOutcome(state.run_id, RunStatus.COMPLETED, None, [])
    else:
        raise ValueError(f"unknown decision {decision!r}; expected approve, retry, or abort")

    return run_workflow(
        spec,
        registry,
        state,
        policy=policy,
        rubrics=rubrics,
        critic_llm=critic_llm,
        on_event=on_event,
        start_at=start,
    )


def _stopped_stage(state: RunState) -> str | None:
    """The stage whose gate escalated, if the run is waiting."""
    for attempt in reversed(state.attempts):
        if attempt.decision is not None:
            if attempt.decision.verdict is GateVerdict.ESCALATE:
                return attempt.stage_id
            return None
    return None
