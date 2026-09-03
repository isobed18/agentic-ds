"""Deterministic stage adapters for :mod:`ads.orchestration`.

The orchestration protocol persists typed artifacts, while pandas frames remain
process-local payloads.  This module keeps those frames under private blackboard
keys and makes every durable decision/report cross the stage boundary as an
artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import ClassVar, Final

import pandas as pd

from ads.agents.runtime import DEFAULT_AGENT_RUNTIME_POLICY, AgentRuntimePolicy
from ads.contracts.base import Artifact, ArtifactType
from ads.contracts.datacard import DataCard, Sensitivity
from ads.contracts.dataflow import FoldSelection, RowSelection, SplitManifest, TableAsset
from ads.contracts.features import FeatureSpec
from ads.contracts.gates import QualitySignals
from ads.contracts.integration import (
    IntegrationPlan,
    IntegrationTrial,
    integration_plan_fingerprint,
)
from ads.contracts.leakage import LeakageReport
from ads.contracts.problem import ProblemDefinition
from ads.contracts.reporting import DecisionAuthority, DecisionRecord, EvaluationReport
from ads.contracts.training import TrainingReport
from ads.contracts.validation import (
    ValidationStrategy,
    ValidationTrial,
    validation_strategy_fingerprint,
)
from ads.dataflow import load_table_asset, persist_table_asset
from ads.discovery import audit_leakage
from ads.ds_toolkit import build_preprocessor
from ads.eda import profile_for_eda
from ads.intake import LoadedTable, load_directory, profile_table, profile_tables
from ads.integration import execute_plan
from ads.orchestration import RunState, StageResult
from ads.reporting import build_evaluation_report, render_markdown
from ads.sandbox import ExecutionBackend
from ads.splitting import describe_split, make_splitter, split_holdout
from ads.store import compute_artifact_id, register_artifact_type
from ads.training import default_candidates, train_candidates

SOURCE_PATH_KEY: Final = "pipeline.source_path"
INTEGRATION_PLAN_KEY: Final = "pipeline.integration_plan"
PROBLEM_KEY: Final = "pipeline.problem_definition"
STRATEGY_KEY: Final = "pipeline.validation_strategy"
CANDIDATE_LIMIT_KEY: Final = "pipeline.candidate_limit"
VALIDATION_FOLDS_KEY: Final = "pipeline.validation_folds"
EXECUTION_BACKEND_KEY: Final = "pipeline.execution_backend"
AGENT_RUNTIME_POLICY_KEY: Final = "pipeline.agent_runtime_policy"
#: stage_id -> instructions a person addressed to that stage's agent.
#: Distinct from a retry correction: a correction is derived by the orchestrator
#: from a specific failed attempt and dies with it, while a directive is what a
#: human asked for and applies every time that stage runs, including the first.
STAGE_DIRECTIVES_KEY: Final = "pipeline.stage_directives"

LOADED_TABLES_KEY: Final = "pipeline.loaded_tables"
SOURCE_FRAMES_KEY: Final = "pipeline.source_frames"
SOURCE_CARDS_KEY: Final = "pipeline.source_cards"
ABT_FRAME_KEY: Final = "pipeline.abt_frame"
MODEL_FRAME_KEY: Final = "pipeline.model_frame"
SPLIT_DIAGNOSTICS_KEY: Final = "pipeline.split_diagnostics"
INTEGRATION_GRAIN_PRESERVED_KEY: Final = "pipeline.integration_grain_preserved"
DROPPED_FEATURES_KEY: Final = "pipeline.dropped_features"
TRAINING_FRAME_COLUMNS_KEY: Final = "pipeline.training_frame_columns"
FEATURE_SPEC_KEY: Final = "pipeline.feature_spec"
FINAL_MARKDOWN_KEY: Final = "pipeline.final_markdown"
#: #200: the language the run was started in. The report is rendered on a
#: background worker, outside any request, where the per-request language
#: ContextVar is unset and falls back to the "tr" default -- so an English UI
#: still got a Turkish report skeleton, and the Turkish half was an accident of
#: that default rather than a response to the language selection. Capturing the
#: chosen language at start and binding it here lets the reporting stage render
#: in the language actually chosen.
RUN_LANGUAGE_KEY: Final = "pipeline.run_language"

_DROP_FEATURE = re.compile(r"^\s*drop_feature\s*:\s*([^#]+)", re.IGNORECASE)


class FinalReport(Artifact):
    """Rendered handoff paired with the exact evaluation artifact it presents."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.FINAL_REPORT
    schema_version: ClassVar[str] = "1"

    evaluation_artifact_id: str
    markdown: str
    run_seed: int | None = None

    def summary(self) -> dict[str, object]:
        return {
            "evaluation_artifact_id": self.evaluation_artifact_id,
            "n_characters": len(self.markdown),
            "run_seed": self.run_seed,
        }


