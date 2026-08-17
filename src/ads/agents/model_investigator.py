"""Bounded tool loop for an agent-authored, host-scored model experiment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from ads.agents.base import AgentSpec
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.exploration import InvestigationAction, InvestigationActionKind
from ads.contracts.gates import PermissionTier
from ads.contracts.model_experiment import ModelExperiment, ModelExperimentManifest
from ads.contracts.problem import ProblemDefinition
from ads.contracts.training import TrainingReport
from ads.llm import LARGE, StructuredLLM
from ads.skills import render_skills, select_skills
from ads.store import compute_artifact_id
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry
from ads.training.experiments import (
    ROW_ID_COLUMN,
    ExperimentPartition,
    measure_experiment_predictions,
)

MAX_TURNS = 10
MAX_TRANSCRIPT_CHARS = 24_000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MANIFEST_NAME = "model_experiment.json"

_TOOL_HELP = """\
- column_profile/value_counts/cardinality/null_rate: use table experiment_train or
  experiment_validation and a column name.
- correlation: use experiment_train only; validation labels are not available.
- execute_python: write both /artifacts/model_experiment.json and the CSV named by its
  predictions_ref. pandas, NumPy and scikit-learn are installed in the isolated runtime.
"""

SYSTEM_PROMPT = f"""\
You are a local model investigator. The mandatory deterministic candidate menu has already run.
Try one consequential model or feature approach that menu did not cover. Investigate with tools,
then write and execute Python against the supplied development copies.

/data/experiment_train.csv contains the target and opaque row ids.
/data/experiment_validation.csv contains the same feature shape but NO target. It is an inner
development validation set, not the final holdout. Fit only on experiment_train and write exactly
one prediction for every validation row. Never infer, reconstruct, or request validation labels.

