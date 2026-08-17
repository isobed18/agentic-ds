"""Bounded tool loop for one agent-authored feature experiment."""

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
from ads.contracts.feature_experiment import FeatureExperiment, FeatureExperimentManifest
from ads.contracts.features import FeatureSpec
from ads.contracts.gates import PermissionTier
from ads.contracts.problem import ProblemDefinition
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
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MANIFEST_NAME = "feature_experiment.json"

SYSTEM_PROMPT = f"""\
You are a local feature investigator. The mandatory fold-local preprocessing floor is already
defined. Investigate one consequential feature hypothesis that it does not express, using several
measurement tools when useful, then write and execute Python to test it.

/data/experiment_train.csv contains the target and opaque row ids. The validation copy contains
the same input columns but NO target. Fit and derive everything from training rows only. Create
/artifacts/{MANIFEST_NAME} and its referenced predictions CSV in one execute_python call. The CSV
must contain exactly __ads_experiment_row_id and prediction. The manifest has no score field: the
host scores it against labels that are never mounted. This result is exploratory, cannot change
the production FeatureSpec, cannot satisfy a gate, and never uses the final holdout.
"""


@dataclass(frozen=True)
class FeatureInvestigationResult:
    experiment: FeatureExperiment | None
    audit: AgentAudit
    degraded_reason: str | None = None


def build_spec() -> AgentSpec[InvestigationAction]:
    return AgentSpec(
        id="feature_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=InvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=build_tool_registry().ids(),
        max_tool_tier=PermissionTier.EXECUTE,
        narration_fields=frozenset({"reason"}),
    )


def _audit(
    spec: AgentSpec[InvestigationAction],
    *,
    accepted: bool,
    attempts: int,
    model: str,
    tools: list[str],
    failures: list[str],
    latency: float,
    skills: list[str],
) -> AgentAudit:
    return AgentAudit(
        stage_id="feature_investigation",
        agent_id=spec.id,
        output_contract=FeatureExperimentManifest.__name__,
        panel_size=1,
        valid_members=int(accepted),
        allowed_tools=sorted(spec.allowed_tools),
        evidence_tools=tools,
        skills_used=skills,
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
                latency_s=round(latency, 3),
            )
        ],
    )


def _degraded(
    reason: str,
    *,
    spec: AgentSpec[InvestigationAction] | None = None,
    attempts: int = 0,
    model: str | None = None,
    tools: list[str] | None = None,
    failures: list[str] | None = None,
    latency: float = 0.0,
    skills: list[str] | None = None,
) -> FeatureInvestigationResult:
    current = spec or build_spec()
    return FeatureInvestigationResult(
        experiment=None,
        audit=_audit(
            current,
            accepted=False,
            attempts=attempts,
            model=model or current.profile.name,
            tools=tools or [],
            failures=failures or [reason[:500]],
            latency=latency,
            skills=skills or [],
        ),
        degraded_reason=reason[:500],
    )


def degraded_feature_investigation(reason: str) -> FeatureInvestigationResult:
    return _degraded(reason, failures=[f"preparation_error:{reason[:200]}"])


def _resolve(root: Path, ref: str) -> Path:
    candidate = (root / ref.removeprefix("artifact://")).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise ValueError("Feature output does not resolve inside the artifact directory.")
    if candidate.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("Feature output exceeds the 16 MB limit.")
    return candidate


