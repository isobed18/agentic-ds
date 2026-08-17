"""Bounded multi-tool EDA investigator with typed exploratory output.

This agent is deliberately different from the planner agents. It may inspect
rows and execute code, but it cannot return UI prose or gate evidence directly.
The only publishable result is a sandbox-created JSON manifest that validates
against :class:`ExploratoryAnalysisManifest`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ads.agents.base import AgentContext, AgentSpec
from ads.agents.interpretation import validate_measurement_binding
from ads.contracts.agents import AgentAudit, AgentMemberAudit
from ads.contracts.comprehension import (
    ComprehensionBrief,
    ComprehensionScope,
    InterpretationBatchProposal,
    InterpretationItem,
    InterpretationProposal,
)
from ads.contracts.datacard import DataCard
from ads.contracts.eda import EDAReport
from ads.contracts.evidence import (
    MeasurementBundle,
    MeasurementKind,
    MeasurementRecord,
)
from ads.contracts.exploration import (
    ExploratoryAnalysis,
    ExploratoryAnalysisManifest,
    InvestigationAction,
    InvestigationActionKind,
)
from ads.contracts.gates import PermissionTier
from ads.llm import LARGE, StructuredLLM
from ads.store import compute_artifact_id
from ads.tools import PermissionBroker, ToolError, ToolRuntime, build_tool_registry

MAX_TURNS = 10
MAX_TRANSCRIPT_CHARS = 24_000
MAX_MANIFEST_BYTES = 128_000
MANIFEST_NAME = "exploratory_analysis.json"

_TOOL_HELP = """\
- column_profile: {"table":"abt","column":"..."}
- value_counts: {"table":"abt","column":"..."}
- correlation: {"table":"abt","left_column":"...","right_column":"..."}
- cardinality: {"table":"abt","column":"..."}
- null_rate: {"table":"abt","column":"..."}
- candidate_keys: {"table":"abt"}
- validation_signals: {"table":"abt","target_column":"...","task_type":"..."}
- execute_python: {"code":"...","timeout":30}. The ABT copy is /data/abt.csv.
"""

SYSTEM_PROMPT = f"""\
You are an exploratory data-analysis investigator running locally. A mandatory deterministic
profile has already run. Find one additional, consequential analysis that the fixed profiler did
not already answer. Work iteratively: inspect the data through several tools when useful, then
write and execute Python.

Every response is exactly one typed action. Use call_tool repeatedly to investigate. Python may
read /data/abt.csv and may write only under /artifacts. Source data is read-only. stdout is for
debugging and is never shown in the UI.

To publish, executed code must write /artifacts/{MANIFEST_NAME}. The file must match the supplied
ExploratoryAnalysisManifest schema and contain aggregate chart data, a proposed interpretation,
why it matters, and a concrete verification question. Only bar, horizontal-bar, and histogram
charts are accepted. Keep category labels bounded and never put source rows in the manifest.
After execute_python returns artifact://{MANIFEST_NAME}, finish by referencing that exact artifact.