# FinalReport is defined here rather than in contracts/, so the store cannot
# import it without a cycle. Registering at import time keeps `load()` able to
# resolve it by type like every other artifact.
register_artifact_type(FinalReport)


def configure_pipeline_state(
    state: RunState,
    *,
    source_path: str | Path,
    integration_plan: IntegrationPlan,
    problem: ProblemDefinition,
    validation_strategy: ValidationStrategy,
    candidate_limit: int | None = None,
) -> None:
    """Attach approved run inputs and deterministic execution options."""
    if candidate_limit is not None and candidate_limit < 1:
        raise ValueError("candidate_limit must be positive when supplied")
    state.blackboard.update(
        {
            SOURCE_PATH_KEY: Path(source_path),
            INTEGRATION_PLAN_KEY: integration_plan,
            PROBLEM_KEY: problem,
            STRATEGY_KEY: validation_strategy,
            CANDIDATE_LIMIT_KEY: candidate_limit,
        }
    )


def configure_full_pipeline_state(
    state: RunState,
    *,
    source_path: str | Path,
    candidate_limit: int | None = None,
    validation_folds: int = 3,
    execution_backend: ExecutionBackend | None = None,
    agent_runtime_policy: AgentRuntimePolicy | None = None,
) -> None:
    """Attach source and execution options for the agent-backed workflow."""
    if candidate_limit is not None and candidate_limit < 1:
        raise ValueError("candidate_limit must be positive when supplied")
    if not 2 <= validation_folds <= 20:
        raise ValueError("validation_folds must be between 2 and 20")
    state.blackboard.update(
        {
            SOURCE_PATH_KEY: Path(source_path),
            CANDIDATE_LIMIT_KEY: candidate_limit,
            VALIDATION_FOLDS_KEY: validation_folds,
        }
    )
    if execution_backend is not None:
        state.blackboard[EXECUTION_BACKEND_KEY] = execution_backend
    state.blackboard[AGENT_RUNTIME_POLICY_KEY] = (
        agent_runtime_policy or DEFAULT_AGENT_RUNTIME_POLICY
    )


def agent_runtime_policy(state: RunState) -> AgentRuntimePolicy:
    value = state.blackboard.get(AGENT_RUNTIME_POLICY_KEY, DEFAULT_AGENT_RUNTIME_POLICY)
    if not isinstance(value, AgentRuntimePolicy):
        raise TypeError("pipeline.agent_runtime_policy must be an AgentRuntimePolicy.")
    return value


def _frame(state: RunState, key: str) -> pd.DataFrame:
    value = state.blackboard.get(key)
    if not isinstance(value, pd.DataFrame):
        raise ValueError(f"RunState.blackboard[{key!r}] does not contain a DataFrame.")
    return value


def _promoted_document_tables(state: RunState) -> list[LoadedTable]:
    """Human-reviewed PDF table candidates already promoted for this run.

    A promoted candidate is a `TableAsset` in the run's own artifact store
    (`promote_reviewed_document_tables`, tagged `stage_exec_id=
    "document-table-promotion"`) -- promotion never touches the uploaded
    source directory, because a source with execution history is immutable
    (#111: mutating it would silently change what every other run reading the
    same source sees). Intake is where every table a run trains on is
    assembled from disk, so it is where these have to be merged in instead;
    nothing downstream needs to know a table came from a PDF rather than a
    file next to it.
    """
    promoted: list[LoadedTable] = []
    for ref in state.store.list(state.run_id, artifact_type=ArtifactType.TABLE_ASSET):
        if ref.stage_exec_id != "document-table-promotion":
            continue
        _, frame = load_table_asset(state.store, ref.artifact_id)
        promoted.append(
            LoadedTable(
                name=ref.name or ref.artifact_id,
                frame=frame,
                source_uri=f"artifact://{ref.artifact_id}",
                source_format="promoted-document-table",
            )
        )
    return promoted


