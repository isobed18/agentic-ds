from __future__ import annotations

from pathlib import Path

from ads.automation import (
    AutomationRunner,
    ExecutorRegistry,
    NodeExecutionContext,
    NodeExecutionResult,
    compile_automation_plan,
)
from ads.automation.catalog import instantiate_component
from ads.contracts.automation import NodeAttempt
from ads.contracts.base import ArtifactType
from ads.contracts.staging import (
    LocalizedText,
    PipelineBlueprint,
    PipelineConnection,
    StagingReportArtifact,
)
from ads.store import ArtifactStore


def _text(value: str) -> LocalizedText:
    return LocalizedText(en=value, tr=value)


def _report(component_id: str, finding: str) -> StagingReportArtifact:
    return StagingReportArtifact(
        producer_component_id=component_id,
        title=_text(component_id),
        summary_text=_text(finding),
        findings=[_text(finding)],
    )


def _report_graph() -> PipelineBlueprint:
    components = [
        instantiate_component("agent.report", component_id, settings={"report_kind": "custom"})
        for component_id in ("report-a", "report-b", "consumer")
    ]
    return PipelineBlueprint(
        name=_text("Exact report graph"),
        components=components,
        connections=[
            PipelineConnection(
                id="b-to-consumer",
                source_component="report-b",
                source_port="reports",
                target_component="consumer",
                target_port="reports",
            )
        ],
    )


def _registry(seen_inputs: list[tuple[str, tuple[str, ...]]]) -> ExecutorRegistry:
    registry = ExecutorRegistry()

    def execute(context: NodeExecutionContext) -> NodeExecutionResult:
        input_ids = context.artifact_ids("reports")
        seen_inputs.append((context.node.instance_id, input_ids))
        if context.node.instance_id == "consumer":
            upstream = context.store.load(input_ids[0], StagingReportArtifact)
            finding = f"consumed:{upstream.producer_component_id}"
        else:
            finding = f"produced:{context.node.instance_id}"
        return NodeExecutionResult(outputs={"reports": _report(context.node.instance_id, finding)})

    registry.register("ads.executor.agent.report", execute)
    return registry


def test_runner_uses_exact_edges_and_records_instance_lineage(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    seen: list[tuple[str, tuple[str, ...]]] = []
    plan = compile_automation_plan(_report_graph())

    result = AutomationRunner(store, _registry(seen)).run(run_id="run-exact", plan=plan)

    assert result.status == "completed"
    assert [attempt.component_instance_id for attempt in result.attempts] == [
        "report-a",
        "report-b",
        "consumer",
    ]
    consumer_input = next(inputs for component, inputs in seen if component == "consumer")
    bound_report = store.load(consumer_input[0], StagingReportArtifact)
    assert bound_report.producer_component_id == "report-b"
    attempts = store.load_all("run-exact", ArtifactType.NODE_ATTEMPT, NodeAttempt)
    final_consumer = next(
        attempt
        for attempt in attempts
        if attempt.component_instance_id == "consumer" and attempt.status == "succeeded"
    )
    assert final_consumer.input_bindings[0].source_component == "report-b"
    assert final_consumer.output_bindings[0].availability == "released"


def test_pause_survives_a_new_runner_process_and_resumes_downstream(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    seen: list[tuple[str, tuple[str, ...]]] = []
    blueprint = _report_graph()
    blueprint = blueprint.model_copy(
        update={
            "components": [
                component.model_copy(
                    update={
                        "control": component.control.model_copy(update={"execution": "pause_after"})
                    }
                )
                if component.id == "report-b"
                else component
                for component in blueprint.components
            ]
        }
    )
    plan = compile_automation_plan(blueprint)

    first = AutomationRunner(store, _registry(seen)).run(run_id="run-resume", plan=plan)
    assert first.status == "paused"
    assert first.stopped_after_component == "report-b"
    assert all(component != "consumer" for component, _ in seen)

    second_seen: list[tuple[str, tuple[str, ...]]] = []
    second = AutomationRunner(store, _registry(second_seen)).run(run_id="run-resume", plan=plan)
    assert second.status == "completed"
    assert [component for component, _ in second_seen] == ["consumer"]


def test_new_plan_revision_reuses_identical_execution_keys_truthfully(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plan = compile_automation_plan(_report_graph())
    first_seen: list[tuple[str, tuple[str, ...]]] = []
    AutomationRunner(store, _registry(first_seen)).run(run_id="run-cache", plan=plan)

    revision = plan.model_copy(update={"plan_revision": 2})
    second_seen: list[tuple[str, tuple[str, ...]]] = []
    result = AutomationRunner(store, _registry(second_seen)).run(run_id="run-cache", plan=revision)

    assert result.status == "completed"
    assert second_seen == []
    assert len(result.attempts) == 3
    assert all(attempt.status == "cached" for attempt in result.attempts)
