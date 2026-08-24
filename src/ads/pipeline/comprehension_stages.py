"""Best-effort interpretation stages that never block scientific execution."""

from __future__ import annotations

import pandas as pd

from ads.agents.base import AgentResult, AgentSpec, run_agent
from ads.agents.eda_investigator import investigate_eda
from ads.agents.feature_investigator import (
    degraded_feature_investigation,
    investigate_features,
)
from ads.agents.interpretation import build_context, build_spec
from ads.agents.leakage_investigator import investigate_leakage
from ads.agents.model_investigator import (
    degraded_model_investigation,
    investigate_model,
)
from ads.agents.sensitivity_investigator import investigate_sensitivity
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.base import ArtifactType
from ads.contracts.comprehension import (
    ComprehensionBrief,
    ComprehensionScope,
    InterpretationBatchProposal,
    InterpretationItem,
    InterpretationKind,
    InterpretationProposal,
)
from ads.contracts.datacard import DataCard
from ads.contracts.eda import EDAReport
from ads.contracts.evidence import (
    MeasurementBundle,
    MeasurementKind,
    MeasurementRecord,
    SubjectRef,
)
from ads.contracts.feature_experiment import FeatureExperiment
from ads.contracts.features import FeatureSpec
from ads.contracts.integration import IntegrationPlan
from ads.contracts.leakage import LeakageReport, leakage_finding_fingerprint
from ads.contracts.model_experiment import ModelExperiment
from ads.contracts.problem import ProblemDefinition
from ads.contracts.training import TrainingReport
from ads.contracts.validation import ValidationStrategy
from ads.discovery.measurements import (
    analysis_measurement_bundle,
    source_measurement_bundle,
)
from ads.intake import LoadedTable, profile_table
from ads.llm import StructuredLLM
from ads.orchestration import RunState, StageResult
from ads.pipeline.stages import (
    ABT_FRAME_KEY,
    EXECUTION_BACKEND_KEY,
    MODEL_FRAME_KEY,
    SOURCE_CARDS_KEY,
    agent_runtime_policy,
)
from ads.sandbox import ExecutionBackend, materialize_frame_copies
from ads.store import compute_artifact_id
from ads.tools import ToolRuntime
from ads.training import prepare_experiment_partition


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
                validation_failures=[failure.code for failure in result.all_failures],
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
    cards: list[DataCard] | None = None,
) -> StageResult:
    """Return a healthy or explicitly degraded brief; never raise an LLM failure."""
    spec = build_spec()
    bundle_id = compute_artifact_id(bundle)
    context = build_context(bundle, scope, cards)
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
            items=[InterpretationItem.from_proposal(item, measurements) for item in proposal.items],
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

    def augmented(state: RunState, correction: list[str] | None = None) -> StageResult:
        primary = stage(state, correction)
        plan = next(
            (item for item in primary.artifacts if isinstance(item, IntegrationPlan)),
            None,
        )
        if plan is None:
            return primary
        cards = state.blackboard.get(SOURCE_CARDS_KEY)
        if not isinstance(cards, list) or not all(isinstance(card, DataCard) for card in cards):
            raise ValueError("Intake did not place source DataCards on the run blackboard.")

        # Sensitivity before the features are chosen. The classifier is a
        # name-matcher plus checksums and misses personal columns whose names it
        # does not recognise; the agent reads what the name means. It can only
        # add, never clear a checksum, and it is given no values.
        reviewed: list[DataCard] = []
        renamed: list[str] = []
        enabled = agent_runtime_policy(state).investigator_enabled(
            "sensitivity_investigation"
        )
        for card in cards if enabled else []:
            updated, changed = investigate_sensitivity(card, llm)
            reviewed.append(updated)
            renamed.extend(f"{card.table_name}.{column}" for column in changed)
        if renamed:
            state.blackboard[SOURCE_CARDS_KEY] = reviewed
            cards = reviewed

        advisory = _interpret(
            stage_id="source_comprehension",
            scope=ComprehensionScope.SOURCE,
            bundle=source_measurement_bundle(cards, plan),
            llm=llm,
            cards=cards,
        )
        return _merge(primary, advisory)

    return augmented