def intake_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Load/profile source files and persist any pre-approved run decisions."""
    del correction
    source_path = state.blackboard.get(SOURCE_PATH_KEY)
    if not isinstance(source_path, Path):
        raise ValueError(f"RunState.blackboard[{SOURCE_PATH_KEY!r}] must contain a Path.")
    loaded = load_directory(source_path)
    loaded.extend(_promoted_document_tables(state))
    cards = profile_tables(loaded)
    state.blackboard[LOADED_TABLES_KEY] = loaded
    state.blackboard[SOURCE_FRAMES_KEY] = {table.name: table.frame for table in loaded}
    state.blackboard[SOURCE_CARDS_KEY] = cards

    artifacts: list[Artifact] = [*cards]
    names = {index: card.table_name for index, card in enumerate(cards)}
    configured = (
        (INTEGRATION_PLAN_KEY, IntegrationPlan, "approved_integration_plan"),
        (PROBLEM_KEY, ProblemDefinition, "approved_problem_definition"),
        (STRATEGY_KEY, ValidationStrategy, "approved_validation_strategy"),
    )
    for key, expected, name in configured:
        value = state.blackboard.get(key)
        if value is None:
            continue
        if not isinstance(value, expected):
            raise ValueError(f"RunState.blackboard[{key!r}] must contain {expected.__name__}.")
        names[len(artifacts)] = name
        artifacts.append(value)
    return StageResult(
        artifacts=artifacts,
        names=names,
        signals=QualitySignals(
            n_rows=sum(card.n_rows for card in cards),
            # Deliberately not counted here. `pii_columns_in_context` had no
            # producer at all, and the obvious fix -- count every PII column at
            # intake -- makes the gate fire on every dataset that contains a
            # name, which is most of them. The rule's own message says these
            # columns "would be sent to the model", and that is not what having
            # one means: PII columns are excluded from the feature pool by
            # `discovery.support`, and the model only ever sees a column name.
            #
            # The condition worth stopping on is the one that should be
            # impossible: a PII column surviving into the features. That is
            # measured where the features are chosen, not here.
        ),
    )


def integration_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Execute the approved plan, verify its grain, and profile the resulting ABT."""
    del correction
    frames = state.blackboard.get(SOURCE_FRAMES_KEY)
    if not isinstance(frames, dict) or not all(
        isinstance(name, str) and isinstance(frame, pd.DataFrame) for name, frame in frames.items()
    ):
        raise ValueError("Intake did not place source frames on the run blackboard.")
    plan = state.require(ArtifactType.INTEGRATION_PLAN, IntegrationPlan)
    if plan.trial_artifact_id is not None:
        trial = state.require(ArtifactType.INTEGRATION_TRIAL, IntegrationTrial)
        proposal = plan.to_proposal()
        if compute_artifact_id(trial) != plan.trial_artifact_id:
            raise ValueError("Integration plan points to a different trial artifact.")
        if trial.plan_fingerprint != integration_plan_fingerprint(proposal):
            raise ValueError("Integration plan semantics changed after its deterministic trial.")
        if not trial.grain_preserved:
            raise ValueError("Integration plan references a failed deterministic grain trial.")
    result = execute_plan(plan, frames)
    state.blackboard[INTEGRATION_GRAIN_PRESERVED_KEY] = result.grain_preserved
    state.blackboard[ABT_FRAME_KEY] = result.frame
    state.blackboard[MODEL_FRAME_KEY] = result.frame.copy()
    abt_card = profile_table(
        LoadedTable(name="abt", frame=result.frame, source_uri="derived", source_format="duckdb")
    )
    table_asset, _ = persist_table_asset(
        state.store,
        result.frame,
        run_id=state.run_id,
        producer_component_id="integrate-data",
        stage_exec_id="integration",
        name="integrated_table",
        source_artifact_ids=[compute_artifact_id(plan)],
        transformation="execute_approved_integration_plan",
    )
    return StageResult(
        artifacts=[abt_card, table_asset],
        names={0: "abt", 1: "integrated_table"},
        signals=QualitySignals(n_rows=len(result.frame)),
    )


