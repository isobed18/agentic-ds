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
from ads.contracts.datacard import ColumnProfile, DataCard, SemanticType
from ads.contracts.gates import QualitySignals
from ads.contracts.integration import IntegrationPlan, IntegrationPlanProposal, IntegrationTrial
from ads.contracts.problem import (
    Metric,
    ProblemCandidateProposal,
    ProblemDefinition,
    ProblemDiscoveryProposal,
    TaskType,
)
from ads.contracts.validation import (
    ValidationSignals,
    ValidationStrategy,
    ValidationStrategyProposal,
)
from ads.discovery.support import MAX_CLASSES
from ads.intake import detect_primary_keys, detect_relationships
from ads.llm import StructuredLLM
from ads.orchestration import RunState, StageResult
from ads.pipeline.stages import (
    ABT_FRAME_KEY,
    EXECUTION_BACKEND_KEY,
    QUICK_PROBLEM_KEY,
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
from ads.tools.integration import trial_integration_plan

_SYNTHETIC_ROW_ID_BASE = "__ads_row_id"


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


def _validation_failure_details(result: AgentPanelResult[Any]) -> list[str]:
    """Which validators fired, for a reader rather than for a rule (#427).

    The count alone cannot be acted on: `unknown_target` on a column named in
    the original casing and `task_target_mismatch` on a datetime are both "1
    validation failure", and they call for opposite corrections.
    """
    seen: list[str] = []
    for failure in result.all_failures:
        line = f"{failure.layer}/{failure.code}: {failure.detail}"
        if failure.field_path:
            line = f"{line} (at {failure.field_path})"
        if line not in seen:
            seen.append(line)
    return seen


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
        signals=QualitySignals(
            validation_failures=_validation_failure_count(result),
            validation_failure_details=_validation_failure_details(result),
        ),
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


def _trial_plan(
    *,
    state: RunState,
    cards: list[DataCard],
    frames: dict[str, pd.DataFrame],
    proposal: IntegrationPlanProposal,
) -> IntegrationTrial:
    """Run an executor-owned trial without routing a deterministic case through an agent."""
    runtime = ToolRuntime.from_sources(cards, frames, run_id=f"{state.run_id}-single-table-trial")
    try:
        payload = trial_integration_plan(
            runtime,
            {"plan": proposal.model_dump(mode="json", exclude={"created_at"})},
        )
        return IntegrationTrial.model_validate(payload.data)
    finally:
        runtime.close()


def _single_table_schema_result(
    *,
    state: RunState,
    cards: list[DataCard],
    frames: dict[str, pd.DataFrame],
) -> StageResult | None:
    """Build the no-join plan deterministically for one observation table.

    Schema interpretation is necessary when sources must be related. For one
    table there is no relationship judgment to make, so spending several model
    turns asking for a join plan adds latency and can only invent structure.
    """
    if len(cards) != 1 or len(frames) != 1:
        return None
    return _no_join_schema_result(state=state, card=cards[0], cards=cards, frames=frames)


def _largest_card(cards: list[DataCard], frames: dict[str, pd.DataFrame]) -> DataCard | None:
    """The widest usable table, by rows then columns then name.

    Deterministic all the way down, because this choice ends up in an artifact:
    two runs over the same files must pick the same base table.
    """
    usable = [
        card
        for card in cards
        if (frame := frames.get(card.table_name)) is not None and not frame.empty
    ]
    if not usable:
        return None
    return max(
        usable,
        key=lambda card: (len(frames[card.table_name]), len(card.columns), card.table_name),
    )


def _no_join_schema_result(
    *,
    state: RunState,
    card: DataCard,
    cards: list[DataCard],
    frames: dict[str, pd.DataFrame],
    extra_warnings: tuple[list[str], list[str]] | None = None,
) -> StageResult | None:
    """A verified no-join plan over ``card``, with a row identity if needed.

    A source key is preferred and verified against the full frame. When none is
    clean, the executor adds a reserved row identity to its in-memory copy. The
    resulting ABT profiles that column as an identifier, which keeps it out of
    targets and model features while preserving exact row lineage.
    """
    frame = frames.get(card.table_name)
    if frame is None or frame.empty:
        return None

    candidates = detect_primary_keys(card, frame)
    grain = list(candidates[0].columns) if candidates else []
    base_warnings, base_warnings_tr = extra_warnings or ([], [])
    warnings: list[str] = list(base_warnings)
    warnings_tr: list[str] = list(base_warnings_tr)

    def proposal_for(columns: list[str]) -> IntegrationPlanProposal:
        return IntegrationPlanProposal(
            base_table=card.table_name,
            base_grain=columns,
            grain_description=(
                "One analytical row per source row, identified by " + ", ".join(columns) + "."
            ),
            grain_description_tr=(
                "Her kaynak satırı için bir analitik satır; kimlik: "
                + ", ".join(columns)
                + "."
            ),
            warnings=warnings,
            warnings_tr=warnings_tr,
        )

    trial: IntegrationTrial | None = None
    proposal: IntegrationPlanProposal | None = None
    if grain:
        proposal = proposal_for(grain)
        trial = _trial_plan(state=state, cards=cards, frames=frames, proposal=proposal)
        if not trial.grain_preserved:
            proposal = None
            trial = None

    if proposal is None:
        row_id = _SYNTHETIC_ROW_ID_BASE
        suffix = 1
        while row_id in frame.columns:
            row_id = f"{_SYNTHETIC_ROW_ID_BASE}_{suffix}"
            suffix += 1
        prepared = frame.copy()
        prepared.insert(0, row_id, range(len(prepared)))
        frames[card.table_name] = prepared
        state.blackboard[SOURCE_FRAMES_KEY] = frames
        warnings = [
            *base_warnings,
            "No clean source key exists. The executor added an executor-owned row identity "
            f"({row_id}) for lineage; it is excluded from model features.",
        ]
        warnings_tr = [
            *base_warnings_tr,
            "Temiz bir kaynak anahtarı yok. Yürütücü, veri soyunu izlemek için yürütücüye ait "
            f"bir satır kimliği ({row_id}) ekledi; bu alan model özelliklerinden çıkarılır.",
        ]
        proposal = proposal_for([row_id])
        trial = _trial_plan(state=state, cards=cards, frames=frames, proposal=proposal)

    if not trial.grain_preserved:
        raise ValueError("Executor-owned single-table row identity did not preserve grain.")
    trial_id = compute_artifact_id(trial)
    plan = IntegrationPlan.from_proposal(proposal, [], trial_artifact_id=trial_id)
    return StageResult(
        artifacts=[plan, trial],
        names={0: "integration_plan", 1: "integration_trial"},
        signals=QualitySignals(validation_failures=0),
        digest=(
            (
                "Single-table input requires no relationship inference. "
                if len(cards) == 1
                else f"No join could be established; continuing on {card.table_name} alone. "
            )
            + f"{plan.summary()}; deterministic trial: {trial.summary()}"
        ),
    )


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
        single_table = _single_table_schema_result(state=state, cards=cards, frames=frames)
        if single_table is not None:
            return single_table
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
            # #381: this used to be the end of the road. The stage returned an
            # audit and no integration_plan, and the next stage's declared input
            # was missing, so the run stopped at the gate with "schema_discovery
            # produced no integration_plan" and nothing to do but rework or stop
            # -- for two files that each work perfectly well on their own.
            #
            # A single table already degrades to a verified no-join plan over a
            # row identity. There is no reason several tables should not: falling
            # back to the largest one and saying so leaves a person with a
            # running pipeline and an explicit warning about what was left out,
            # instead of a dead end. The warning is on the plan, so it reaches
            # the review gate rather than only the logs.
            #
            # Only after the orchestrator has already sent this stage back once.
            # The retry loop is the first and better answer -- a corrected
            # attempt often does find the join -- and pre-empting it on the
            # first failure would replace a recoverable investigation with a
            # single-table plan nobody asked for. This is the floor under it,
            # not a substitute for it.
            base = _largest_card(cards, frames)
            if base is not None and len(cards) > 1 and state.attempt_count("schema_discovery") > 1:
                left_out = sorted(
                    card.table_name for card in cards if card.table_name != base.table_name
                )
                fallback = _no_join_schema_result(
                    state=state,
                    card=base,
                    cards=cards,
                    frames=frames,
                    extra_warnings=(
                        [
                            "No join between the uploaded tables could be established, so the "
                            f"analysis continues on {base.table_name} alone. Left out: "
                            f"{', '.join(left_out)}. Rework this stage if these tables should "
                            "be related."
                        ],
                        [
                            "Yüklenen tablolar arasında bir birleştirme kurulamadı; analiz "
                            f"yalnızca {base.table_name} üzerinden sürüyor. Dışarıda kalan: "
                            f"{', '.join(left_out)}. Bu tablolar ilişkilendirilmeliyse bu "
                            "aşamayı yeniden çalıştırın."
                        ],
                    ),
                )
                if fallback is not None:
                    return StageResult(
                        artifacts=[*fallback.artifacts, audit],
                        names={**fallback.names, len(fallback.artifacts): "agent_audit"},
                        # No outstanding contract failure: the plan this stage
                        # emits was built and trialled deterministically, the
                        # same reasoning the recovered-member path above
                        # applies. What the investigators failed to do is in the
                        # audit, and what was left out is a warning on the plan
                        # -- both of which reach the review gate. Counting the
                        # failures here instead asks the gate to retry a stage
                        # that has already produced a verified answer.
                        signals=QualitySignals(validation_failures=0),
                        digest=f"{detail[:1000]} | {fallback.digest}",
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


#: Deterministic per-task default, matched to what the ProblemDiscoveryAgent's
#: own system prompt already tells the LLM to prefer -- an imbalance-aware
#: metric over plain accuracy, and silhouette for the unsupervised case.
_QUICK_METRIC_BY_TASK: dict[TaskType, Metric] = {
    TaskType.REGRESSION: Metric.RMSE,
    TaskType.BINARY_CLASSIFICATION: Metric.ROC_AUC,
    TaskType.MULTICLASS_CLASSIFICATION: Metric.BALANCED_ACCURACY,
    TaskType.ANOMALY_DETECTION: Metric.SILHOUETTE,
}


def _infer_supervised_task_type(profile: ColumnProfile) -> TaskType:
    """Read a task type off a target column's own measured shape (#241).

    Same rule the ProblemDiscoveryAgent's system prompt states for the LLM to
    follow: 2 distinct values is binary, 3+ is multiclass, continuous numeric is
    regression. Falls back to regression for a shape that fits none of those --
    ``compute_support`` then raises the mismatch as a named blocking reason on
    the candidate rather than this function raising and failing the stage, so
    the human sees why instead of the run just breaking.
    """
    if profile.semantic_type in (SemanticType.NUMERIC_CONTINUOUS, SemanticType.NUMERIC_DISCRETE):
        return TaskType.REGRESSION
    if profile.semantic_type is SemanticType.BOOLEAN:
        return TaskType.BINARY_CLASSIFICATION
    if profile.semantic_type is SemanticType.CATEGORICAL:
        if profile.n_unique == 2:
            return TaskType.BINARY_CLASSIFICATION
        if 3 <= profile.n_unique <= MAX_CLASSES:
            return TaskType.MULTICLASS_CLASSIFICATION
    return TaskType.REGRESSION


def _quick_problem_proposal(selection: dict[str, Any], card: DataCard) -> ProblemCandidateProposal:
    """Build the one candidate a quick-pick selection names, with no LLM call.

    Mirrors what the agent proposes for the same shape of target, so the
    ``ProblemDefinition`` this produces is indistinguishable downstream from one
    a human confirmed out of the agent's proposals (#241).
    """
    kind = selection.get("kind")
    if kind == "flag_anomalies":
        return ProblemCandidateProposal(
            title="Flag unusual rows",
            title_tr="Alışılmadık satırları işaretle",
            task_type=TaskType.ANOMALY_DETECTION,
            target_column=None,
            business_rationale=(
                "Surfaces rows that deviate from the rest of the measured data, for manual "
                "review rather than a labelled prediction."
            ),
            business_rationale_tr=(
                "Etiketli bir tahmin yerine, ölçülen verinin geri kalanından sapan satırları "
                "manuel inceleme için ortaya çıkarır."
            ),
            evidence_columns=[],
            primary_metric=Metric.SILHOUETTE,
        )
    if kind == "predict_column":
        target_column = str(selection.get("target_column") or "")
        profile = card.column(target_column)
        # #428: a person correcting a failed problem discovery may name the task
        # type as well as the column. Inference reads the column's measured
        # shape, which is the right default, but it cannot know that a 0/1
        # integer is a label rather than a quantity -- and the person looking at
        # their own data does. An explicit choice wins; `compute_support` still
        # measures whether it is viable and names the blocking reasons if not.
        stated = selection.get("task_type")
        task_type = (
            TaskType(str(stated))
            if stated
            else _infer_supervised_task_type(profile)
            if profile
            else TaskType.REGRESSION
        )
        return ProblemCandidateProposal(
            title=f"Predict {target_column}"[:120],
            title_tr=f"{target_column} sütununu tahmin et"[:120],
            task_type=task_type,
            target_column=target_column,
            business_rationale=(
                f"Predicts {target_column} for each row so downstream decisions can act on it."
            )[:800],
            business_rationale_tr=(
                f"Her satır için {target_column} sütununu tahmin ederek sonraki kararların "
                "buna göre alınmasını sağlar."
            )[:800],
            evidence_columns=[target_column] if profile else [],
            primary_metric=_QUICK_METRIC_BY_TASK[task_type],
        )
    raise ValueError(f"unknown quick problem selection kind {kind!r}")


def make_problem_discovery_stage(llm: StructuredLLM, *, panel_size: int = 1):
    """Create problem discovery plus deterministic support attachment/selection."""
    spec = _single_call(_require_evidence(build_problem_spec(), "column_profile", "null_rate"))

    def stage(state: RunState, correction: list[str] | None = None) -> StageResult:
        card = state.require(ArtifactType.DATA_CARD, DataCard)
        frame = _abt_frame(state)
        # #241: a problem stated through the quick-pick selector is honoured on
        # the first attempt without ever calling the LLM. A rejected gate still
        # falls back to the full agent conversation below on retry (`correction`
        # is only set then) -- reproposing the same deterministic framing would
        # not be a rework, and the person clearly wants something else.
        quick_selection = state.blackboard.get(QUICK_PROBLEM_KEY)
        # #428: a framing pinned after a failure is a constraint, not a ranking
        # hint, so it is honoured even though a correction is present -- that
        # correction *is* the pin. #241's selection stays advisory on retry for
        # the reason it always was: a gate that rejected the deterministic
        # framing would only be handed the same one again.
        pinned = bool(quick_selection and quick_selection.get("pinned"))
        if quick_selection is not None and (pinned or correction is None):
            candidates = attach_support(
                ProblemDiscoveryProposal(
                    candidates=[_quick_problem_proposal(quick_selection, card)]
                ),
                card,
                frame,
                user_intent=state.user_intent,
            )
            selected = candidates.candidates[0]
            problem = ProblemDefinition(
                task_type=selected.task_type,
                target_column=selected.target_column,
                primary_metric=selected.primary_metric,
                title=selected.title,
                title_tr=selected.title_tr,
                description=selected.business_rationale,
                description_tr=selected.business_rationale_tr,
                excluded_columns=[],
                confirmed_by="human",
                source_candidate_id=selected.candidate_id,
            )
            support = selected.support
            return StageResult(
                artifacts=[candidates, problem],
                names={0: "problem_candidates", 1: "problem_definition"},
                signals=QualitySignals(
                    n_rows=support.n_rows,
                    minority_class_count=support.minority_class_count,
                    rows_per_feature=support.rows_per_feature,
                ),
                digest=str(candidates.summary()),
            )
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
            title_tr=selected.title_tr,
            description=selected.business_rationale,
            description_tr=selected.business_rationale_tr,
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
