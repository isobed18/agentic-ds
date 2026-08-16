"""Best-effort interpretation stages that never block scientific execution."""

from __future__ import annotations

from ads.agents.base import AgentResult, AgentSpec, run_agent
from ads.agents.interpretation import build_context, build_spec
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.base import ArtifactType
from ads.contracts.comprehension import (
    ComprehensionBrief,
    ComprehensionScope,
    InterpretationBatchProposal,
    InterpretationItem,
)
from ads.contracts.datacard import DataCard
from ads.contracts.eda import EDAReport
from ads.contracts.evidence import MeasurementBundle
from ads.contracts.integration import IntegrationPlan
from ads.discovery.measurements import (
    analysis_measurement_bundle,
    source_measurement_bundle,
)
from ads.llm import StructuredLLM
from ads.orchestration import RunState, StageResult
from ads.pipeline.stages import SOURCE_CARDS_KEY
from ads.store import compute_artifact_id


def _audit_from_result(
    stage_id: str,
    spec: AgentSpec[InterpretationBatchProposal],
    result: AgentResult[InterpretationBatchProposal],
) -> AgentAudit:
    attempts = result.attempts
    return AgentAudit(
        stage_id=stage_id,
        agent_id=spec.id,
        output_contract=spec.output_contract.__name__,
        panel_size=1,
        valid_members=int(result.succeeded),
        agreement=None,
        verbatim_agreement=None,
        allowed_tools=[],
        evidence_tools=[],
        validator_count=len(spec.validators),
        members=[
            AgentMemberAudit(
                member=1,
                model=attempts[-1].response.model if attempts else spec.profile.name,
                attempts=max(len(attempts), 1),
                accepted=result.succeeded,
                validation_failures=[
                    failure.code for failure in result.all_failures
                ],
                repairs=[repair for attempt in attempts for repair in attempt.repairs],
                latency_s=result.total_latency_s,
            )
        ],
    )


def _failure_audit(
    stage_id: str,
    spec: AgentSpec[InterpretationBatchProposal],
    exc: Exception,
) -> AgentAudit:
    return AgentAudit(
        stage_id=stage_id,
        agent_id=spec.id,
        output_contract=spec.output_contract.__name__,
        panel_size=1,
        valid_members=0,
        agreement=None,
        verbatim_agreement=None,
        allowed_tools=[],
        evidence_tools=[],
        validator_count=len(spec.validators),
        members=[
            AgentMemberAudit(
                member=1,
                model=spec.profile.name,
                attempts=1,
                accepted=False,
                validation_failures=[f"interpretation_error:{type(exc).__name__}"],
                repairs=[],
                latency_s=0.0,
            )
        ],
    )


def _interpret(
    *,
    stage_id: str,
    scope: ComprehensionScope,
    bundle: MeasurementBundle,
    llm: StructuredLLM,
) -> StageResult:
    """Return a healthy or explicitly degraded brief; never raise an LLM failure."""
    spec = build_spec()
    bundle_id = compute_artifact_id(bundle)
    context = build_context(bundle, scope)
    try:
        result = run_agent(spec, context, llm)
    except Exception as exc:  # noqa: BLE001 - this entire component is advisory
        # A backend, client, or response-shape bug must degrade explanation only;
        # scientific execution cannot depend on this optional interpretation pass.
        audit = _failure_audit(stage_id, spec, exc)
        reason = f"Interpretation failed: {type(exc).__name__}: {exc}"[:500]
        brief = ComprehensionBrief(
            scope=scope,
            measurement_bundle_id=bundle_id,
            degraded=True,
            degradation_reason=reason,
        )
        return StageResult(
            artifacts=[bundle, brief, audit],
            names={
                0: "measurements",
                1: "comprehension",
                2: f"{stage_id}_audit",
            },
            digest=reason,
        )

    audit = _audit_from_result(stage_id, spec, result)
    if not result.succeeded:
        codes = sorted({failure.code for failure in result.all_failures})
        reason = f"Interpretation failed validation: {codes or ['unknown']}"
        brief = ComprehensionBrief(
            scope=scope,
            measurement_bundle_id=bundle_id,
            degraded=True,
            degradation_reason=reason,
        )
    else:
        proposal = result.require()
        measurements = context.facts["measurement_by_id"]
        if not isinstance(measurements, dict):
            raise TypeError("Interpretation context lost its measurement catalog.")
        brief = ComprehensionBrief(
            scope=scope,
            measurement_bundle_id=bundle_id,
            items=[
                InterpretationItem.from_proposal(item, measurements)
                for item in proposal.items
            ],
        )
    return StageResult(
        artifacts=[bundle, brief, audit],
        names={
            0: "measurements",
            1: "comprehension",
            2: f"{stage_id}_audit",
        },
        digest=str(brief.summary()),
    )


def _merge(primary: StageResult, advisory: StageResult) -> StageResult:
    """Attach advisory artifacts without changing the checkpoint signals or critique."""
    offset = len(primary.artifacts)
    names = {**primary.names}
    names.update({offset + index: name for index, name in advisory.names.items()})
    digest_parts = [item for item in (primary.digest, advisory.digest) if item]
    return StageResult(
        artifacts=[*primary.artifacts, *advisory.artifacts],
        signals=primary.signals,
        critique=primary.critique,
        names=names,
        digest="\n\n".join(digest_parts) or None,
    )


def augment_schema_discovery_stage(stage, llm: StructuredLLM):
    """Run source interpretation after a valid plan, inside the existing checkpoint."""

    def augmented(
        state: RunState, correction: list[str] | None = None
    ) -> StageResult:
        primary = stage(state, correction)
        plan = next(
            (item for item in primary.artifacts if isinstance(item, IntegrationPlan)),
            None,
        )
        if plan is None:
            return primary
        cards = state.blackboard.get(SOURCE_CARDS_KEY)
        if not isinstance(cards, list) or not all(
            isinstance(card, DataCard) for card in cards
        ):
            raise ValueError("Intake did not place source DataCards on the run blackboard.")
        advisory = _interpret(
            stage_id="source_comprehension",
            scope=ComprehensionScope.SOURCE,
            bundle=source_measurement_bundle(cards, plan),
            llm=llm,
        )
        return _merge(primary, advisory)

    return augmented


def augment_eda_stage(stage, llm: StructuredLLM):
    """Run analysis interpretation after deterministic EDA, inside its checkpoint."""

    def augmented(
        state: RunState, correction: list[str] | None = None
    ) -> StageResult:
        primary = stage(state, correction)
        report = next(
            (item for item in primary.artifacts if isinstance(item, EDAReport)),
            None,
        )
        if report is None:
            return primary
        card = state.require(ArtifactType.DATA_CARD, DataCard)
        advisory = _interpret(
            stage_id="analysis_comprehension",
            scope=ComprehensionScope.ANALYSIS,
            bundle=analysis_measurement_bundle(card, report),
            llm=llm,
        )
        return _merge(primary, advisory)

    return augmented


__all__ = [
    "augment_eda_stage",
    "augment_schema_discovery_stage",
]