An executed result is exploratory, not gate evidence. Do not claim that it clears leakage or any
other safety finding. If no worthwhile analysis can be produced, abandon honestly.
"""


@dataclass(frozen=True)
class EDAInvestigationResult:
    artifacts: tuple[ExploratoryAnalysis | MeasurementBundle | ComprehensionBrief, ...]
    audit: AgentAudit
    degraded_reason: str | None = None


def build_spec() -> AgentSpec[InvestigationAction]:
    tools = build_tool_registry().ids()
    return AgentSpec(
        id="eda_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=InvestigationAction,
        profile=LARGE,
        max_attempts=1,
        allowed_tools=tools,
        max_tool_tier=PermissionTier.EXECUTE,
        narration_fields=frozenset({"reason"}),
    )


def _context(card: DataCard, report: EDAReport) -> str:
    columns = [
        {
            "name": item.name,
            "semantic_type": item.semantic_type.value,
            "sensitivity": item.sensitivity.value,
            "null_rate": item.null_rate,
            "n_unique": item.n_unique,
        }
        for item in card.columns
    ]
    default = {
        "target": report.target_column,
        "rows": report.row_count,
        "default_analyses": [
            "target distribution",
            "column missingness",
            "Pearson correlation matrix",
            "target Pearson and adjusted mutual information",
            "IQR outlier counts",
            "numeric shape descriptors",
            "datetime period distributions",
        ],
        "strongest_target_relationships": [
            item.model_dump(mode="json")
            for item in sorted(
                report.target_relationships,
                key=lambda item: max(
                    abs(item.pearson_correlation or 0.0),
                    item.adjusted_mutual_information,
                ),
                reverse=True,
            )[:8]
        ],
    }
    manifest_schema = ExploratoryAnalysisManifest.model_json_schema()
    return (
        "## Dataset columns\n"
        + json.dumps(columns, separators=(",", ":"), default=str)
        + "\n\n## Mandatory EDA already completed\n"
        + json.dumps(default, separators=(",", ":"), default=str)
        + "\n\n## Available tools\n"
        + _TOOL_HELP
        + "\n## Required publish manifest schema\n"
        + json.dumps(manifest_schema, separators=(",", ":"), default=str)
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
) -> AgentAudit:
    return AgentAudit(
        stage_id="eda_investigation",
        agent_id=spec.id,
        output_contract=ExploratoryAnalysisManifest.__name__,
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


def _resolve_manifest(artifacts_dir: Path, artifact_ref: str) -> Path:
    relative = artifact_ref.removeprefix("artifact://")
    candidate = (artifacts_dir / relative).resolve()
    root = artifacts_dir.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise ValueError("Finished artifact does not resolve inside the sandbox output directory.")
    if candidate.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Exploratory manifest exceeds the 128 KB limit.")
    return candidate


def _validate_subjects(manifest: ExploratoryAnalysisManifest, card: DataCard) -> None:
    known = set(card.column_names)
    invalid = [
        subject
        for subject in manifest.subjects
        if subject.table != "abt" or subject.column is None or subject.column not in known
    ]
    if invalid:
        raise ValueError(f"Manifest subjects must be known ABT columns: {invalid}.")


def _promote(
    *,
    manifest: ExploratoryAnalysisManifest,
    report: EDAReport,
    code: str,
    execution_count: int | None,
    tool_calls: list[str],
    artifact_ref: str,
) -> tuple[ExploratoryAnalysis, MeasurementBundle, ComprehensionBrief]:
    analysis = ExploratoryAnalysis(
        source_eda_artifact_id=compute_artifact_id(report),
        manifest=manifest,
        code=code,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        execution_count=execution_count,
        tool_calls=tool_calls,
        output_ref=artifact_ref,
    )
    analysis_id = compute_artifact_id(analysis)
    measurement = MeasurementRecord.create(
        source_artifact_id=analysis_id,
        field_path="/manifest/chart",
        kind=MeasurementKind.EXPLORATORY_ANALYSIS,
        subjects=manifest.subjects,
        value={
            "evidence_class": "exploratory",
            "chart": manifest.chart.model_dump(mode="json"),
            "table": manifest.table.model_dump(mode="json") if manifest.table else None,
        },
    )
    bundle = MeasurementBundle(scope="analysis_exploratory", records=[measurement])
    proposal = InterpretationProposal(
        kind=manifest.interpretation_kind,
        subjects=manifest.subjects,
        measurement_ids=[measurement.measurement_id],
        interpretation=manifest.interpretation,
        why_it_matters=manifest.why_it_matters,
        verification_question=manifest.verification_question,
        confidence=manifest.confidence,
    )
    context = AgentContext(
        sections={},
        facts={"measurement_by_id": {measurement.measurement_id: measurement}},
    )
    failures = validate_measurement_binding(
        InterpretationBatchProposal(items=[proposal]), context
    )
    if failures:
        raise ValueError("Manifest interpretation is not bound to its measurement: " + "; ".join(
            failure.code for failure in failures
        ))
    brief = ComprehensionBrief(
        scope=ComprehensionScope.ANALYSIS,
        measurement_bundle_id=compute_artifact_id(bundle),
        items=[InterpretationItem.from_proposal(proposal, context.facts["measurement_by_id"])],
    )
    return analysis, bundle, brief


def investigate_eda(
    *,
    card: DataCard,
    report: EDAReport,
    llm: StructuredLLM,
    runtime: ToolRuntime,
) -> EDAInvestigationResult:
    """Run the bounded tool loop; every failure degrades without blocking EDA."""
    spec = build_spec()
    backend = runtime.backend()
    if backend is None or not runtime.execution_available():
        reason = "sandbox_unavailable"
        return EDAInvestigationResult(
            artifacts=(),
            audit=_audit(
                spec=spec,
                model=spec.profile.name,
                attempts=0,
                accepted=False,
                tools=[],
                failures=[reason],
                latency_s=0.0,
            ),
            degraded_reason=reason,
        )

    broker = PermissionBroker(build_tool_registry())
    transcript: list[str] = []
    tool_calls: list[str] = []
    produced: dict[str, tuple[str, int | None]] = {}
    failures: list[str] = []
    attempts = 0
    latency = 0.0
    model = spec.profile.name
    base_prompt = _context(card, report)

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
                return EDAInvestigationResult(
                    artifacts=(),
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
                        for ref in result.artifact_refs:
                            produced[ref] = (code, result.data.get("execution_count"))
                continue

            assert action.action is InvestigationActionKind.FINISH
            assert action.artifact_ref is not None
            if action.artifact_ref not in produced:
                failures.append("unexecuted_artifact_ref")
                transcript.append(
                    f"Turn {turn} rejected: {action.artifact_ref} was not produced by "
                    "this investigation's execute_python calls."
                )
                continue
            code, execution_count = produced[action.artifact_ref]
            try:
                path = _resolve_manifest(
                    runtime.execution_artifacts_dir(),
                    action.artifact_ref,
                )
                manifest = ExploratoryAnalysisManifest.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                _validate_subjects(manifest, card)
                artifacts = _promote(
                    manifest=manifest,
                    report=report,
                    code=code,
                    execution_count=execution_count,
                    tool_calls=tool_calls,
                    artifact_ref=action.artifact_ref,
                )
            except (OSError, ValueError, ValidationError) as exc:
                failures.append("invalid_manifest")
                transcript.append(
                    f"Turn {turn} manifest rejected: {type(exc).__name__}: {exc}"
                )
                continue
            return EDAInvestigationResult(
                artifacts=artifacts,
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
    except Exception as exc:  # noqa: BLE001 - advisory investigation must not stop EDA
        failures.append(f"investigation_error:{type(exc).__name__}")
        reason = f"Investigation failed: {type(exc).__name__}: {exc}"[:500]
        return EDAInvestigationResult(
            artifacts=(),
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

    reason = f"Investigation exhausted its {MAX_TURNS}-turn budget."
    return EDAInvestigationResult(
        artifacts=(),
        audit=_audit(
            spec=spec,
            model=model,
            attempts=attempts,
            accepted=False,
            tools=tool_calls,
            failures=[*failures, "turn_budget_exhausted"],
            latency_s=latency,
        ),
        degraded_reason=reason,
    )


__all__ = [
    "EDAInvestigationResult",
    "MANIFEST_NAME",
    "MAX_TURNS",
    "SYSTEM_PROMPT",
    "build_spec",
    "investigate_eda",
]
