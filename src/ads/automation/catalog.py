"""Host-owned node vocabulary for the visual ML automation editor.

The planner may instantiate these definitions, configure their closed settings,
and connect matching ports.  It may not invent executable code or a new node
kind.  This is the separation between agent-authored graphs and agent-authored
runtime capabilities.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from ads.contracts.base import FrozenModel
from ads.contracts.staging import (
    LocalizedText,
    PipelineBlueprint,
    PipelineComponent,
    PipelineConnection,
    PipelinePort,
)


class AutomationComponentDefinition(FrozenModel):
    catalog_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    category: Literal[
        "source",
        "understand",
        "extract",
        "review",
        "transform",
        "agent",
        "analyze",
        "train",
        "publish",
        "template",
    ]
    title: LocalizedText
    description: LocalizedText
    kind: str
    inputs: list[PipelinePort] = Field(default_factory=list)
    outputs: list[PipelinePort] = Field(default_factory=list)
    default_settings: dict[str, Any] = Field(default_factory=dict)
    evidence_layer: Literal["measured", "agent_proposal", "human_decision", "executor"]
    repeatable: bool = True


def _text(en: str, tr: str) -> LocalizedText:
    return LocalizedText(en=en, tr=tr)


def _port(
    port_id: str,
    label: str,
    data_type: str,
    *,
    required: bool = True,
    multiple: bool = False,
) -> PipelinePort:
    return PipelinePort(
        id=port_id,
        label=_text(label, label),
        data_type=data_type,
        required=required,
        multiple=multiple,
    )


def automation_component_catalog() -> list[AutomationComponentDefinition]:
    """Small orthogonal node set; templates compose it but do not replace it."""
    return [
        AutomationComponentDefinition(
            catalog_id="data.upload",
            category="source",
            title=_text("Uploaded data", "Yüklenen veri"),
            description=_text(
                "Owns uploaded structured files and documents; performs no interpretation.",
                "Yüklenen yapısal dosya ve belgeleri tutar; yorum yapmaz.",
            ),
            kind="data_source",
            outputs=[
                _port("structured_files", "Structured files", "structured_files", required=False),
                _port("documents", "Documents", "documents", required=False),
            ],
            evidence_layer="measured",
            repeatable=False,
        ),
        AutomationComponentDefinition(
            catalog_id="data.profile_tables",
            category="understand",
            title=_text("Profile tables", "Tabloları profille"),
            description=_text(
                "Measures schema, quality, privacy, keys and distributions without agent prose.",
                "Ajan metni olmadan şema, kalite, gizlilik, anahtar ve dağılımları ölçer.",
            ),
            kind="intake",
            inputs=[_port("structured_files", "Structured files", "structured_files")],
            outputs=[_port("table_profiles", "Table profiles", "table_profiles")],
            evidence_layer="measured",
        ),
        AutomationComponentDefinition(
            catalog_id="data.find_relationships",
            category="understand",
            title=_text("Find relationships", "İlişkileri bul"),
            description=_text(
                "Measures join endpoints, overlap, cardinality and row-loss risk.",
                "Birleştirme uçlarını, örtüşmeyi, kardinaliteyi ve satır kaybını ölçer.",
            ),
            kind="schema_discovery",
            inputs=[_port("table_profiles", "Table profiles", "table_profiles")],
            outputs=[_port("relationship_graph", "Relationship graph", "relationship_graph")],
            evidence_layer="measured",
        ),
        AutomationComponentDefinition(
            catalog_id="document.extract",
            category="extract",
            title=_text("Understand documents", "Belgeleri anla"),
            description=_text(
                "Runs one replaceable document engine and preserves page/region provenance.",
                "Değiştirilebilir bir belge motoru çalıştırır ve sayfa/bölge kaynağını korur.",
            ),
            kind="document_understanding",
            inputs=[_port("documents", "Documents", "documents")],
            outputs=[
                _port("document_content", "Document content", "document_content"),
                _port("extracted_tables", "Candidate tables", "extracted_tables"),
                _port("document_figures", "Candidate figures", "document_figures"),
            ],
            default_settings={
                "engine": "docling",
                "ocr": "auto",
                "extract_tables": True,
                "extract_figures": True,
            },
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="document.review_tables",
            category="review",
            title=_text("Review extracted tables", "Çıkarılan tabloları incele"),
            description=_text(
                "A person accepts candidate schemas and provenance before they become data inputs.",
                "Aday şema ve kaynak bilgisi veri girdisi olmadan önce kişi tarafından "
                "kabul edilir.",
            ),
            kind="human_review",
            inputs=[_port("extracted_tables", "Candidate tables", "extracted_tables")],
            outputs=[_port("accepted_tables", "Accepted tables", "accepted_tables")],
            evidence_layer="human_decision",
        ),
        AutomationComponentDefinition(
            catalog_id="data.integrate",
            category="transform",
            title=_text("Integrate data", "Veriyi birleştir"),
            description=_text(
                "Builds one analytical table from measured joins and explicitly accepted tables.",
                "Ölçülen birleştirmeler ve açıkça kabul edilmiş tablolardan analiz tablosu kurar.",
            ),
            kind="integration",
            inputs=[
                _port("table_profiles", "Table profiles", "table_profiles"),
                _port(
                    "relationship_graph",
                    "Relationship graph",
                    "relationship_graph",
                    required=False,
                ),
                _port("accepted_tables", "Accepted tables", "accepted_tables", required=False),
            ],
            outputs=[_port("integrated_table", "Integrated table", "integrated_table")],
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="agent.report",
            category="agent",
            title=_text("Create report", "Rapor oluştur"),
            description=_text(
                "Creates a bilingual interpretation from bounded artifacts; never changes data.",
                "Sınırlı artifact'lardan iki dilli yorum üretir; veriyi değiştirmez.",
            ),
            kind="report",
            inputs=[
                _port("table_profiles", "Table profiles", "table_profiles", required=False),
                _port(
                    "relationship_graph",
                    "Relationship graph",
                    "relationship_graph",
                    required=False,
                ),
                _port("document_content", "Document content", "document_content", required=False),
                _port("document_figures", "Document figures", "document_figures", required=False),
                _port("integrated_table", "Integrated table", "integrated_table", required=False),
                _port("eda_artifacts", "EDA artifacts", "eda_artifacts", required=False),
                _port("evaluation_report", "Evaluation", "evaluation_report", required=False),
                _port("reports", "Upstream reports", "reports", required=False, multiple=True),
            ],
            outputs=[_port("reports", "Reports", "reports")],
            default_settings={"report_kind": "data_understanding", "instructions": ""},
            evidence_layer="agent_proposal",
        ),
        AutomationComponentDefinition(
            catalog_id="agent.define_problem",
            category="agent",
            title=_text("Define ML problem", "ML problemini tanımla"),
            description=_text(
                "Proposes one target, task and metric for a branch from measured support.",
                "Ölçülen destekten bir dal için hedef, görev ve metrik önerir.",
            ),
            kind="problem_discovery",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("reports", "Context reports", "reports", required=False),
            ],
            outputs=[_port("problem_definition", "Problem definition", "problem_definition")],
            default_settings={"target_column": None, "task_type": None, "primary_metric": None},
            evidence_layer="agent_proposal",
        ),
        AutomationComponentDefinition(
            catalog_id="agent.plan_validation",
            category="agent",
            title=_text("Plan validation", "Doğrulamayı planla"),
            description=_text(
                "Chooses and trials a split strategy for one exact problem branch.",
                "Tek bir problem dalı için bölme stratejisi seçer ve dener.",
            ),
            kind="validation",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
            ],
            outputs=[_port("validation_strategy", "Validation strategy", "validation_strategy")],
            default_settings={"n_folds": 5},
            evidence_layer="agent_proposal",
        ),
        AutomationComponentDefinition(
            catalog_id="analysis.eda",
            category="analyze",
            title=_text("Explore data", "Veriyi keşfet"),
            description=_text(
                "Creates measured EDA tables and charts for one problem definition.",
                "Bir problem tanımı için ölçülen EDA tablo ve grafiklerini üretir.",
            ),
            kind="analysis",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
            ],
            outputs=[_port("eda_artifacts", "EDA artifacts", "eda_artifacts")],
            evidence_layer="measured",
        ),
        AutomationComponentDefinition(
            catalog_id="analysis.leakage",
            category="analyze",
            title=_text("Audit leakage", "Sızıntıyı denetle"),
            description=_text(
                "Runs the deterministic leakage floor for a problem and validation plan.",
                "Problem ve doğrulama planı için deterministik sızıntı tabanını çalıştırır.",
            ),
            kind="analysis",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
                _port("validation_strategy", "Validation", "validation_strategy"),
            ],
            outputs=[_port("leakage_report", "Leakage report", "leakage_report")],
            evidence_layer="measured",
        ),
        AutomationComponentDefinition(
            catalog_id="data.features",
            category="transform",
            title=_text("Build features", "Özellikleri oluştur"),
            description=_text(
                "Declares fold-local feature routing after leakage exclusions are known.",
                "Sızıntı dışlamaları bilindikten sonra katlama-içi özellik yönlendirmesi tanımlar.",
            ),
            kind="feature_engineering",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
                _port("leakage_report", "Leakage report", "leakage_report"),
            ],
            outputs=[_port("feature_spec", "Feature specification", "feature_spec")],
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="data.split",
            category="transform",
            title=_text("Split data", "Veriyi böl"),
            description=_text(
                "Materializes the accepted validation split and retained-support measurements.",
                "Kabul edilen doğrulama bölmesini ve korunan destek ölçümlerini oluşturur.",
            ),
            kind="splitting",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
                _port("validation_strategy", "Validation", "validation_strategy"),
            ],
            outputs=[_port("split_manifest", "Split manifest", "split_manifest")],
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="ml.train",
            category="train",
            title=_text("Train models", "Modelleri eğit"),
            description=_text(
                "Fits and compares bounded model candidates with fold-local preprocessing.",
                "Katlama-içi ön işleme ile sınırlı model adaylarını eğitir ve karşılaştırır.",
            ),
            kind="training",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("problem_definition", "Problem", "problem_definition"),
                _port("validation_strategy", "Validation", "validation_strategy"),
                _port("leakage_report", "Leakage report", "leakage_report"),
                _port("feature_spec", "Feature specification", "feature_spec"),
                _port("split_manifest", "Split manifest", "split_manifest"),
            ],
            outputs=[_port("trained_models", "Trained models", "trained_models")],
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="ml.evaluate",
            category="analyze",
            title=_text("Evaluate models", "Modelleri değerlendir"),
            description=_text(
                "Produces holdout, baseline, uncertainty and safety measurements.",
                "Holdout, taban çizgisi, belirsizlik ve güvenlik ölçümleri üretir.",
            ),
            kind="evaluation",
            inputs=[
                _port("trained_models", "Trained models", "trained_models"),
                _port("problem_definition", "Problem", "problem_definition"),
                _port("validation_strategy", "Validation", "validation_strategy"),
                _port("leakage_report", "Leakage report", "leakage_report"),
            ],
            outputs=[_port("evaluation_report", "Evaluation report", "evaluation_report")],
            evidence_layer="measured",
        ),
        AutomationComponentDefinition(
            catalog_id="report.final",
            category="publish",
            title=_text("Publish final report", "Son raporu yayınla"),
            description=_text(
                "Renders a bilingual, auditable report from measured evaluation artifacts.",
                "Ölçülen değerlendirme artifact'larından iki dilli denetlenebilir rapor üretir.",
            ),
            kind="report",
            inputs=[
                _port("evaluation_report", "Evaluation report", "evaluation_report"),
                _port("trained_models", "Trained models", "trained_models"),
                _port("problem_definition", "Problem", "problem_definition"),
            ],
            outputs=[_port("final_report", "Final report", "final_report")],
            evidence_layer="executor",
        ),
        AutomationComponentDefinition(
            catalog_id="template.default_ml",
            category="template",
            title=_text("Default ML pipeline", "Varsayılan ML hattı"),
            description=_text(
                "Expands into problem, validation, EDA, leakage, features, split, train, "
                "evaluate and report nodes.",
                "Problem, doğrulama, EDA, sızıntı, özellik, bölme, eğitim, değerlendirme ve "
                "rapor düğümlerine açılır.",
            ),
            kind="template",
            inputs=[
                _port("integrated_table", "Integrated table", "integrated_table"),
                _port("reports", "Understanding reports", "reports", required=False, multiple=True),
            ],
            outputs=[
                _port("trained_models", "Trained models", "trained_models"),
                _port("final_report", "Final report", "final_report"),
            ],
            default_settings={"template": "default_ml_v1"},
            evidence_layer="executor",
        ),
    ]


def instantiate_component(
    catalog_id: str,
    instance_id: str,
    *,
    branch_id: str | None = None,
    group_id: str | None = None,
    settings: dict[str, Any] | None = None,
    configured_by: Literal["system", "planner", "human"] = "system",
) -> PipelineComponent:
    definition = next(
        (item for item in automation_component_catalog() if item.catalog_id == catalog_id), None
    )
    if definition is None:
        raise ValueError(f"unknown automation component {catalog_id!r}")
    return PipelineComponent(
        id=instance_id,
        kind=definition.kind,
        title=definition.title,
        description=definition.description,
        inputs=definition.inputs,
        outputs=definition.outputs,
        settings={**definition.default_settings, **(settings or {})},
        evidence_layer=definition.evidence_layer,
        configured_by=configured_by,
        catalog_id=catalog_id,
        branch_id=branch_id,
        group_id=group_id,
    )


def _slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (cleaned[:32] or fallback).strip("-")


def add_problem_branches(
    blueprint: PipelineBlueprint,
    raw_branches: list[dict[str, Any]],
    *,
    configured_by: Literal["planner", "human"] = "planner",
) -> PipelineBlueprint:
    """Expand up to three problem branches using only registered components."""
    components = list(blueprint.components)
    connections = list(blueprint.connections)
    used_ids = {item.id for item in components}
    for ordinal, raw in enumerate(raw_branches[:3], start=1):
        title = str(raw.get("title") or f"Problem {ordinal}")
        branch_id = _slug(str(raw.get("branch_id") or title), f"problem-{ordinal}")
        if any(item.branch_id == branch_id for item in components):
            continue
        prefix = branch_id
        node_specs = [
            ("problem", "agent.define_problem"),
            ("validation", "agent.plan_validation"),
            ("eda", "analysis.eda"),
            ("leakage", "analysis.leakage"),
            ("features", "data.features"),
            ("split", "data.split"),
            ("training", "ml.train"),
            ("evaluation", "ml.evaluate"),
            ("report", "report.final"),
        ]
        ids = {role: f"{prefix}-{role}" for role, _ in node_specs}
        if set(ids.values()) & used_ids:
            continue
        problem_settings = {
            "problem_title": title,
            "target_column": raw.get("target_column"),
            "task_type": raw.get("task_type"),
            "primary_metric": raw.get("primary_metric"),
        }
        for role, catalog_id in node_specs:
            components.append(
                instantiate_component(
                    catalog_id,
                    ids[role],
                    branch_id=branch_id,
                    group_id=branch_id,
                    settings=problem_settings if role == "problem" else None,
                    configured_by=configured_by,
                )
            )
        used_ids.update(ids.values())

        def edge(
            source: str,
            source_port: str,
            target: str,
            target_port: str,
            edge_prefix: str = prefix,
        ) -> None:
            connections.append(
                PipelineConnection(
                    id=f"{edge_prefix}:{source}:{source_port}:{target}:{target_port}",
                    source_component=source,
                    source_port=source_port,
                    target_component=target,
                    target_port=target_port,
                )
            )

        problem = ids["problem"]
        validation = ids["validation"]
        edge("integrate-data", "integrated_table", problem, "integrated_table")
        edge("understanding-synthesis", "reports", problem, "reports")
        edge("integrate-data", "integrated_table", validation, "integrated_table")
        edge(problem, "problem_definition", validation, "problem_definition")
        for role in ("eda", "leakage", "features", "split", "training"):
            edge("integrate-data", "integrated_table", ids[role], "integrated_table")
            edge(problem, "problem_definition", ids[role], "problem_definition")
        edge(validation, "validation_strategy", ids["leakage"], "validation_strategy")
        edge(validation, "validation_strategy", ids["split"], "validation_strategy")
        edge(validation, "validation_strategy", ids["training"], "validation_strategy")
        edge(ids["leakage"], "leakage_report", ids["features"], "leakage_report")
        edge(ids["leakage"], "leakage_report", ids["training"], "leakage_report")
        edge(ids["features"], "feature_spec", ids["training"], "feature_spec")
        edge(ids["split"], "split_manifest", ids["training"], "split_manifest")
        edge(ids["training"], "trained_models", ids["evaluation"], "trained_models")
        edge(problem, "problem_definition", ids["evaluation"], "problem_definition")
        edge(validation, "validation_strategy", ids["evaluation"], "validation_strategy")
        edge(ids["leakage"], "leakage_report", ids["evaluation"], "leakage_report")
        edge(ids["evaluation"], "evaluation_report", ids["report"], "evaluation_report")
        edge(ids["training"], "trained_models", ids["report"], "trained_models")
        edge(problem, "problem_definition", ids["report"], "problem_definition")

    expanded = blueprint.model_copy(update={"components": components, "connections": connections})
    expanded.validate_connections()
    return expanded


def apply_planner_graph_operations(
    blueprint: PipelineBlueprint,
    *,
    additions: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
    updates: dict[str, dict[str, Any]] | None = None,
    disable_components: list[str] | None = None,
) -> PipelineBlueprint:
    """Apply planner graph edits through the same catalog boundary as the UI.

    The model chooses registered capabilities and typed endpoints; the host
    instantiates every node, validates its settings/control policy, and rejects
    the whole proposal if any connection is incompatible or cyclic.
    """
    # Local import avoids making the component catalog depend on staging's
    # default-graph construction during module import.
    from ads.staging.blueprint import apply_component_updates

    current_updates = dict(updates or {})
    for component_id in disable_components or []:
        current_updates.setdefault(component_id, {})["enabled"] = False
    revised = apply_component_updates(
        blueprint,
        current_updates,
        configured_by="planner",
    )
    components = list(revised.components)
    used_ids = {item.id for item in components}
    for raw in additions or []:
        component_id = str(raw.get("component_id") or "")
        if component_id in used_ids:
            raise ValueError(f"pipeline component id {component_id!r} already exists")
        component = instantiate_component(
            str(raw.get("catalog_id") or ""),
            component_id,
            branch_id=raw.get("branch_id"),
            group_id=raw.get("group_id"),
            configured_by="planner",
        )
        one_node = PipelineBlueprint(name=revised.name, components=[component], connections=[])
        configured = apply_component_updates(
            one_node,
            {
                component_id: {
                    "enabled": raw.get("enabled", True),
                    "settings": dict(raw.get("settings") or {}),
                    "control": dict(raw.get("control") or {}),
                }
            },
            configured_by="planner",
        ).components[0]
        components.append(configured)
        used_ids.add(component_id)

    edges = list(revised.connections)
    edge_ids = {edge.id for edge in edges}
    for raw in connections or []:
        edge = PipelineConnection.model_validate(raw)
        if edge.id in edge_ids:
            raise ValueError(f"pipeline connection id {edge.id!r} already exists")
        edges.append(edge)
        edge_ids.add(edge.id)
    result = revised.model_copy(update={"components": components, "connections": edges})
    result.validate_connections()
    return result


__all__ = [
    "AutomationComponentDefinition",
    "add_problem_branches",
    "apply_planner_graph_operations",
    "automation_component_catalog",
    "instantiate_component",
]