def augment_eda_stage(stage, llm: StructuredLLM):
    """Add non-blocking authored investigation and measured interpretation to EDA."""

    def augmented(state: RunState, correction: list[str] | None = None) -> StageResult:
        primary = stage(state, correction)
        report = next(
            (item for item in primary.artifacts if isinstance(item, EDAReport)),
            None,
        )
        if report is None:
            return primary
        card = state.require(ArtifactType.DATA_CARD, DataCard)
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if backend is not None and not isinstance(backend, ExecutionBackend):
            raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
        exploration_artifacts = []
        exploration_names: dict[int, str] = {}
        # The fixed profiler above has already run and is what the stage is
        # judged on. This block only adds an agent-authored analysis, so the
        # switch removes work rather than protection.
        if backend is not None and agent_runtime_policy(state).investigator_enabled(
            "eda_investigation"
        ):
            frame = state.blackboard.get(ABT_FRAME_KEY)
            if not isinstance(frame, pd.DataFrame):
                raise ValueError("Integration did not place the ABT frame on the blackboard.")
            materialize_frame_copies(backend, {"abt": frame})
            investigation = investigate_eda(
                card=card,
                report=report,
                llm=llm,
                runtime=ToolRuntime.from_sources(
                    [card],
                    {"abt": frame},
                    run_id=state.run_id,
                    execution_backend=backend,
                    artifacts_dir=backend.artifacts_dir,
                ),
                budget=agent_runtime_policy(state).budget("eda_investigation"),
                code_execution_enabled=agent_runtime_policy(state).code_enabled(
                    "eda_investigation"
                ),
            )
            exploration_artifacts.extend(investigation.artifacts)
            exploration_artifacts.append(investigation.audit)
            exploration_names = {
                index: name
                for index, name in enumerate(
                    [
                        "exploratory_analysis",
                        "exploratory_measurements",
                        "exploratory_comprehension",
                    ][: len(investigation.artifacts)]
                    + ["eda_investigation_audit"]
                )
            }
        if exploration_artifacts:
            primary = _merge(
                primary,
                StageResult(
                    artifacts=exploration_artifacts,
                    names=exploration_names,
                    digest=(
                        "Agent-authored exploratory analysis completed."
                        if len(exploration_artifacts) > 1
                        else "Agent-authored exploration degraded; default EDA remains complete."
                    ),
                ),
            )
        advisory = _interpret(
            stage_id="analysis_comprehension",
            scope=ComprehensionScope.ANALYSIS,
            bundle=analysis_measurement_bundle(card, report),
            llm=llm,
        )
        return _merge(primary, advisory)

    return augmented


def augment_leakage_stage(stage, llm: StructuredLLM):
    """Let an agent request a registered challenge without weakening the floor."""

    def augmented(state: RunState, correction: list[str] | None = None) -> StageResult:
        primary = stage(state, correction)
        report_index = next(
            (
                index
                for index, artifact in enumerate(primary.artifacts)
                if isinstance(artifact, LeakageReport)
            ),
            None,
        )
        if report_index is None:
            return primary
        report = primary.artifacts[report_index]
        assert isinstance(report, LeakageReport)
        if not report.findings:
            return primary

        card = state.require(ArtifactType.DATA_CARD, DataCard)
        strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
        frame = state.blackboard.get(ABT_FRAME_KEY)
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("Integration did not place the ABT frame on the blackboard.")
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if backend is not None and not isinstance(backend, ExecutionBackend):
            raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
        data_paths = materialize_frame_copies(backend, {"abt": frame}) if backend else {}
        catalog = {leakage_finding_fingerprint(finding): finding for finding in report.findings}
        result = investigate_leakage(
            card=card,
            report=report,
            strategy=strategy,
            llm=llm,
            runtime=ToolRuntime.from_sources(
                [card],
                {"abt": frame},
                run_id=f"{state.run_id}-leakage-challenge",
                execution_backend=backend,
                artifacts_dir=backend.artifacts_dir if backend else None,
                resources={"leakage_findings": catalog, "data_paths": data_paths},
            ),
            budget=agent_runtime_policy(state).budget("leakage_investigation"),
            code_execution_enabled=agent_runtime_policy(state).code_enabled(
                "leakage_investigation"
            ),
        )
        enriched = (
            report.with_challenge(result.challenge) if result.challenge is not None else report
        )
        artifacts = list(primary.artifacts)
        artifacts[report_index] = enriched
        audit_index = len(artifacts)
        artifacts.append(result.audit)
        names = {**primary.names, audit_index: "leakage_challenge_audit"}
        digest = primary.digest or ""
        if result.challenge is not None:
            digest += f"\n\nRegistered leakage challenge: {result.challenge.result_summary}"
        elif result.degraded_reason:
            digest += f"\n\nLeakage challenge unavailable: {result.degraded_reason}"
        return StageResult(
            artifacts=artifacts,
            signals=enriched.to_quality_signals(),
            critique=primary.critique,
            names=names,
            digest=digest.strip(),
        )

    return augmented


