from __future__ import annotations

from ads.automation import compile_automation_plan
from ads.automation.catalog import add_problem_branches, instantiate_component
from ads.contracts.staging import LocalizedText, PipelineBlueprint, PipelineConnection
from ads.staging import apply_component_updates, build_default_blueprint


def test_plan_freezes_instance_settings_scope_and_contracts() -> None:
    blueprint = add_problem_branches(
        build_default_blueprint(has_tables=True, has_documents=False),
        [
            {"branch_id": "cost", "title": "Cost", "target_column": "cost"},
            {"branch_id": "revenue", "title": "Revenue", "target_column": "revenue"},
        ],
    )
    blueprint = apply_component_updates(
        blueprint,
        {
            "cost-training": {"settings": {"candidate_limit": 2}},
            "revenue-training": {"settings": {"candidate_limit": 6}},
            "cost-eda": {"enabled": False},
        },
        configured_by="human",
    )

    plan = compile_automation_plan(blueprint)
    nodes = {node.instance_id: node for node in plan.nodes}

    assert plan.schema_version == "2"
    assert nodes["cost-training"].resolved_settings["candidate_limit"] == 2
    assert nodes["revenue-training"].resolved_settings["candidate_limit"] == 6
    assert nodes["cost-training"].scope_id == "cost"
    assert nodes["revenue-training"].scope_id == "revenue"
    assert "cost-eda" not in nodes
    split_binding = next(
        binding
        for binding in nodes["cost-training"].input_bindings
        if binding.target_port == "split_manifest"
    )
    assert split_binding.source_component == "cost-split"
    assert split_binding.contract_id == "ads.split_manifest@1"


def test_plan_routes_report_from_the_connected_instance_not_latest_type() -> None:
    report_a = instantiate_component("agent.report", "report-a", settings={"report_kind": "custom"})
    report_b = instantiate_component("agent.report", "report-b", settings={"report_kind": "custom"})
    consumer = instantiate_component("agent.report", "consumer", settings={"report_kind": "custom"})
    blueprint = PipelineBlueprint(
        name=LocalizedText(en="Exact route", tr="Exact route"),
        components=[report_a, report_b, consumer],
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

    plan = compile_automation_plan(blueprint)
    consumer_plan = next(node for node in plan.nodes if node.instance_id == "consumer")

    assert len(consumer_plan.input_bindings) == 1
    assert consumer_plan.input_bindings[0].source_component == "report-b"
    assert consumer_plan.input_bindings[0].source_port == "reports"
    assert all(binding.source_component != "report-a" for binding in plan.exact_bindings)


def test_editor_revision_does_not_change_execution_semantics() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=False)

    original = compile_automation_plan(blueprint)
    later_editor_revision = compile_automation_plan(
        blueprint.model_copy(update={"revision": blueprint.revision + 4})
    )

    assert later_editor_revision.blueprint_fingerprint == original.blueprint_fingerprint
    assert later_editor_revision.plan_id == original.plan_id