def profiling_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Run deterministic EDA over the integrated analytical base table."""
    del correction
    frame = _frame(state, ABT_FRAME_KEY)
    card = state.require(ArtifactType.DATA_CARD, DataCard)
    problem = state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)
    report = profile_for_eda(card, frame, problem)
    n_features = max(len(report.model_eligible_columns), 1)
    return StageResult(
        artifacts=[report],
        names={0: "eda"},
        signals=QualitySignals(
            n_rows=len(frame),
            rows_per_feature=len(frame) / n_features,
        ),
    )


def _drop_columns_from_correction(
    correction: list[str] | None,
    *,
    frame: pd.DataFrame,
    target_column: str | None,
) -> set[str]:
    drops: set[str] = set()
    for instruction in correction or ():
        match = _DROP_FEATURE.match(instruction)
        if match is None:
            continue
        column = match.group(1).strip()
        if column == target_column:
            raise ValueError("A leakage correction cannot drop the configured target.")
        if column not in frame.columns:
            raise ValueError(f"Leakage correction names unknown feature {column!r}.")
        drops.add(column)
    return drops


def _current_problem(state: RunState) -> ProblemDefinition:
    return state.require(ArtifactType.PROBLEM_DEFINITION, ProblemDefinition)


def leakage_audit_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Measure leakage and apply mechanical ``drop_feature:`` retry corrections."""
    abt = _frame(state, ABT_FRAME_KEY)
    problem = _current_problem(state)
    strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
    plan = state.require(ArtifactType.INTEGRATION_PLAN, IntegrationPlan)

    prior_drops = set(state.blackboard.get(DROPPED_FEATURES_KEY, ()))
    requested_drops = _drop_columns_from_correction(
        correction,
        frame=abt,
        target_column=problem.target_column,
    )
    dropped = prior_drops | requested_drops | set(problem.excluded_columns)
    state.blackboard[DROPPED_FEATURES_KEY] = frozenset(dropped)
    model_frame = abt.drop(columns=sorted(dropped), errors="ignore").copy()
    state.blackboard[MODEL_FRAME_KEY] = model_frame

    corrected_problem = problem.model_copy(update={"excluded_columns": sorted(dropped)})
    card = state.require(ArtifactType.DATA_CARD, DataCard)
    proposal = plan.to_proposal()
    report = audit_leakage(
        card,
        abt,
        target_column=corrected_problem.target_column,
        task_type=corrected_problem.task_type,
        validation_strategy=strategy,
        integration_plan=proposal,
        excluded_columns=frozenset(dropped),
    )
    return StageResult(
        artifacts=[report, corrected_problem],
        names={0: "leakage_current", 1: "problem_current"},
        signals=report.to_quality_signals(),
    )


