"""Agent-backed orchestration stages with injectable structured LLMs.

One model call is made per orchestration attempt.  The agent modules still own
their prompts, contracts, validators, and deterministic repair; the outer
orchestrator owns retry budgets and audit lineage, avoiding nested three-by-three
retry loops.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

import pandas as pd

from ads.agents.base import (
    AgentContext,
    AgentPanelResult,
    AgentSpec,
    require_tool_evidence,
    run_agent_panel,
)
from ads.agents.problem_discovery import (
    attach_support,
)
from ads.agents.problem_discovery import (
    build_context as build_problem_context,
)
from ads.agents.problem_discovery import (
    build_spec as build_problem_spec,
)
from ads.agents.problem_investigator import investigate_problem_context
from ads.agents.schema_discovery import (
    build_context_with_evidence,
)
from ads.agents.schema_discovery import (
    build_spec as build_schema_spec,
)
from ads.agents.schema_investigator import (
    SchemaMemberResult,
    investigate_schema,
)
from ads.agents.schema_investigator import (
    build_action_spec as build_schema_action_spec,
)
from ads.agents.validation_investigator import investigate_validation_context
from ads.agents.validation_strategy import (
    build_context as build_validation_context,
)
from ads.agents.validation_strategy import (
    build_spec as build_validation_spec,
)
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.base import ArtifactType
from ads.contracts.datacard import DataCard, SemanticType
from ads.contracts.gates import QualitySignals
from ads.contracts.integration import IntegrationPlan, IntegrationPlanProposal
from ads.contracts.problem import (
    ProblemDefinition,
    ProblemDiscoveryProposal,
)
from ads.contracts.validation import (
    ValidationSignals,
    ValidationStrategy,
    ValidationStrategyProposal,
)
from ads.intake import detect_relationships
from ads.llm import StructuredLLM
from ads.orchestration import RunState, StageResult
from ads.pipeline.stages import (
    ABT_FRAME_KEY,
    EXECUTION_BACKEND_KEY,
    SOURCE_CARDS_KEY,
    SOURCE_FRAMES_KEY,
    STAGE_DIRECTIVES_KEY,
    VALIDATION_FOLDS_KEY,
    agent_runtime_policy,
)
from ads.sandbox import ExecutionBackend, materialize_frame_copies
from ads.skills import select_skills
from ads.splitting import execute_validation_trial
from ads.store import compute_artifact_id
from ads.tools import PermissionBroker, ToolRuntime, build_tool_registry


def _single_call[T](spec: AgentSpec[T]) -> AgentSpec[T]:
    """Delegate retry budgeting to the outer workflow, one call per attempt."""
    return replace(spec, max_attempts=1)


def _require_evidence[T](spec: AgentSpec[T], *tool_ids: str) -> AgentSpec[T]:
    return replace(spec, validators=(*spec.validators, require_tool_evidence(*tool_ids)))


def _admit_tool(
    context: AgentContext,
    broker: PermissionBroker,
    spec: AgentSpec[Any],
    runtime: ToolRuntime,
    tool_id: str,
    **arguments: Any,
):
    result = broker.invoke(spec, tool_id, runtime, **arguments)
    context.admit_tool_result(result.tool_id, result.summary, tier=result.tier)
    return result


def _with_directives(context: AgentContext, state: RunState, stage_id: str) -> AgentContext:
    """Carry a person's instruction to this stage's agent into its context.

    Rendered as a distinct section rather than folded into the correction block,
    because they mean different things: a correction says the last attempt was
    wrong, a directive says what the human wants regardless of any attempt. An
    agent that cannot tell them apart will treat a preference as a failure.
    """
    directives = (state.blackboard.get(STAGE_DIRECTIVES_KEY) or {}).get(stage_id)
    if not directives:
        return context
    context.sections["Instruction from the human"] = "\n".join(f"- {line}" for line in directives)
    return context


def _with_correction(context: AgentContext, correction: list[str] | None) -> AgentContext:
    if not correction:
        return context
    context.sections["Orchestrator correction"] = "\n".join(
        f"- {instruction}" for instruction in correction
    )
    return context


def _validation_failure_count(result: AgentPanelResult[Any]) -> int:
    return len(result.all_failures)


def _agent_audit(
    stage_id: str,
    spec: AgentSpec[Any],
    context: AgentContext,
    result: AgentPanelResult[Any],
) -> AgentAudit:
    members = []
    for index, member in enumerate(result.members, start=1):
        attempts = member.attempts
        members.append(
            AgentMemberAudit(
                member=index,
                model=attempts[-1].response.model if attempts else spec.profile.name,
                attempts=len(attempts),
                accepted=member.succeeded,
                validation_failures=[failure.code for failure in member.all_failures],
                repairs=[repair for attempt in attempts for repair in attempt.repairs],
                latency_s=member.total_latency_s,
            )
        )
    return AgentAudit(
        stage_id=stage_id,
        agent_id=spec.id,
        output_contract=spec.output_contract.__name__,
        panel_size=len(result.members),
        valid_members=sum(member.succeeded for member in result.members),
        agreement=result.agreement if len(result.members) > 1 else None,
        verbatim_agreement=(result.verbatim_agreement if len(result.members) > 1 else None),
        allowed_tools=sorted(spec.allowed_tools),
        evidence_tools=sorted({item.tool_id for item in context.evidence_tools}),
        skills_used=list(context.facts.get("skill_ids", [])),
        validator_count=len(spec.validators),
        members=members,
    )


def _failed_result(result: AgentPanelResult[Any], audit: AgentAudit) -> StageResult:
    failures = result.all_failures
    detail = "; ".join(f"{failure.code}: {failure.detail}" for failure in failures)
    if not detail:
        detail = "The agent returned no valid contract."
    return StageResult(
        artifacts=[audit],
        names={0: "agent_audit"},
        signals=QualitySignals(validation_failures=_validation_failure_count(result)),
        digest=detail[:1500],
    )


def _source_inputs(state: RunState) -> tuple[list[DataCard], dict[str, pd.DataFrame]]:
    cards = state.blackboard.get(SOURCE_CARDS_KEY)
    frames = state.blackboard.get(SOURCE_FRAMES_KEY)
    if not isinstance(cards, list) or not all(isinstance(card, DataCard) for card in cards):
        raise ValueError("Intake did not place source DataCards on the run blackboard.")
    if not isinstance(frames, dict) or not all(
        isinstance(name, str) and isinstance(frame, pd.DataFrame) for name, frame in frames.items()
    ):
        raise ValueError("Intake did not place source frames on the run blackboard.")
    return cards, frames


def make_schema_discovery_stage(llm: StructuredLLM, *, panel_size: int = 1):
    """Create active schema discovery with tool-driven deterministic plan trials."""
    if panel_size < 1:
        raise ValueError("panel_size must be at least 1")

    def _audit(members: list[SchemaMemberResult], *, allow_code: bool) -> AgentAudit:
        valid = [member for member in members if member.succeeded]
        decisions = Counter(
            member.decision_fingerprint
            for member in valid
            if member.decision_fingerprint is not None
        )
        verbatim = Counter(
            member.verbatim_fingerprint
            for member in valid
            if member.verbatim_fingerprint is not None
        )
        return AgentAudit(
            stage_id="schema_discovery",
            agent_id="schema_discovery",
            output_contract=IntegrationPlanProposal.__name__,
            panel_size=len(members),
            valid_members=len(valid),
            agreement=(
                max(decisions.values(), default=0) / len(members) if len(members) > 1 else None
            ),
            verbatim_agreement=(
                max(verbatim.values(), default=0) / len(members) if len(members) > 1 else None
            ),
            allowed_tools=sorted(build_schema_action_spec(allow_code=allow_code).allowed_tools),
            evidence_tools=sorted({tool for member in members for tool in member.tool_calls}),
            validator_count=len(build_schema_spec().validators) + 2,
            raw_rows_shared=any(member.raw_rows_shared for member in members),
            members=[
                AgentMemberAudit(
                    member=index,
                    model=member.model,
                    attempts=member.attempts,
                    accepted=member.succeeded,
                    validation_failures=[failure.code for failure in member.failures],
                    repairs=[],
                    latency_s=round(member.latency_s, 3),
                )
                for index, member in enumerate(members, start=1)
            ],
        )

    def stage(state: RunState, correction: list[str] | None = None) -> StageResult:
        cards, frames = _source_inputs(state)
        relationships = detect_relationships(cards, frames)
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if backend is not None and not isinstance(backend, ExecutionBackend):
            raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
        data_paths = materialize_frame_copies(backend, frames) if backend else {}
        runtime_policy = agent_runtime_policy(state)
        allow_code = False
        if backend is not None:
            probe = ToolRuntime(execution_backend=backend)
            allow_code = probe.execution_available() and runtime_policy.code_enabled(
                "schema_investigation"
            )

        skills = select_skills("schema_discovery", cards)
        members: list[SchemaMemberResult] = []
        for member_index in range(panel_size):
            context = build_context_with_evidence(cards, relationships)
            if skills:
                context.sections["Relevant schema skills"] = "\n\n".join(
                    skill.body for skill in skills
                )
            if state.user_intent:
                context.sections["User planning preferences"] = state.user_intent
            context = _with_directives(context, state, "schema_discovery")
            context = _with_correction(context, correction)
            runtime = ToolRuntime.from_sources(
                cards,
                frames,
                run_id=f"{state.run_id}-schema-{member_index + 1}",
                execution_backend=backend,
                artifacts_dir=backend.artifacts_dir if backend else None,
            )
            members.append(
                investigate_schema(
                    context=context,
                    llm=llm,
                    runtime=runtime,
                    data_paths=data_paths,
                    budget=runtime_policy.budget("schema_investigation"),
                    code_execution_enabled=runtime_policy.code_enabled("schema_investigation"),
                )
            )

        audit = _audit(members, allow_code=allow_code)
        valid = [member for member in members if member.succeeded]
        if not valid:
            failures = [failure for member in members for failure in member.failures]
            detail = (
                "; ".join(f"{failure.code}: {failure.detail}" for failure in failures)
                or "The schema investigators returned no tested plan."
            )
            return StageResult(
                artifacts=[audit],
                names={0: "agent_audit"},
                signals=QualitySignals(validation_failures=len(failures)),
                digest=detail[:1500],
            )

        counts = Counter(member.decision_fingerprint for member in valid)
        selected_fingerprint = counts.most_common(1)[0][0]
        selected = next(
            member for member in valid if member.decision_fingerprint == selected_fingerprint
        )
        assert selected.proposal is not None
        assert selected.trial is not None
        trial = selected.trial
        trial_id = compute_artifact_id(trial)
        plan = IntegrationPlan.from_proposal(
            selected.proposal,
            relationships,
            trial_artifact_id=trial_id,
        )
        return StageResult(
            artifacts=[plan, trial, audit],
            names={
                0: "integration_plan",
                1: "integration_trial",
                2: "agent_audit",
            },
            signals=QualitySignals(
                # Rejected exploratory actions remain in the audit, but a member that
                # recovered and submitted a fully validated, exactly trialled plan has
                # no outstanding contract failure. Treating investigation history as
                # final invalidity would make the new correction loop self-defeating.
                validation_failures=0,
                self_consistency_agreement=(audit.agreement if panel_size > 1 else None),
                panel_size=panel_size if panel_size > 1 else None,
                panel_valid_members=(len(valid) if panel_size > 1 else None),
            ),
            digest=(f"{plan.summary()}; deterministic trial: {trial.summary()}"),
        )

    return stage


def _abt_frame(state: RunState) -> pd.DataFrame:
    frame = state.blackboard.get(ABT_FRAME_KEY)
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("Integration did not place an ABT frame on the run blackboard.")
    return frame


def make_problem_discovery_stage(llm: StructuredLLM, *, panel_size: int = 1):
    """Create problem discovery plus deterministic support attachment/selection."""
    spec = _single_call(_require_evidence(build_problem_spec(), "column_profile", "null_rate"))

    def stage(state: RunState, correction: list[str] | None = None) -> StageResult:
        card = state.require(ArtifactType.DATA_CARD, DataCard)
        frame = _abt_frame(state)
        context = _with_correction(
            build_problem_context(card, user_intent=state.user_intent), correction
        )
        investigation_audit: AgentAudit | None = None
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if backend is not None:
            if not isinstance(backend, ExecutionBackend):
                raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
            materialize_frame_copies(
                backend,
                {card.table_name: frame},
                replace_existing=True,
            )
            investigation = investigate_problem_context(
                context=context,
                llm=llm,
                runtime=ToolRuntime.from_sources(
                    [card],
                    {card.table_name: frame},
                    run_id=f"{state.run_id}-problem-investigation",
                    execution_backend=backend,
                    artifacts_dir=backend.artifacts_dir,
                ),
                budget=agent_runtime_policy(state).budget("problem_investigation"),
                code_execution_enabled=agent_runtime_policy(state).code_enabled(
                    "problem_investigation"
                ),
            )
            investigation_audit = investigation.audit
        runtime = ToolRuntime.from_sources([card], {card.table_name: frame}, run_id=state.run_id)
        broker = PermissionBroker(build_tool_registry())
        for column in card.candidate_targets()[:12]:
            arguments = {"table": card.table_name, "column": column.name}
            _admit_tool(context, broker, spec, runtime, "column_profile", **arguments)
            _admit_tool(context, broker, spec, runtime, "null_rate", **arguments)
            _admit_tool(context, broker, spec, runtime, "cardinality", **arguments)
            if column.semantic_type in {
                SemanticType.BOOLEAN,
                SemanticType.CATEGORICAL,
                SemanticType.NUMERIC_DISCRETE,
            }:
                _admit_tool(context, broker, spec, runtime, "value_counts", **arguments)
        result: AgentPanelResult[ProblemDiscoveryProposal] = run_agent_panel(
            spec, context, llm, panel_size=panel_size
        )
        audit = _agent_audit("problem_discovery", spec, context, result)
        if not result.succeeded:
            failed = _failed_result(result, audit)
            if investigation_audit is None:
                return failed
            return StageResult(
                artifacts=[*failed.artifacts, investigation_audit],
                names={**failed.names, len(failed.artifacts): "problem_investigation_audit"},
                signals=failed.signals,
                critique=failed.critique,
                digest=failed.digest,
            )

        candidates = attach_support(
            result.require(),
            card,
            frame,
            user_intent=state.user_intent,
        )
        selected = candidates.viable()[0] if candidates.viable() else candidates.candidates[0]
        problem = ProblemDefinition(
            task_type=selected.task_type,
            target_column=selected.target_column,
            primary_metric=selected.primary_metric,
            title=selected.title,
            description=selected.business_rationale,
            excluded_columns=[],
            confirmed_by="auto",
            source_candidate_id=selected.candidate_id,
        )
        support = selected.support
        artifacts = [candidates, problem, audit]
        names = {
            0: "problem_candidates",
            1: "problem_definition",
            2: "agent_audit",
        }
        if investigation_audit is not None:
            names[len(artifacts)] = "problem_investigation_audit"
            artifacts.append(investigation_audit)
        return StageResult(
            artifacts=artifacts,
            names={
                **names,
            },
            signals=QualitySignals(
                validation_failures=_validation_failure_count(result),
                n_rows=support.n_rows,
                minority_class_count=support.minority_class_count,
                rows_per_feature=support.rows_per_feature,
                self_consistency_agreement=(result.agreement if panel_size > 1 else None),
                panel_size=panel_size if panel_size > 1 else None,
                panel_valid_members=(
                    sum(m.succeeded for m in result.members) if panel_size > 1 else None
                ),
            ),
            digest=str(candidates.summary()),
        )

    return stage


def make_validation_strategy_stage(llm: StructuredLLM, *, panel_size: int = 1):
    """Create validation-strategy selection constrained by measured signals."""
    spec = _single_call(_require_evidence(build_validation_spec(), "validation_signals"))

    def stage(state: RunState, correction: list[str] | None = None) -> StageResult:
        card = state.require(ArtifactType.DATA_CARD, DataCard)
        problem = state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)
        frame = _abt_frame(state)
        n_folds = state.blackboard.get(VALIDATION_FOLDS_KEY, 3)
        if not isinstance(n_folds, int):
            raise ValueError("pipeline.validation_folds must be an integer.")
        runtime = ToolRuntime.from_sources([card], {card.table_name: frame}, run_id=state.run_id)
        broker = PermissionBroker(build_tool_registry())
        evidence_context = AgentContext()
        measured = _admit_tool(
            evidence_context,
            broker,
            spec,
            runtime,
            "validation_signals",
            table=card.table_name,
            target_column=problem.target_column,
            task_type=problem.task_type.value,
            n_folds=n_folds,
        )
        signals = ValidationSignals.model_validate(measured.data)
        context = build_validation_context(card, signals)
        context.admit_tool_result(measured.tool_id, measured.summary, tier=measured.tier)
        if state.user_intent:
            context.sections["User planning preferences"] = state.user_intent
        context = _with_directives(context, state, "validation_strategy")
        context = _with_correction(context, correction)
        investigation_audit: AgentAudit | None = None
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if backend is not None:
            if not isinstance(backend, ExecutionBackend):
                raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
            materialize_frame_copies(
                backend,
                {card.table_name: frame},
                replace_existing=True,
            )
            investigation_audit = investigate_validation_context(
                context=context,
                llm=llm,
                runtime=ToolRuntime.from_sources(
                    [card],
                    {card.table_name: frame},
                    run_id=f"{state.run_id}-validation-investigation",
                    execution_backend=backend,
                    artifacts_dir=backend.artifacts_dir,
                    resources={"validation_signals": signals},
                ),
                budget=agent_runtime_policy(state).budget("validation_investigation"),
                code_execution_enabled=agent_runtime_policy(state).code_enabled(
                    "validation_investigation"
                ),
            )
        result: AgentPanelResult[ValidationStrategyProposal] = run_agent_panel(
            spec, context, llm, panel_size=panel_size
        )
        audit = _agent_audit("validation_strategy", spec, context, result)
        if not result.succeeded:
            return _failed_result(result, audit)

        proposal = result.require()
        trial = execute_validation_trial(frame, proposal, signals)
        strategy = ValidationStrategy.from_proposal(
            proposal,
            signals,
            trial_artifact_id=compute_artifact_id(trial),
        )
        artifacts = [strategy, trial, audit]
        names = {0: "validation_strategy", 1: "validation_trial", 2: "agent_audit"}
        if investigation_audit is not None:
            names[len(artifacts)] = "validation_investigation_audit"
            artifacts.append(investigation_audit)
        return StageResult(
            artifacts=artifacts,
            names=names,
            signals=QualitySignals(
                validation_failures=_validation_failure_count(result),
                n_rows=signals.n_usable_rows,
                minority_class_count=signals.minority_class_count,
                self_consistency_agreement=(result.agreement if panel_size > 1 else None),
                panel_size=panel_size if panel_size > 1 else None,
                panel_valid_members=(
                    sum(m.succeeded for m in result.members) if panel_size > 1 else None
                ),
            ),
            digest=str(strategy.summary()),
        )

    return stage


__all__ = [
    "make_problem_discovery_stage",
    "make_schema_discovery_stage",
    "make_validation_strategy_stage",
]
