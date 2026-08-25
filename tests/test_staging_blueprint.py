"""The Staging graph is a validated contract, not decorative browser state."""

from __future__ import annotations

import pytest

from ads.contracts.staging import PipelineConnection
from ads.staging import (
    apply_component_updates,
    build_default_blueprint,
    document_engine_catalog,
    validate_executable_blueprint,
)


def test_default_blueprint_names_every_component_input_and_output() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)

    assert {component.id for component in blueprint.components} >= {
        "data-source",
        "understand-documents",
        "structured-brief",
        "document-brief",
        "understanding-synthesis",
        "default-ml-pipeline",
    }
    assert all(port.data_type for component in blueprint.components for port in component.inputs)
    assert all(port.data_type for component in blueprint.components for port in component.outputs)
    validate_executable_blueprint(blueprint)


def test_document_component_defaults_to_docling_and_lists_local_alternatives() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)
    component = next(item for item in blueprint.components if item.id == "understand-documents")
    engines = {item["id"]: item for item in document_engine_catalog()}

    assert component.settings["engine"] == "docling"
    assert {"docling", "unstructured", "marker", "mineru", "text_layer"} <= set(engines)
    assert all(item["local"] is True for item in engines.values())
    assert isinstance(engines["docling"]["available"], bool)
    assert engines["docling"]["selectable"] is True
    assert engines["unstructured"]["selectable"] is True
    assert engines["marker"]["selectable"] is True
    assert engines["mineru"]["selectable"] is True


def test_agent_can_only_change_known_component_preferences() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)

    changed = apply_component_updates(
        blueprint,
        {
            "understand-documents": {
                "settings": {
                    "engine": "unstructured",
                    "extract_tables": False,
                    "use_extracted_tables_for_training": False,
                }
            }
        },
        configured_by="planner",
    )
    document = next(item for item in changed.components if item.id == "understand-documents")
    assert document.settings["engine"] == "unstructured"
    assert document.configured_by == "planner"

    with pytest.raises(ValueError, match="not allowed"):
        apply_component_updates(
            blueprint,
            {"understand-documents": {"settings": {"shell_command": "anything"}}},
            configured_by="planner",
        )


def test_incompatible_edges_and_missing_required_inputs_cannot_be_accepted() -> None:
    blueprint = build_default_blueprint(has_tables=True, has_documents=True)
    incompatible = blueprint.model_copy(
        update={
            "connections": [
                *blueprint.connections,
                PipelineConnection(
                    id="bad-edge",
                    source_component="data-source",
                    source_port="documents",
                    target_component="intake",
                    target_port="structured_files",
                ),
            ]
        }
    )
    with pytest.raises(ValueError, match="incompatible"):
        incompatible.validate_connections()

    disconnected = blueprint.model_copy(
        update={
            "connections": [
                edge for edge in blueprint.connections if edge.id != "source-to-intake"
            ]
        }
    )
    with pytest.raises(ValueError, match="structured_files"):
        validate_executable_blueprint(disconnected)


def test_document_only_source_disables_the_structured_ml_path_truthfully() -> None:
    blueprint = build_default_blueprint(has_tables=False, has_documents=True)
    enabled = {component.id for component in blueprint.components if component.enabled}

    assert "understand-documents" in enabled
    assert "document-brief" in enabled
    assert "understanding-synthesis" in enabled
    assert "default-ml-pipeline" not in enabled
    validate_executable_blueprint(blueprint)