def investigate_features(
    *,
    partition: ExperimentPartition,
    problem: ProblemDefinition,
    feature_spec: FeatureSpec,
    llm: StructuredLLM,
    runtime: ToolRuntime,
) -> FeatureInvestigationResult:
    """Run one advisory feature experiment; every failure preserves the floor."""
    spec = build_spec()
    selected_skills = select_skills("feature_investigation", list(runtime.cards.values()))
    skill_ids = [skill.skill_id for skill in selected_skills]
    if runtime.backend() is None or not runtime.execution_available():
        return _degraded("sandbox_unavailable", spec=spec, skills=skill_ids)
    available = set(partition.validation_features.columns) - {ROW_ID_COLUMN}
    context = {
        "task_type": problem.task_type.value,
        "target_column": partition.target_column,
        "metric_measured_by_host": problem.primary_metric.value,
        "training_rows": len(partition.training),
        "validation_rows": len(partition.validation_features),
        "available_source_columns": sorted(available),
        "mandatory_routes": feature_spec.summary(),
    }
    base_prompt = (
        "## Development protocol\n"
        + json.dumps(context, separators=(",", ":"), default=str)
        + "\n\n## Required manifest schema\n"
        + json.dumps(FeatureExperimentManifest.model_json_schema(), separators=(",", ":"))
        + "\n\n## Tools\nUse profiling tools, then execute_python and finish "
        "with its exact artifact ref."
        + ("\n\n## Applicable skills\n" + render_skills(selected_skills) if selected_skills else "")
    )
    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tool_calls: list[str] = []
    produced: dict[str, tuple[str, int | None, frozenset[str]]] = {}
    failures: list[str] = []
    latency = 0.0
    model = spec.profile.name
    try:
        for turn in range(1, MAX_TURNS + 1):
            response = llm.generate_structured(
                system=spec.system_prompt,
                prompt=base_prompt + "\n\n" + "\n".join(transcript)[-24_000:],
                json_schema=InvestigationAction.model_json_schema(),
                profile=spec.profile,
            )
            latency += response.latency_s
            model = response.model
            if response.parsed is None:
                failures.append("invalid_json")
                continue
            try:
                action = InvestigationAction.model_validate(response.parsed)
            except ValidationError as exc:
                failures.append("invalid_action")
                transcript.append(f"Turn {turn} rejected: {exc}")
                continue
            if action.action is InvestigationActionKind.ABANDON:
                return _degraded(
                    action.reason,
                    spec=spec,
                    attempts=turn,
                    model=model,
                    tools=tool_calls,
                    failures=[*failures, "agent_abandoned"],
                    latency=latency,
                    skills=skill_ids,
                )
            if action.action is InvestigationActionKind.CALL_TOOL:
                assert action.tool_id is not None
                try:
                    result = broker.invoke(spec, action.tool_id, runtime, **action.arguments)
                except ToolError as exc:
                    failures.append(f"tool_error:{action.tool_id}")
                    transcript.append(f"Turn {turn} tool failed: {type(exc).__name__}: {exc}")
                    continue
                tool_calls.append(action.tool_id)
                transcript.append(
                    f"Turn {turn} {action.tool_id}: {result.summary}; "
                    + json.dumps(result.data, default=str, separators=(",", ":"))[:8_000]
                )
                if action.tool_id == "execute_python" and isinstance(
                    action.arguments.get("code"), str
                ):
                    batch = frozenset(result.artifact_refs)
                    for ref in batch:
                        produced[ref] = (
                            action.arguments["code"],
                            result.data.get("execution_count"),
                            batch,
                        )
                continue
            assert action.artifact_ref is not None
            if action.artifact_ref not in produced:
                failures.append("unexecuted_manifest_ref")
                continue
            code, execution_count, batch = produced[action.artifact_ref]
            try:
                root = runtime.execution_artifacts_dir()
                manifest = FeatureExperimentManifest.model_validate_json(
                    _resolve(root, action.artifact_ref).read_text(encoding="utf-8")
                )
                if not set(manifest.source_columns) <= available:
                    raise ValueError("Manifest names unavailable source columns.")
                if manifest.predictions_ref not in batch:
                    raise ValueError("Manifest and predictions require one execution batch.")
                prediction_path = _resolve(root, manifest.predictions_ref)
                score, baseline = measure_experiment_predictions(
                    pd.read_csv(prediction_path),
                    partition,
                    task_type=problem.task_type,
                    metric=problem.primary_metric,
                )
                experiment = FeatureExperiment(
                    source_feature_spec_artifact_id=compute_artifact_id(feature_spec),
                    task_type=problem.task_type,
                    metric=problem.primary_metric,
                    score=score,
                    baseline_score=baseline,
                    evaluation_row_count=len(partition.validation_targets),
                    manifest=manifest,
                    code=code,
                    code_hash=hashlib.sha256(code.encode()).hexdigest(),
                    predictions_hash=hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
                    execution_count=execution_count,
                    tool_calls=tool_calls,
                    manifest_ref=action.artifact_ref,
                )
                return FeatureInvestigationResult(
                    experiment=experiment,
                    audit=_audit(
                        spec,
                        accepted=True,
                        attempts=turn,
                        model=model,
                        tools=tool_calls,
                        failures=failures,
                        latency=latency,
                        skills=skill_ids,
                    ),
                )
            except (ValueError, ValidationError, OSError, pd.errors.ParserError) as exc:
                failures.append("invalid_feature_output")
                transcript.append(f"Turn {turn} output rejected: {type(exc).__name__}: {exc}")
        return _degraded(
            "turn_budget_exhausted",
            spec=spec,
            attempts=MAX_TURNS,
            model=model,
            tools=tool_calls,
            failures=failures,
            latency=latency,
            skills=skill_ids,
        )
    except Exception as exc:  # noqa: BLE001 - advisory boundary must not stop the run
        return _degraded(
            f"{type(exc).__name__}: {exc}",
            spec=spec,
            model=model,
            tools=tool_calls,
            failures=[*failures, f"investigation_error:{type(exc).__name__}"],
            latency=latency,
            skills=skill_ids,
        )
    finally:
        runtime.close()


__all__ = [
    "FeatureInvestigationResult",
    "MANIFEST_NAME",
    "degraded_feature_investigation",
    "investigate_features",
]
