"""Graph-native automation composition keeps capabilities and evidence trustworthy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ads.api.service import ControlPlane
from ads.automation import (
    add_problem_branches,
    apply_planner_graph_operations,
    automation_component_catalog,
    compile_automation_plan,
    instantiate_component,
)
from ads.contracts.base import ArtifactType
from ads.contracts.integration import IntegrationPlan
from ads.contracts.staging import (
    LocalizedText,
    PipelineConnection,
    PipelineNodeControl,
    StagingReportArtifact,
    StagingWorkspace,
)
from ads.staging import build_default_blueprint, validate_executable_blueprint
from ads.store import ArtifactStore


def test_catalog_components_have_one_responsibility_and_typed_boundaries() -> None:
    catalog = automation_component_catalog()

    assert {item.catalog_id for item in catalog} >= {
        "data.upload",
        "document.extract",
        "document.review_tables",
        "agent.report",
        "analysis.eda",
        "ml.train",
        "template.default_ml",
    }
    assert len({item.catalog_id for item in catalog}) == len(catalog)
    assert all(port.data_type for item in catalog for port in [*item.inputs, *item.outputs])
    layers = {item.catalog_id: item.evidence_layer for item in catalog}
    assert layers["document.extract"] == "executor"
    assert layers["document.review_tables"] == "human_decision"
    assert layers["agent.report"] == "agent_proposal"


def test_default_document_path_requires_review_before_integration() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)

    assert any(
        edge.source_component == "understand-documents"
        and edge.target_component == "review-document-tables"
        for edge in blueprint.connections
    )
    assert any(
        edge.source_component == "review-document-tables"
        and edge.target_component == "promote-document-tables"
        for edge in blueprint.connections
    )
    assert any(
        edge.source_component == "understand-documents"
        and edge.target_component == "promote-document-tables"
        for edge in blueprint.connections
    )
    assert any(
        edge.source_component == "promote-document-tables"
        and edge.target_component == "integrate-data"
        for edge in blueprint.connections
    )
    assert not any(
        edge.source_component == "understand-documents"
        and edge.target_component == "integrate-data"
        for edge in blueprint.connections
    )


def test_default_workspace_routes_modalities_and_synthesizes_their_reports() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)
    components = {item.id: item for item in blueprint.components}

    assert "planner" not in components, "planner is the graph control plane, not a data node"
    assert components["structured-brief"].catalog_id == "agent.report"
    assert components["document-brief"].catalog_id == "agent.report"
    assert components["understanding-synthesis"].catalog_id == "agent.report"
    synthesis_inputs = {
        (edge.source_component, edge.target_port)
        for edge in blueprint.connections
        if edge.target_component == "understanding-synthesis"
    }
    assert synthesis_inputs == {
        ("structured-brief", "reports"),
        ("document-brief", "reports"),
    }


def test_node_control_is_editable_without_changing_component_capability() -> None:
    baseline = build_default_blueprint(has_tables=True, has_documents=False)
    eda = instantiate_component("analysis.eda", "review-eda", configured_by="human")
    eda = eda.model_copy(
        update={
            "control": PipelineNodeControl(
                execution="pause_after",
                gate_handler="human",
                max_retries=1,
            )
        }
    )
    candidate = baseline.model_copy(update={"components": [*baseline.components, eda]})

    validated = ControlPlane._validate_human_blueprint(baseline, candidate)
    saved = next(item for item in validated.components if item.id == "review-eda")

    assert saved.control.execution == "pause_after"
    assert saved.control.max_retries == 1


def test_compiler_creates_deterministic_plan_and_pause_boundary() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=False)
    branch = add_problem_branches(
        blueprint,
        [
            {
                "branch_id": "cost",
                "title": "Predict cost",
                "target_column": "cost",
                "task_type": "regression",
                "primary_metric": "rmse",
            }
        ],
    )
    branch = branch.model_copy(
        update={
            "components": [
                item.model_copy(
                    update={"control": item.control.model_copy(update={"execution": "pause_after"})}
                )
                if item.id == "cost-eda"
                else item
                for item in branch.components
            ]
        }
    )

    plan = compile_automation_plan(branch)

    assert plan.node_order.index("cost-problem") < plan.node_order.index("cost-eda")
    assert plan.pause_after_component == "cost-eda"
    assert plan.pause_after_stage == "eda"
    assert plan.blueprint_fingerprint


def test_control_plane_persists_compiled_execution_plan(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    workspace = StagingWorkspace(
        source_id="upload:test",
        source_fingerprint="sha256:test",
        pipeline_blueprint=build_default_blueprint(has_tables=True, has_documents=False),
    )
    store.put(workspace, run_id="run-compile", stage_exec_id="staging")

    compiled = plane.compile_automation("run-compile")

    assert compiled["artifact_id"]
    assert compiled["plan"]["node_order"][0] == "data-source"
    assert compiled["plan"]["blueprint_fingerprint"]


def test_staging_reports_are_clickable_artifacts_attached_to_their_node(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    report = StagingReportArtifact(
        producer_component_id="understanding-synthesis",
        title=LocalizedText(en="Data overview", tr="Veri özeti"),
        summary_text=LocalizedText(
            en="Tables share a customer key.", tr="Tablolar anahtar paylaşır."
        ),
        findings=[LocalizedText(en="One join needs review.", tr="Bir birleşim incelenmeli.")],
    )
    ref = store.put(report, run_id="run-report", stage_exec_id="staging-reports")

    outputs = plane._staging_component_outputs(  # noqa: SLF001 - graph projection seam
        build_default_blueprint(has_tables=True, has_documents=False),
        intake_ids=[],
        schema_ids=[],
        document_ids=[],
        document_summary=None,
        reports_ready=True,
        report_artifact_ids={"understanding-synthesis": [ref.artifact_id]},
        plan_ready=True,
        plan_accepted=False,
    )
    synthesis = next(
        item
        for item in outputs
        if item.component_id == "understanding-synthesis" and item.port_id == "reports"
    )

    assert synthesis.artifact_ids == [ref.artifact_id]
    assert plane.artifact_preview(ref.artifact_id)["summary"]["en"].startswith("Tables")


def test_planner_can_add_registered_nodes_and_typed_connections() -> None:
    baseline = build_default_blueprint(has_tables=True, has_documents=False)

    revised = apply_planner_graph_operations(
        baseline,
        additions=[
            {
                "catalog_id": "agent.report",
                "component_id": "eda-review-report",
                "settings": {
                    "report_kind": "custom",
                    "instructions": "Explain the EDA artifacts for a joint review.",
                },
                "control": {"execution": "pause_after", "gate_handler": "human"},
            }
        ],
        connections=[
            {
                "id": "planner:add:eda-report-to-synthesis",
                "source_component": "eda-review-report",
                "source_port": "reports",
                "target_component": "understanding-synthesis",
                "target_port": "reports",
            }
        ],
    )

    added = next(item for item in revised.components if item.id == "eda-review-report")
    assert revised.revision == baseline.revision + 1
    assert added.configured_by == "planner"
    assert added.settings["report_kind"] == "custom"
    assert added.control.execution == "pause_after"
    revised.validate_connections()


def test_planner_patch_rejects_a_stale_graph_revision() -> None:
    baseline = build_default_blueprint(has_tables=True, has_documents=False)

    with pytest.raises(ValueError, match="stale graph patch"):
        apply_planner_graph_operations(
            baseline,
            base_revision=baseline.revision + 1,
            disable_components=["structured-brief"],
        )


def test_planner_cannot_invent_an_unregistered_component() -> None:
    baseline = build_default_blueprint(has_tables=True, has_documents=False)
    with pytest.raises(ValueError, match="unknown automation component"):
        apply_planner_graph_operations(
            baseline,
            additions=[
                {
                    "catalog_id": "agent.run_arbitrary_python",
                    "component_id": "unsafe-node",
                }
            ],
        )


def test_planner_can_expand_two_independent_default_ml_branches() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=False)
    expanded = add_problem_branches(
        blueprint,
        [
            {
                "branch_id": "readmission",
                "title": "Predict readmission",
                "target_column": "readmitted",
                "task_type": "binary_classification",
                "primary_metric": "roc_auc",
            },
            {
                "branch_id": "cost",
                "title": "Predict cost",
                "target_column": "cost",
                "task_type": "regression",
                "primary_metric": "rmse",
            },
        ],
    )

    for branch_id in ("readmission", "cost"):
        branch = [item for item in expanded.components if item.branch_id == branch_id]
        assert len(branch) == 9
        assert {item.catalog_id for item in branch} >= {
            "agent.define_problem",
            "analysis.eda",
            "ml.train",
            "report.final",
        }
    expanded.validate_connections()


def test_control_plane_launches_each_problem_branch_as_an_isolated_agent_run(
    tmp_path, monkeypatch
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plane = ControlPlane(store=store, source_roots=(), upload_root=tmp_path / "uploads")
    integration_ref = store.put(
        IntegrationPlan(
            base_table="customers",
            base_grain=["customer_id"],
            grain_description="One row per customer.",
        ),
        run_id="run-parent",
        stage_exec_id="schema_discovery",
    )
    blueprint = add_problem_branches(
        build_default_blueprint(has_tables=True, has_documents=False),
        [
            {
                "branch_id": "churn",
                "title": "Predict churn",
                "target_column": "churned",
                "task_type": "binary_classification",
                "primary_metric": "roc_auc",
            },
            {
                "branch_id": "value",
                "title": "Predict value",
                "target_column": "value",
                "task_type": "regression",
                "primary_metric": "rmse",
            },
        ],
    )
    store.put(
        StagingWorkspace(
            source_id="safe-demo",
            source_fingerprint="sha256:test",
            schema_artifact_ids=[integration_ref.artifact_id],
            pipeline_blueprint=blueprint,
        ),
        run_id="run-parent",
        stage_exec_id="staging",
    )
    launched = []

    def fake_start_run(**kwargs):
        launched.append(kwargs)
        return f"child-{len(launched)}"

    monkeypatch.setattr(plane, "start_run", fake_start_run)

    result = plane.start_automation_branches("run-parent")

    assert [item["branch_id"] for item in result] == ["churn", "value"]
    assert {item["problem"].target_column for item in launched} == {"churned", "value"}
    assert all(
        item["mode"] == "agent" and item["parent_run_id"] == "run-parent" for item in launched
    )
    problem_ref = store.put(
        launched[0]["problem"], run_id="child-1", stage_exec_id="problem_discovery"
    )
    monkeypatch.setattr(
        plane,
        "list_runs",
        lambda: [
            SimpleNamespace(
                parent_run_id="run-parent",
                branch_label="churn",
                run_id="child-1",
                status="running",
            )
        ],
    )
    workspace = store.load(
        store.latest("run-parent", ArtifactType.STAGING_WORKSPACE).artifact_id,
        StagingWorkspace,
    )
    outputs = plane._branch_component_outputs(  # noqa: SLF001 - projection seam
        "run-parent", workspace
    )
    problem_output = next(item for item in outputs if item.component_id == "churn-problem")
    assert problem_output.status == "ready"
    assert problem_output.artifact_ids == [problem_ref.artifact_id]


def test_human_may_add_catalog_nodes_but_not_spoof_their_ports() -> None:
    baseline = build_default_blueprint(has_tables=True, has_documents=False)
    report = instantiate_component("agent.report", "custom-report", configured_by="human")
    candidate = baseline.model_copy(update={"components": [*baseline.components, report]})

    validated = ControlPlane._validate_human_blueprint(baseline, candidate)
    saved_report = next(item for item in validated.components if item.id == "custom-report")
    assert saved_report.configured_by == "human"

    spoofed = report.model_copy(update={"outputs": []})
    bad = baseline.model_copy(update={"components": [*baseline.components, spoofed]})
    with pytest.raises(ValueError, match="catalog definition"):
        ControlPlane._validate_human_blueprint(baseline, bad)


def test_graph_rejects_cycles_and_multiple_connections_to_single_input() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=False)
    duplicate = blueprint.model_copy(
        update={
            "connections": [
                *blueprint.connections,
                PipelineConnection(
                    id="duplicate-profile-input",
                    source_component="intake",
                    source_port="table_profiles",
                    target_component="integrate-data",
                    target_port="table_profiles",
                ),
            ]
        }
    )
    with pytest.raises(ValueError, match="only one connection"):
        duplicate.validate_connections()

    # Synthesis already consumes the structured report; routing it back creates a real cycle.
    synthesis = next(item for item in blueprint.components if item.id == "understanding-synthesis")
    report = next(item for item in blueprint.components if item.id == "structured-brief")
    cyclic_report = report.model_copy(
        update={
            "inputs": [
                *report.inputs,
                synthesis.inputs[0].model_copy(
                    update={"id": "reports_back", "data_type": "reports", "multiple": False}
                ),
            ]
        }
    )
    replacements = {"structured-brief": cyclic_report}
    components = [replacements.get(item.id, item) for item in blueprint.components]
    cyclic = blueprint.model_copy(
        update={
            "components": components,
            "connections": [
                *blueprint.connections,
                PipelineConnection(
                    id="synthesis-back-to-report",
                    source_component="understanding-synthesis",
                    source_port="reports",
                    target_component="structured-brief",
                    target_port="reports_back",
                ),
            ],
        }
    )
    with pytest.raises(ValueError, match="acyclic"):
        cyclic.validate_connections()

    validate_executable_blueprint(blueprint)