def splitting_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Build the configured split and expose its usability measurements to the gate."""
    del correction
    frame = _frame(state, MODEL_FRAME_KEY)
    problem = _current_problem(state)
    strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
    if strategy.trial_artifact_id is not None:
        trial = state.require(ArtifactType.VALIDATION_TRIAL, ValidationTrial)
        if compute_artifact_id(trial) != strategy.trial_artifact_id:
            raise ValueError("ValidationStrategy points to a different trial artifact.")
        if trial.proposal_fingerprint != validation_strategy_fingerprint(strategy.to_proposal()):
            raise ValueError("ValidationStrategy semantics changed after its trial.")
        if not trial.passed:
            raise ValueError("ValidationStrategy references a failed split trial.")
    if problem.target_column is None:
        labeled = frame
    else:
        labeled = frame.loc[frame[problem.target_column].notna()]
    diagnostics = describe_split(labeled, strategy)
    state.blackboard[SPLIT_DIAGNOSTICS_KEY] = diagnostics
    table_asset = state.require(ArtifactType.TABLE_ASSET, TableAsset)
    manifest = _build_split_manifest(
        labeled,
        strategy,
        target_column=problem.target_column or "",
        table_asset=table_asset,
        table_frame=frame,
    )
    return StageResult(
        artifacts=[manifest],
        names={0: "split_manifest"},
        signals=diagnostics.to_quality_signals(),
    )


def _row_selection(positions: list[int]) -> RowSelection:
    normalized = [int(position) for position in positions]
    fingerprint = hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()
    return RowSelection(
        count=len(normalized),
        fingerprint=fingerprint,
        positions=normalized,
    )


def _build_split_manifest(
    frame: pd.DataFrame,
    strategy: ValidationStrategy,
    *,
    target_column: str,
    table_asset: TableAsset,
    table_frame: pd.DataFrame,
) -> SplitManifest:
    if not frame.index.is_unique or not table_frame.index.is_unique:
        raise ValueError("SplitManifest requires a unique integrated-table row index.")
    index_positions = {index: position for position, index in enumerate(table_frame.index)}

    def positions(index: pd.Index) -> list[int]:
        try:
            return [index_positions[value] for value in index]
        except KeyError as exc:
            raise ValueError("split references a row outside the integrated table") from exc

    outer_train, holdout = split_holdout(
        frame,
        strategy,
        target_column=target_column,
    )
    splitter = make_splitter(
        strategy,
        outer_train,
        target_column=target_column,
    )
    folds = [
        FoldSelection(
            fold=fold,
            train=_row_selection(positions(train_index)),
            validation=_row_selection(positions(validation_index)),
        )
        for fold, (train_index, validation_index) in enumerate(splitter.iter_folds(outer_train))
    ]
    return SplitManifest(
        table_asset_id=compute_artifact_id(table_asset),
        table_fingerprint=table_asset.fingerprint,
        validation_strategy_artifact_id=compute_artifact_id(strategy),
        target_column=target_column,
        outer_train=_row_selection(positions(outer_train.index)),
        holdout=_row_selection(positions(holdout.index)),
        folds=folds,
    )


def _excluded_feature_columns(state: RunState, problem, card) -> set[str]:
    """Columns kept out of the model's features, and why each one is.

    Two sources: what the human excluded, and what the source profile classified
    as personal. The second is the one that was missing -- `discovery.support`
    drops personal columns from the *candidate* count, but the feature spec is
    built from a freshly profiled model frame that never sees the source
    classification. Measured on the sample data, `full_name`, `email_address`
    and `license_no` were reaching the model's features as a result.

    Shared by the stage that builds the spec and the stage that re-derives it to
    check consistency, so the two cannot disagree about what was excluded.
    """
    personal = {
        column.name
        for source in (state.blackboard.get(SOURCE_CARDS_KEY) or [])
        for column in source.columns
        if column.sensitivity is Sensitivity.PII
    }
    return (set(problem.excluded_columns) | personal) & set(card.column_names)


def feature_pipeline_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Declare the mandatory preprocessing floor without fitting global statistics."""
    del correction
    frame = _frame(state, MODEL_FRAME_KEY)
    problem = _current_problem(state)
    if problem.target_column is None:
        raise ValueError("Feature preparation requires a supervised target column.")
    card = profile_table(
        LoadedTable(
            name="model_frame",
            frame=frame,
            source_uri="derived",
            source_format="pandas",
        )
    )
    # Columns the source classified as personal are excluded here, which is the
    # only place it actually takes effect. `discovery.support` drops them from
    # the *candidate* count, but the feature spec is built from a freshly
    # profiled model frame that never sees the source classification -- so
    # `full_name`, `email_address` and `license_no` were reaching the model's
    # features on the sample data, measured rather than assumed.
    excluded = _excluded_feature_columns(state, problem, card)
    spec = FeatureSpec.from_card(
        card,
        target_column=problem.target_column,
        excluded_columns=excluded,
    )
    state.blackboard[FEATURE_SPEC_KEY] = spec

    # The condition `pii_egress_requested` is actually worth stopping on: a
    # column classified personal that survived into the model's features.
    # `discovery.support` excludes them, so a non-zero count here means the
    # exclusion was bypassed -- by a human override, or by a bug. Either way a
    # person should see it before a model is trained on it.
    #
    # Counting every PII column at intake instead would fire on any dataset
    # containing a name, which is most of them, and would say "would be sent to
    # the model" about a column the model never receives.
    feature_columns = {
        *spec.numeric_columns,
        *spec.categorical_columns,
        *spec.datetime_columns,
    }
    personal_in_features = sum(
        1
        for source in (state.blackboard.get(SOURCE_CARDS_KEY) or [])
        for column in source.columns
        if column.sensitivity is Sensitivity.PII and column.name in feature_columns
    )

    return StageResult(
        artifacts=[spec],
        names={0: "feature_spec"},
        signals=QualitySignals(
            n_rows=len(frame),
            pii_columns_in_context=personal_in_features,
            rows_per_feature=(
                len(frame)
                / max(
                    len(spec.numeric_columns)
                    + len(spec.categorical_columns)
                    + len(spec.datetime_columns),
                    1,
                )
            ),
        ),
    )


