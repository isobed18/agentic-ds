"""Counter-tests for the authored EDA investigation boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from ads.agents.eda_investigator import MANIFEST_NAME, investigate_eda
from ads.contracts import Metric, ProblemDefinition, TaskType
from ads.contracts.base import ArtifactType
from ads.contracts.comprehension import ComprehensionBrief
from ads.contracts.evidence import MeasurementBundle, MeasurementKind
from ads.contracts.exploration import ExploratoryAnalysis, ExploratoryAnalysisManifest
from ads.contracts.gates import BUILTIN_PROFILES, QualitySignals
from ads.eda import profile_for_eda
from ads.gates import GatePolicy
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse, ModelProfile
from ads.orchestration import (
    ComponentRegistry,
    RunState,
    StageDefinition,
    StageResult,
    linear_spec,
    run_workflow,
)
from ads.pipeline.comprehension_stages import _merge
from ads.sandbox import ExecutionResult, TextOutput
from ads.store import ArtifactStore, compute_artifact_id
from ads.tools import ToolRuntime


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "segment": ["new"] * 8 + ["established"] * 12,
            "tenure": list(range(20)),
            "annual_comp": [80_000 + value * 2_000 for value in range(20)],
        }
    )


def _inputs():
    frame = _frame()
    card = profile_table(
        LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="pandas")
    )
    problem = ProblemDefinition(
        task_type=TaskType.REGRESSION,
        target_column="annual_comp",
        primary_metric=Metric.RMSE,
        title="Compensation",
        description="Predict compensation.",
    )
    return frame, card, profile_for_eda(card, frame, problem)


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "title": "Median compensation by tenure cohort",
        "description": "A cohort comparison beyond the mandatory univariate profile.",
        "subjects": [
            {"table": "abt", "column": "tenure"},
            {"table": "abt", "column": "annual_comp"},
        ],
        "chart": {
            "kind": "hbar",
            "x_label": "Median annual compensation",
            "y_label": "Tenure cohort",
            "unit": "value",
            "series": [
                {"label": "0-9 years", "value": 89_000.0, "count": 10},
                {"label": "10+ years", "value": 119_000.0, "count": 10},
            ],
        },
        "table": {
            "columns": ["Cohort", "Rows", "Median"],
            "rows": [["0-9 years", 10, 89_000.0], ["10+ years", 10, 119_000.0]],
        },
        "interpretation_kind": "distribution_pattern",
        "interpretation": "The measured cohorts suggest compensation differs with tenure.",
        "why_it_matters": "A single overall distribution hides this cohort difference.",
        "verification_question": "Does the compensation policy explicitly use tenure bands?",
        "confidence": "medium",
    }


class _Backend:
    def __init__(self, root: Path, *, manifest: dict[str, Any] | None = None) -> None:
        self.data_dir = root / "data"
        self.artifacts_dir = root / "artifacts"
        self.data_dir.mkdir()
        self.artifacts_dir.mkdir()
        self.manifest = manifest
        self.destroyed = False

    def available(self) -> bool:
        return True

    def create_session(self, run_id: str) -> str:
        return f"session:{run_id}"

    def execute(self, session: str, code: str, timeout: float = 30.0) -> ExecutionResult:
        assert session.startswith("session:")
        assert timeout <= 120
        if self.manifest is not None:
            (self.artifacts_dir / MANIFEST_NAME).write_text(
                json.dumps(self.manifest), encoding="utf-8"
            )
        return ExecutionResult(
            execution_count=1,
            outputs=(TextOutput(text="analysis complete\n", stream="stdout"),),
        )

    def destroy(self, session: str) -> None:
        self.destroyed = True


class _LLM:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        profile: ModelProfile,
    ) -> LLMResponse:
        action = self.actions[len(self.calls)]
        self.calls.append({"system": system, "prompt": prompt, "schema": json_schema})
        return LLMResponse(
            text=json.dumps(action),
            model=profile.name,
            latency_s=0.01,
            parsed=action,
        )


def _successful_actions() -> list[dict[str, Any]]:
    return [
        {
            "action": "call_tool",
            "tool_id": "null_rate",
            "arguments": {"table": "abt", "column": "annual_comp"},
            "reason": "Check whether target coverage explains the default profile.",
        },
        {
            "action": "call_tool",
            "tool_id": "value_counts",
            "arguments": {"table": "abt", "column": "segment"},
            "reason": "Inspect whether segment sizes support a cohort analysis.",
        },
        {
            "action": "call_tool",
            "tool_id": "execute_python",
            "arguments": {
                "code": "# load /data/abt.csv and write /artifacts/exploratory_analysis.json",
                "timeout": 30,
            },
            "reason": "Execute the additional cohort analysis on the read-only copy.",
        },
        {
            "action": "finish",
            "artifact_ref": f"artifact://{MANIFEST_NAME}",
            "reason": "Publish the exact validated file created by executed code.",
        },
    ]


def test_multi_tool_code_loop_produces_exploratory_cited_artifacts(tmp_path: Path) -> None:
    frame, card, report = _inputs()
    backend = _Backend(tmp_path, manifest=_manifest())
    runtime = ToolRuntime.from_sources(
        [card],
        {"abt": frame},
        run_id="investigator-test",
        execution_backend=backend,
        artifacts_dir=backend.artifacts_dir,
    )

    result = investigate_eda(
        card=card,
        report=report,
        llm=_LLM(_successful_actions()),
        runtime=runtime,
    )

    assert result.degraded_reason is None
    assert len(result.artifacts) == 3
    analysis = next(item for item in result.artifacts if isinstance(item, ExploratoryAnalysis))
    bundle = next(item for item in result.artifacts if isinstance(item, MeasurementBundle))
    brief = next(item for item in result.artifacts if isinstance(item, ComprehensionBrief))
    measurement = bundle.records[0]

    assert analysis.artifact_type is ArtifactType.EXPLORATORY_ANALYSIS
    assert analysis.evidence_class == "exploratory"
    assert analysis.tool_calls == ["null_rate", "value_counts", "execute_python"]
    assert measurement.kind is MeasurementKind.EXPLORATORY_ANALYSIS
    assert measurement.source_artifact_id == compute_artifact_id(analysis)
    assert brief.items[0].citations == [measurement]
    assert brief.items[0].epistemic_state == "proposed"
    assert brief.items[0].verification.question.endswith("tenure bands?")
    assert result.audit.evidence_tools == analysis.tool_calls
    assert result.audit.raw_rows_shared is True
    assert result.audit.valid_members == 1
    assert backend.destroyed is True


def test_exploratory_artifact_present_in_run_cannot_change_gate_verdict(
    tmp_path: Path,
) -> None:
    """Exercise the exclusion boundary, not merely its ``exploratory`` label."""
    analysis = ExploratoryAnalysis(
        source_eda_artifact_id="a" * 64,
        manifest=ExploratoryAnalysisManifest.model_validate(_manifest()),
        code="print('exploratory')",
        code_hash="b" * 64,
        tool_calls=["execute_python"],
        output_ref=f"artifact://{MANIFEST_NAME}",
    )
    floor = StageResult(signals=QualitySignals(pii_columns_in_context=1))
    enriched = _merge(
        floor,
        StageResult(
            artifacts=[analysis],
            names={0: "exploratory_analysis"},
            # An advisory component has its own default signals. The merge boundary
            # must never substitute them for the deterministic floor's signals.
            signals=QualitySignals(),
        ),
    )
    spec = linear_spec(
        "exploratory-gate-counter-test",
        "1",
        [
            StageDefinition(
                id="eda",
                component="stage",
                produces=(ArtifactType.EXPLORATORY_ANALYSIS,),
            )
        ],
    )

    decisions = []
    states = []
    for name, result in (("floor", floor), ("enriched", enriched)):
        registry = ComponentRegistry()
        registry.register("stage", lambda state, correction, result=result: result)
        state = RunState(
            run_id=name,
            store=ArtifactStore(tmp_path / name),
            profile=BUILTIN_PROFILES["full_auto"],
        )
        outcome = run_workflow(spec, registry, state, policy=GatePolicy.load())
        decision = outcome.decisions[0]
        decisions.append(
            (
                decision.verdict,
                decision.reason_code,
                decision.triggered_rules,
                decision.correction_instructions,
                decision.human_prompt.context_summary if decision.human_prompt else None,
            )
        )
        states.append(state)

    assert decisions[0] == decisions[1]
    assert states[0].all_of(ArtifactType.EXPLORATORY_ANALYSIS, ExploratoryAnalysis) == []
    assert len(states[1].all_of(ArtifactType.EXPLORATORY_ANALYSIS, ExploratoryAnalysis)) == 1


def test_finish_cannot_reference_a_file_the_execution_did_not_create(tmp_path: Path) -> None:
    frame, card, report = _inputs()
    backend = _Backend(tmp_path, manifest=None)
    actions = [
        {
            "action": "finish",
            "artifact_ref": "artifact://invented.json",
            "reason": "Try to publish an unexecuted file reference.",
        },
        {
            "action": "abandon",
            "reason": "No executed manifest is available to publish safely.",
        },
    ]
    result = investigate_eda(
        card=card,
        report=report,
        llm=_LLM(actions),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            run_id="unexecuted-ref",
            execution_backend=backend,
            artifacts_dir=backend.artifacts_dir,
        ),
    )

    assert result.artifacts == ()
    assert "unexecuted_artifact_ref" in result.audit.members[0].validation_failures
    assert result.audit.valid_members == 0


def test_manifest_format_rejects_arbitrary_plot_points_and_missing_verification() -> None:
    payload = _manifest()
    payload["chart"] = {
        "kind": "bar",
        "x_label": "x",
        "y_label": "y",
        "points": [{"x": 1, "y": 2}],
    }
    payload.pop("verification_question")

    with pytest.raises(ValidationError) as exc:
        ExploratoryAnalysisManifest.model_validate(payload)

    rendered = str(exc.value)
    assert "verification_question" in rendered
    assert "points" in rendered or "series" in rendered


def test_invalid_manifest_never_becomes_an_analysis_artifact(tmp_path: Path) -> None:
    frame, card, report = _inputs()
    malformed = _manifest()
    malformed["subjects"] = [{"table": "abt", "column": "not_a_column"}]
    backend = _Backend(tmp_path, manifest=malformed)
    actions = [
        _successful_actions()[2],
        _successful_actions()[3],
        {
            "action": "abandon",
            "reason": "The host rejected the manifest, so do not publish it.",
        },
    ]
    result = investigate_eda(
        card=card,
        report=report,
        llm=_LLM(actions),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            run_id="invalid-manifest",
            execution_backend=backend,
            artifacts_dir=backend.artifacts_dir,
        ),
    )

    assert result.artifacts == ()
    assert "invalid_manifest" in result.audit.members[0].validation_failures
    assert result.audit.valid_members == 0


def test_unexpected_llm_failure_degrades_and_destroys_the_execution_session(
    tmp_path: Path,
) -> None:
    """Advisory authored EDA must not take the mandatory scientific run down."""
    frame, card, report = _inputs()
    backend = _Backend(tmp_path, manifest=_manifest())
    # The first call executes and opens a session; the exhausted script raises
    # IndexError on the next turn, outside the normal model/JSON failure family.
    result = investigate_eda(
        card=card,
        report=report,
        llm=_LLM([_successful_actions()[2]]),
        runtime=ToolRuntime.from_sources(
            [card],
            {"abt": frame},
            run_id="unexpected-llm-failure",
            execution_backend=backend,
            artifacts_dir=backend.artifacts_dir,
        ),
    )

    assert result.artifacts == ()
    assert result.degraded_reason is not None
    assert "IndexError" in result.degraded_reason
    assert "investigation_error:IndexError" in result.audit.members[0].validation_failures
    assert backend.destroyed is True