def _model_experiment_measurements(
    experiment: ModelExperiment,
    target_column: str,
) -> MeasurementBundle:
    measurement = MeasurementRecord.create(
        source_artifact_id=compute_artifact_id(experiment),
        field_path="/score",
        kind=MeasurementKind.MODEL_EXPERIMENT,
        subjects=[SubjectRef(table="abt", column=target_column)],
        value={
            "evidence_class": experiment.evidence_class,
            "evaluation_split": experiment.evaluation_split,
            "final_holdout_used": experiment.final_holdout_used,
            "metric": experiment.metric.value,
            "score": experiment.score,
            "baseline_score": experiment.baseline_score,
            "evaluation_row_count": experiment.evaluation_row_count,
        },
    )
    return MeasurementBundle(scope="model_experiment", records=[measurement])


def _feature_experiment_artifacts(
    experiment: FeatureExperiment,
    target_column: str,
) -> tuple[MeasurementBundle, ComprehensionBrief]:
    measurement = MeasurementRecord.create(
        source_artifact_id=compute_artifact_id(experiment),
        field_path="/score",
        kind=MeasurementKind.FEATURE_EXPERIMENT,
        subjects=[
            *[SubjectRef(table="abt", column=item) for item in experiment.manifest.source_columns],
            SubjectRef(table="abt", column=target_column),
        ],
        value={
            "evidence_class": experiment.evidence_class,
            "metric": experiment.metric.value,
            "score": experiment.score,
            "baseline_score": experiment.baseline_score,
            "evaluation_split": experiment.evaluation_split,
            "final_holdout_used": experiment.final_holdout_used,
        },
    )
    bundle = MeasurementBundle(scope="feature_experiment", records=[measurement])
    proposal = InterpretationProposal(
        kind=InterpretationKind.OPEN_QUESTION,
        subjects=measurement.subjects,
        measurement_ids=[measurement.measurement_id],
        interpretation=experiment.manifest.hypothesis,
        why_it_matters=(
            "This tested feature hypothesis may change the useful signal available to "
            "the production model, but it has not been promoted."
        ),
        verification_question=experiment.manifest.verification_question,
        confidence=experiment.manifest.confidence,
    )
    brief = ComprehensionBrief(
        scope=ComprehensionScope.ANALYSIS,
        measurement_bundle_id=compute_artifact_id(bundle),
        items=[
            InterpretationItem.from_proposal(proposal, {measurement.measurement_id: measurement})
        ],
    )
    return bundle, brief