def training_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Fit the fixed candidate menu with fold-local preprocessing."""
    del correction
    frame = _frame(state, MODEL_FRAME_KEY)
    problem = _current_problem(state)
    strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
    if problem.target_column is None:
        raise ValueError("Deterministic training requires a supervised target column.")
    card = profile_table(
        LoadedTable(
            name="model_frame",
            frame=frame,
            source_uri="derived",
            source_format="pandas",
        )
    )
    feature_spec = state.require(ArtifactType.FEATURE_SPEC, FeatureSpec)
    expected_spec = FeatureSpec.from_card(
        card,
        target_column=problem.target_column,
        excluded_columns=_excluded_feature_columns(state, problem, card),
    )
    if feature_spec.model_dump(exclude={"created_at"}) != expected_spec.model_dump(
        exclude={"created_at"}
    ):
        raise ValueError("FeatureSpec does not match the current model-frame schema.")
    table_asset = state.require(ArtifactType.TABLE_ASSET, TableAsset)
    split_manifest = state.require(ArtifactType.SPLIT_MANIFEST, SplitManifest)
    if split_manifest.table_asset_id != compute_artifact_id(table_asset):
        raise ValueError("SplitManifest points to a different integrated TableAsset.")
    if split_manifest.table_fingerprint != table_asset.fingerprint:
        raise ValueError("SplitManifest table fingerprint does not match the integrated table.")
    if split_manifest.validation_strategy_artifact_id != compute_artifact_id(strategy):
        raise ValueError("SplitManifest points to a different validation strategy.")
    if split_manifest.target_column != problem.target_column:
        raise ValueError("SplitManifest target does not match the confirmed problem.")
    labeled = frame.loc[frame[problem.target_column].notna()]
    expected_manifest = _build_split_manifest(
        labeled,
        strategy,
        target_column=problem.target_column,
        table_asset=table_asset,
        table_frame=frame,
    )
    if split_manifest.model_dump(exclude={"created_at"}) != expected_manifest.model_dump(
        exclude={"created_at"}
    ):
        raise ValueError(
            "SplitManifest selections do not match the configured deterministic split."
        )
    excluded_present = set(problem.excluded_columns) & set(card.column_names)
    candidates = default_candidates(problem.task_type)
    candidate_limit = state.blackboard.get(CANDIDATE_LIMIT_KEY)
    if candidate_limit is not None:
        if not isinstance(candidate_limit, int) or candidate_limit < 1:
            raise ValueError("pipeline.candidate_limit must be a positive integer or None.")
        candidates = candidates[:candidate_limit]
    state.blackboard[TRAINING_FRAME_COLUMNS_KEY] = tuple(frame.columns)
    report = train_candidates(
        frame,
        strategy,
        lambda: build_preprocessor(
            card,
            target_column=problem.target_column or "",
            excluded_columns=excluded_present,
        ),
        candidates,
        target_column=problem.target_column,
        task_type=problem.task_type,
        primary_metric=problem.primary_metric,
        store=state.store,
        run_id=state.run_id,
    )
    return StageResult(
        artifacts=[report],
        names={0: "training_report"},
        signals=report.to_quality_signals(),
    )


def _build_evaluation(state: RunState) -> EvaluationReport:
    training = state.require(ArtifactType.TRAINED_MODEL, TrainingReport)
    leakage = state.require(ArtifactType.LEAKAGE_REPORT, LeakageReport)
    strategy = state.require(ArtifactType.VALIDATION_STRATEGY, ValidationStrategy)
    problem = _current_problem(state)
    human_decisions = [
        DecisionRecord(
            stage=str(item["stage_id"]),
            decision=f"Human chose {item['decision']!r} at this gate.",
            authority=DecisionAuthority.HUMAN,
            rationale=(
                "Instructions: " + "; ".join(str(value) for value in item["instructions"])
                if item.get("instructions")
                else "The decision carried no free-text instructions."
            ),
        )
        for item in state.blackboard.get("human_decisions", [])
    ]
    return build_evaluation_report(
        training,
        leakage,
        strategy,
        problem,
        decisions=human_decisions,
        gate_decisions=[
            attempt.decision for attempt in state.attempts if attempt.decision is not None
        ],
        prior_leakage_reports=state.all_of(ArtifactType.LEAKAGE_REPORT, LeakageReport),
    )


def evaluation_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Assemble measured model, split, leakage, and decision evidence."""
    del correction
    report = _build_evaluation(state)
    training = state.require(ArtifactType.TRAINED_MODEL, TrainingReport)
    return StageResult(
        artifacts=[report],
        names={0: "evaluation_report"},
        signals=training.to_quality_signals(),
    )