To publish, one execute_python call must create /artifacts/{MANIFEST_NAME} and its referenced CSV.
The manifest has no score field. The host validates row ids and measures the score against labels
that were never mounted. Your declared feature list is provenance you authored, not evidence that
the runtime inspected those columns. The result remains exploratory and cannot affect a gate or
replace the deterministic winner. If the experiment is not supportable, abandon honestly.
"""


@dataclass(frozen=True)
class ModelInvestigationResult:
    experiment: ModelExperiment | None
    audit: AgentAudit
    degraded_reason: str | None = None


def build_spec() -> AgentSpec[InvestigationAction]:
    return AgentSpec(
        id="model_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=InvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=build_tool_registry().ids(),
        max_tool_tier=PermissionTier.EXECUTE,
        narration_fields=frozenset({"reason"}),
    )


def _context(
    partition: ExperimentPartition,
    problem: ProblemDefinition,
    report: TrainingReport,
    skill_text: str,
) -> str:
    columns = [
        column
        for column in partition.validation_features.columns
        if column != ROW_ID_COLUMN
    ]
    deterministic = {
        "task_type": problem.task_type.value,
        "target_column": partition.target_column,
        "metric_measured_by_host": report.primary_metric.value,
        "training_rows": len(partition.training),
        "validation_rows": len(partition.validation_features),
        "available_feature_columns": columns,
        "deterministic_candidates_already_run": [
            {
                "candidate": item.display_name,
                "estimator_class": item.estimator_class,
                "is_baseline": item.is_baseline,
            }
            for item in report.results
        ],
    }
    return (
        "## Development protocol\n"
        + json.dumps(deterministic, separators=(",", ":"), default=str)
        + "\n\n## Available tools\n"
        + _TOOL_HELP
        + "\n## Required manifest schema\n"
        + json.dumps(
            ModelExperimentManifest.model_json_schema(),
            separators=(",", ":"),
            default=str,
        )
        + ("\n\n## Applicable skills\n" + skill_text if skill_text else "")
    )


def _audit(
    *,
    spec: AgentSpec[InvestigationAction],
    model: str,
    attempts: int,
    accepted: bool,
    tools: list[str],
    failures: list[str],
    latency_s: float,
    skills_used: list[str],
) -> AgentAudit:
    return AgentAudit(
        stage_id="model_investigation",
        agent_id=spec.id,
        output_contract=ModelExperimentManifest.__name__,
        panel_size=1,
        valid_members=int(accepted),
        agreement=None,
        verbatim_agreement=None,
        allowed_tools=sorted(spec.allowed_tools),
        evidence_tools=tools,
        skills_used=skills_used,
        validator_count=5,
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


def _degraded(
    spec: AgentSpec[InvestigationAction],
    reason: str,
    *,
    model: str | None = None,
    attempts: int = 0,
    tools: list[str] | None = None,
    failures: list[str] | None = None,
    latency_s: float = 0.0,
    skills_used: list[str] | None = None,
) -> ModelInvestigationResult:
    return ModelInvestigationResult(
        experiment=None,
        audit=_audit(
            spec=spec,
            model=model or spec.profile.name,
            attempts=attempts,
            accepted=False,
            tools=tools or [],
            failures=failures or [reason],
            latency_s=latency_s,
            skills_used=skills_used or [],
        ),
        degraded_reason=reason,
    )


def degraded_model_investigation(reason: str) -> ModelInvestigationResult:
    """Create an honest audit when preparation fails before the tool loop starts."""
    spec = build_spec()
    return _degraded(
        spec,
        reason[:500],
        failures=[f"preparation_error:{reason[:200]}"],
    )


def _resolve_output(root: Path, artifact_ref: str) -> Path:
    relative = artifact_ref.removeprefix("artifact://")
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if resolved_root not in candidate.parents or not candidate.is_file():
        raise ValueError("Experiment output does not resolve inside the artifact directory.")
    if candidate.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("Experiment output exceeds the 16 MB limit.")
    return candidate


def investigate_model(
    *,
    partition: ExperimentPartition,
    problem: ProblemDefinition,
    report: TrainingReport,
    llm: StructuredLLM,
    runtime: ToolRuntime,
) -> ModelInvestigationResult:
    """Run one advisory experiment; every failure preserves deterministic training."""
    spec = build_spec()
    skills = select_skills("model_investigation", list(runtime.cards.values()))
    skill_ids = [skill.skill_id for skill in skills]
    if runtime.backend() is None or not runtime.execution_available():
        return _degraded(spec, "sandbox_unavailable", skills_used=skill_ids)

    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tool_calls: list[str] = []
    produced: dict[str, tuple[str, int | None, frozenset[str]]] = {}
    failures: list[str] = []
    attempts = 0
    latency = 0.0
    model = spec.profile.name
    base_prompt = _context(
        partition,
        problem,
        report,
        render_skills(skills) if skills else "",
    )

    try:
        for turn in range(1, MAX_TURNS + 1):
            attempts += 1
            history = "\n\n".join(transcript)[-MAX_TRANSCRIPT_CHARS:]
            prompt = base_prompt + (
                "\n\n## Investigation transcript\n" + history if history else ""
            )
            response = llm.generate_structured(
                system=spec.system_prompt,
                prompt=prompt,
                json_schema=InvestigationAction.model_json_schema(),
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
                action = InvestigationAction.model_validate(response.parsed)
            except ValidationError as exc:
                failures.append("invalid_action")
                transcript.append(f"Turn {turn} rejected: {exc}.")
                continue

            if action.action is InvestigationActionKind.ABANDON:
                return _degraded(
                    spec,
                    action.reason,
                    model=model,
                    attempts=attempts,
                    tools=tool_calls,
                    failures=[*failures, "agent_abandoned"],
                    latency_s=latency,
                    skills_used=skill_ids,
                )

            if action.action is InvestigationActionKind.CALL_TOOL:
                assert action.tool_id is not None
                try:
                    result = broker.invoke(
                        spec,
                        action.tool_id,
                        runtime,
                        **action.arguments,
                    )
                except ToolError as exc:
                    failures.append(f"tool_error:{action.tool_id}")
                    transcript.append(
                        f"Turn {turn} tool {action.tool_id} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                tool_calls.append(action.tool_id)
                rendered = json.dumps(result.data, default=str, separators=(",", ":"))
                transcript.append(
                    f"Turn {turn} tool {action.tool_id} result: {result.summary}; "
                    f"data={rendered[:8_000]}"
                )
                if action.tool_id == "execute_python":
                    code = action.arguments.get("code")
                    if isinstance(code, str):
                        batch = frozenset(result.artifact_refs)
                        for ref in batch:
                            produced[ref] = (
                                code,
                                result.data.get("execution_count"),
                                batch,
                            )
                continue

            assert action.action is InvestigationActionKind.FINISH
            assert action.artifact_ref is not None
            if action.artifact_ref not in produced:
                failures.append("unexecuted_manifest_ref")
                transcript.append(
                    f"Turn {turn} rejected: {action.artifact_ref} was not created by "
                    "this investigation."
                )
                continue
            code, execution_count, batch = produced[action.artifact_ref]
            try:
                root = runtime.execution_artifacts_dir()
                manifest_path = _resolve_output(root, action.artifact_ref)
                manifest = ModelExperimentManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
                if manifest.predictions_ref not in batch:
                    raise ValueError(
                        "Manifest and predictions must be created by the same execution."
                    )
                predictions_path = _resolve_output(root, manifest.predictions_ref)
                available = set(partition.validation_features.columns) - {ROW_ID_COLUMN}
                if not set(manifest.declared_feature_columns) <= available:
                    raise ValueError("Manifest declares unavailable feature columns.")
                predictions = pd.read_csv(predictions_path)
                score, baseline = measure_experiment_predictions(
                    predictions,
                    partition,
                    task_type=problem.task_type,
                    metric=report.primary_metric,
                )
                experiment = ModelExperiment(
                    source_training_artifact_id=compute_artifact_id(report),
                    task_type=problem.task_type,
                    metric=report.primary_metric,
                    score=score,
                    baseline_score=baseline,
                    evaluation_row_count=len(partition.validation_targets),
                    manifest=manifest,
                    code=code,
                    code_hash=hashlib.sha256(code.encode()).hexdigest(),
                    predictions_hash=hashlib.sha256(
                        predictions_path.read_bytes()
                    ).hexdigest(),
                    execution_count=execution_count,
                    tool_calls=tool_calls,
                    manifest_ref=action.artifact_ref,
                )
            except (OSError, ValueError, ValidationError) as exc:
                failures.append("invalid_experiment_output")
                transcript.append(
                    f"Turn {turn} experiment rejected: {type(exc).__name__}: {exc}"
                )
                continue
            return ModelInvestigationResult(
                experiment=experiment,
                audit=_audit(
                    spec=spec,
                    model=model,
                    attempts=attempts,
                    accepted=True,
                    tools=tool_calls,
                    failures=failures,
                    latency_s=latency,
                    skills_used=skill_ids,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - advisory experiment cannot stop training
        return _degraded(
            spec,
            f"Experiment failed: {type(exc).__name__}: {exc}"[:500],
            model=model,
            attempts=attempts,
            tools=tool_calls,
            failures=[*failures, f"investigation_error:{type(exc).__name__}"],
            latency_s=latency,
            skills_used=skill_ids,
        )
    finally:
        runtime.close()

    return _degraded(
        spec,
        f"Investigation exhausted its {MAX_TURNS}-turn budget.",
        model=model,
        attempts=attempts,
        tools=tool_calls,
        failures=[*failures, "turn_budget_exhausted"],
        latency_s=latency,
        skills_used=skill_ids,
    )


__all__ = [
    "MANIFEST_NAME",
    "MAX_TURNS",
    "ModelInvestigationResult",
    "SYSTEM_PROMPT",
    "build_spec",
    "degraded_model_investigation",
    "investigate_model",
]