def augment_feature_pipeline_stage(stage, llm: StructuredLLM):
    """Test one authored feature hypothesis without changing the feature floor."""

    def augmented(state: RunState, correction: list[str] | None = None) -> StageResult:
        primary = stage(state, correction)
        feature_spec = next(
            (item for item in primary.artifacts if isinstance(item, FeatureSpec)), None
        )
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if feature_spec is None or backend is None:
            return primary
        if not isinstance(backend, ExecutionBackend):
            raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")
        try:
            frame = state.blackboard.get(MODEL_FRAME_KEY)
            if not isinstance(frame, pd.DataFrame):
                raise ValueError("Leakage audit did not place the model frame on the blackboard.")
            problem = state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)
            strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
            if problem.target_column is None:
                raise ValueError("Feature experiments require a supervised target.")
            partition = prepare_experiment_partition(
                frame,
                strategy,
                target_column=problem.target_column,
                excluded_columns=problem.excluded_columns,
            )
            frames = {
                "experiment_train": partition.training,
                "experiment_validation": partition.validation_features,
            }
            materialize_frame_copies(backend, frames, replace_existing=True)
            cards = [
                profile_table(
                    LoadedTable(
                        name=name,
                        frame=value,
                        source_uri="derived",
                        source_format="pandas",
                    )
                )
                for name, value in frames.items()
            ]
            investigation = investigate_features(
                partition=partition,
                problem=problem,
                feature_spec=feature_spec,
                llm=llm,
                runtime=ToolRuntime.from_sources(
                    cards,
                    frames,
                    run_id=f"{state.run_id}-feature-investigation",
                    execution_backend=backend,
                    artifacts_dir=backend.artifacts_dir,
                ),
                budget=agent_runtime_policy(state).budget("feature_investigation"),
                code_execution_enabled=agent_runtime_policy(state).code_enabled(
                    "feature_investigation"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - advisory experiment is non-blocking
            investigation = degraded_feature_investigation(f"{type(exc).__name__}: {exc}")
        if investigation.experiment is None:
            return _merge(
                primary,
                StageResult(
                    artifacts=[investigation.audit],
                    names={0: "feature_investigation_audit"},
                    digest="Feature investigation degraded; deterministic routing remains valid.",
                ),
            )
        experiment = investigation.experiment
        bundle, brief = _feature_experiment_artifacts(experiment, problem.target_column)
        return _merge(
            primary,
            StageResult(
                artifacts=[experiment, bundle, brief, investigation.audit],
                names={
                    0: "feature_experiment",
                    1: "feature_experiment_measurements",
                    2: "feature_experiment_comprehension",
                    3: "feature_investigation_audit",
                },
                digest="An authored feature hypothesis was scored on hidden labels.",
            ),
        )

    return augmented


def augment_training_stage(stage, llm: StructuredLLM):
    """Add one isolated authored experiment without changing training signals."""

    def augmented(state: RunState, correction: list[str] | None = None) -> StageResult:
        primary = stage(state, correction)
        report = next(
            (item for item in primary.artifacts if isinstance(item, TrainingReport)),
            None,
        )
        backend = state.blackboard.get(EXECUTION_BACKEND_KEY)
        if report is None or backend is None:
            return primary
        if not isinstance(backend, ExecutionBackend):
            raise TypeError("Configured execution backend does not satisfy ExecutionBackend.")

        try:
            frame = state.blackboard.get(ABT_FRAME_KEY)
            if not isinstance(frame, pd.DataFrame):
                raise ValueError("Integration did not place the ABT frame on the blackboard.")
            problem = state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)
            strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
            if problem.target_column is None:
                raise ValueError("Authored model experiments require a supervised target.")
            partition = prepare_experiment_partition(
                frame,
                strategy,
                target_column=problem.target_column,
                excluded_columns=problem.excluded_columns,
            )
            frames = {
                "experiment_train": partition.training,
                "experiment_validation": partition.validation_features,
            }
            materialize_frame_copies(backend, frames, replace_existing=True)
            cards = [
                profile_table(
                    LoadedTable(
                        name=name,
                        frame=value,
                        source_uri="derived",
                        source_format="pandas",
                    )
                )
                for name, value in frames.items()
            ]
            investigation = investigate_model(
                partition=partition,
                problem=problem,
                report=report,
                llm=llm,
                runtime=ToolRuntime.from_sources(
                    cards,
                    frames,
                    run_id=state.run_id,
                    execution_backend=backend,
                    artifacts_dir=backend.artifacts_dir,
                ),
                budget=agent_runtime_policy(state).budget("model_investigation"),
                code_execution_enabled=agent_runtime_policy(state).code_enabled(
                    "model_investigation"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - authored experiment is advisory
            investigation = degraded_model_investigation(f"{type(exc).__name__}: {exc}")

        if investigation.experiment is None:
            return _merge(
                primary,
                StageResult(
                    artifacts=[investigation.audit],
                    names={0: "model_investigation_audit"},
                    digest=(
                        "Agent-authored model experiment degraded; deterministic "
                        "training remains complete."
                    ),
                ),
            )

        experiment = investigation.experiment
        result = _merge(
            primary,
            StageResult(
                artifacts=[experiment, investigation.audit],
                names={0: "model_experiment", 1: "model_investigation_audit"},
                digest="Agent-authored model experiment was scored by the host.",
            ),
        )
        advisory = _interpret(
            stage_id="model_experiment_comprehension",
            scope=ComprehensionScope.ANALYSIS,
            bundle=_model_experiment_measurements(
                experiment,
                problem.target_column,
            ),
            llm=llm,
        )
        return _merge(result, advisory)

    return augmented


__all__ = [
    "augment_eda_stage",
    "augment_feature_pipeline_stage",
    "augment_leakage_stage",
    "augment_schema_discovery_stage",
    "augment_training_stage",
]