def reporting_stage(state: RunState, correction: list[str] | None = None) -> StageResult:
    """Refresh complete gate history and render the final Markdown handoff."""
    del correction
    from contextlib import nullcontext

    from ads.api import i18n

    evaluation = _build_evaluation(state)
    # #200: render in the language the run was started in, not the worker's
    # ContextVar default. Without this the report followed "tr" by accident
    # regardless of the UI's language, and could never reflect an English UI.
    # Only bind when a language was actually captured: an uncaptured run (e.g. a
    # direct pipeline invocation) must keep whatever language is ambient rather
    # than being forced to the default.
    language = state.blackboard.get(RUN_LANGUAGE_KEY)
    with (i18n.using(language) if language else nullcontext()):
        markdown = render_markdown(evaluation).rstrip()
    if state.run_seed is not None:
        markdown += f"\n\n## Reproducibility\n\nRun seed: `{state.run_seed}`"
    state.blackboard[FINAL_MARKDOWN_KEY] = markdown
    final = FinalReport(
        evaluation_artifact_id=compute_artifact_id(evaluation),
        markdown=markdown,
        run_seed=state.run_seed,
    )
    return StageResult(
        artifacts=[evaluation, final],
        names={0: "evaluation_report_final", 1: "final_report"},
    )


__all__ = [
    "ABT_FRAME_KEY",
    "AGENT_RUNTIME_POLICY_KEY",
    "STAGE_DIRECTIVES_KEY",
    "DROPPED_FEATURES_KEY",
    "EXECUTION_BACKEND_KEY",
    "FEATURE_SPEC_KEY",
    "FINAL_MARKDOWN_KEY",
    "RUN_LANGUAGE_KEY",
    "INTEGRATION_GRAIN_PRESERVED_KEY",
    "FinalReport",
    "MODEL_FRAME_KEY",
    "SPLIT_DIAGNOSTICS_KEY",
    "TRAINING_FRAME_COLUMNS_KEY",
    "configure_pipeline_state",
    "agent_runtime_policy",
    "configure_full_pipeline_state",
    "evaluation_stage",
    "feature_pipeline_stage",
    "intake_stage",
    "integration_stage",
    "leakage_audit_stage",
    "profiling_stage",
    "reporting_stage",
    "splitting_stage",
    "training_stage",
]
