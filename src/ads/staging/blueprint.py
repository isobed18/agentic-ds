"""Default multimodal pipeline graph and its host-owned validation rules.

The graph is not an execution engine yet. It is the durable pre-run contract that
states exactly what each component consumes and produces. The existing workflow
runner remains the executor for the default ML component; document extractors can
be implemented behind the same typed ports without changing the UI contract.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ads.contracts.registry import canonical_contract_id
from ads.contracts.staging import (
    LocalizedText,
    PipelineBlueprint,
    PipelineComponent,
    PipelineConnection,
    PipelineNodeControl,
    PipelinePort,
)


def _text(en: str, tr: str) -> LocalizedText:
    return LocalizedText(en=en, tr=tr)


def _port(
    port_id: str,
    en: str,
    tr: str,
    data_type: str,
    *,
    required: bool = True,
    multiple: bool = False,
) -> PipelinePort:
    return PipelinePort(
        id=port_id,
        label=_text(en, tr),
        data_type=canonical_contract_id(data_type),
        required=required,
        multiple=multiple,
    )


def document_engine_catalog() -> list[dict[str, Any]]:
    """Truthful local capabilities for the document-engine preference control."""
    definitions = (
        (
            "docling",
            "Docling",
            "Layout, OCR, tables and figures with a unified document representation.",
            "Yerleşim, OCR, tablo ve şekilleri birleşik belge gösterimiyle işler.",
            "docling",
            "documents-docling",
            True,
            "MIT",
        ),
        (
            "unstructured",
            "Unstructured",
            "Flexible partitioning with fast, high-resolution and OCR modes.",
            "Hızlı, yüksek çözünürlüklü ve OCR kipleriyle esnek bölümleme sunar.",
            "unstructured",
            "documents-unstructured",
            True,
            "Apache-2.0",
        ),
        (
            "marker",
            "Marker",
            "PDF-to-Markdown extraction focused on layout, equations and images.",
            "Yerleşim, denklem ve görsellere odaklı PDF'den Markdown'a çıkarım yapar.",
            "marker",
            "documents-marker-worker",
            True,
            "GPL-3.0 / restricted model weights",
        ),
        (
            "mineru",
            "MinerU",
            "Complex PDF extraction with tables, formulas, OCR and multimodal layout.",
            "Tablo, formül, OCR ve çok kipli yerleşim için karmaşık PDF çıkarımı yapar.",
            "mineru",
            "documents-mineru-worker",
            True,
            "Apache-2.0 with additional terms",
        ),
        (
            "text_layer",
            "Built-in text reader",
            "Fast local PDF text-layer extraction; no OCR or table reconstruction.",
            "Hızlı yerel PDF metin katmanı okuma; OCR veya tablo oluşturma yapmaz.",
            "pypdf",
            None,
            True,
            "BSD-3-Clause",
        ),
    )
    worker_root = Path(os.environ.get("ADS_DOCUMENT_ENV_ROOT", ".document-envs"))

    def available(engine_id: str, module: str) -> bool:
        if engine_id in {"marker", "mineru"}:
            executable = (
                worker_root
                / engine_id
                / "Scripts"
                / ("python.exe" if engine_id == "marker" else "mineru.exe")
            )
            return executable.exists()
        return importlib.util.find_spec(module) is not None

    return [
        {
            "id": engine_id,
            "label": label,
            "description": _text(en, tr).model_dump(mode="json"),
            "available": available(engine_id, module),
            "install_extra": install_extra,
            "local": True,
            "selectable": selectable,
            "license": license_name,
        }
        for engine_id, label, en, tr, module, install_extra, selectable, license_name in definitions
    ]


def build_default_blueprint(*, has_tables: bool, has_documents: bool) -> PipelineBlueprint:
    """Build the recommended multimodal graph from orthogonal components.

    The planner deliberately surrounds the graph as its control plane. It reads
    row-free output summaries and revises this blueprint, but it is not rendered
    as a fake data dependency.
    """
    components = [
        PipelineComponent(
            id="data-source",
            kind="data_source",
            title=_text("Uploaded data", "Yüklenen veri"),
            description=_text(
                "CSV, Excel, Parquet and document files in one source.",
                "Tek kaynakta CSV, Excel, Parquet ve belge dosyaları.",
            ),
            outputs=[
                _port(
                    "structured_files", "Structured files", "Yapısal dosyalar", "structured_files"
                ),
                _port("documents", "Documents", "Belgeler", "documents"),
            ],
            evidence_layer="measured",
            catalog_id="data.upload",
        ),
        PipelineComponent(
            id="intake",
            kind="intake",
            title=_text("Profile tables", "Tabloları profille"),
            description=_text(
                "Measure columns, types, keys, quality and privacy without exposing rows.",
                "Satırları açmadan sütun, tür, anahtar, kalite ve gizliliği ölçer.",
            ),
            inputs=[
                _port(
                    "structured_files", "Structured files", "Yapısal dosyalar", "structured_files"
                )
            ],
            outputs=[
                _port("table_profiles", "Table profiles", "Tablo profilleri", "table_profiles")
            ],
            enabled=has_tables,
            evidence_layer="measured",
            catalog_id="data.profile_tables",
        ),
        PipelineComponent(
            id="schema-discovery",
            kind="schema_discovery",
            title=_text("Find relationships", "İlişkileri bul"),
            description=_text(
                "Measure join endpoints, overlap, cardinality and row-loss risk.",
                "Birleştirme uçlarını, örtüşmeyi, kardinaliteyi ve satır kaybını ölçer.",
            ),
            inputs=[
                _port("table_profiles", "Table profiles", "Tablo profilleri", "table_profiles")
            ],
            outputs=[
                _port(
                    "relationship_graph",
                    "Relationship graph",
                    "İlişki grafiği",
                    "relationship_graph",
                )
            ],
            enabled=has_tables,
            evidence_layer="measured",
            catalog_id="data.find_relationships",
        ),
        PipelineComponent(
            id="understand-documents",
            kind="document_understanding",
            title=_text("Understand documents", "Belgeleri anla"),
            description=_text(
                "Read PDFs, recover structure, and optionally extract candidate tables.",
                "PDF'leri okur, yapıyı çıkarır ve isteğe bağlı aday tablolar üretir.",
            ),
            inputs=[_port("documents", "Documents", "Belgeler", "documents")],
            outputs=[
                _port("document_content", "Document content", "Belge içeriği", "document_content"),
                _port("extracted_tables", "Candidate tables", "Aday tablolar", "extracted_tables"),
                _port("document_figures", "Candidate figures", "Aday şekiller", "document_figures"),
            ],
            settings={
                "engine": "docling",
                "ocr": "auto",
                "extract_tables": True,
                "extract_figures": True,
                "use_extracted_tables_for_training": False,
            },
            enabled=has_documents,
            optional=True,
            evidence_layer="executor",
            catalog_id="document.extract",
        ),
        PipelineComponent(
            id="review-document-tables",
            kind="human_review",
            title=_text("Review extracted tables", "Çıkarılan tabloları incele"),
            description=_text(
                "Accept table schema and page provenance before document values can enter data.",
                "Belge değerleri veriye girmeden önce tablo şeması ve sayfa kaynağını kabul edin.",
            ),
            inputs=[
                _port("extracted_tables", "Candidate tables", "Aday tablolar", "extracted_tables")
            ],
            outputs=[
                _port(
                    "review_decisions",
                    "Review decisions",
                    "İnceleme kararları",
                    "review_decisions",
                )
            ],
            enabled=has_documents,
            optional=True,
            evidence_layer="human_decision",
            catalog_id="document.review_tables",
        ),
        PipelineComponent(
            id="promote-document-tables",
            kind="integration",
            title=_text("Promote document tables", "Belge tablolarını veri yap"),
            description=_text(
                "Create immutable tables only from explicitly accepted candidates.",
                "Yalnızca açıkça kabul edilen adaylardan değişmez tablolar üretir.",
            ),
            inputs=[
                _port("extracted_tables", "Candidate tables", "Aday tablolar", "extracted_tables"),
                _port(
                    "review_decisions",
                    "Review decisions",
                    "İnceleme kararları",
                    "review_decisions",
                ),
            ],
            outputs=[
                _port(
                    "accepted_tables",
                    "Promoted tables",
                    "Veriye alınan tablolar",
                    "accepted_tables",
                )
            ],
            enabled=has_documents,
            optional=True,
            evidence_layer="executor",
            catalog_id="document.promote_tables",
        ),
        PipelineComponent(
            id="integrate-data",
            kind="integration",
            title=_text("Integrate data", "Veriyi birleştir"),
            description=_text(
                "Create the analytical table from measured joins and accepted document tables.",
                "Ölçülen birleştirmelerden ve kabul edilen belge tablolarından "
                "analiz tablosu üretir.",
            ),
            inputs=[
                _port("table_profiles", "Table profiles", "Tablo profilleri", "table_profiles"),
                _port("relationship_graph", "Relationships", "İlişkiler", "relationship_graph"),
                _port(
                    "accepted_tables",
                    "Accepted document tables",
                    "Kabul edilen belge tabloları",
                    "accepted_tables",
                    required=False,
                ),
            ],
            outputs=[
                _port("integrated_table", "Integrated table", "Birleşik tablo", "integrated_table")
            ],
            settings={"include_document_tables": False},
            enabled=has_tables,
            evidence_layer="executor",
            catalog_id="data.integrate",
        ),
        PipelineComponent(
            id="structured-brief",
            kind="report",
            title=_text("Explain structured data", "Yapısal veriyi açıkla"),
            description=_text(
                "Explain measured table relationships, risks, opportunities and open questions.",
                "Ölçülen tablo ilişkilerini, riskleri, fırsatları ve açık soruları açıklar.",
            ),
            inputs=[
                _port(
                    "table_profiles",
                    "Table profiles",
                    "Tablo profilleri",
                    "table_profiles",
                    required=False,
                ),
                _port(
                    "relationship_graph",
                    "Relationships",
                    "İlişkiler",
                    "relationship_graph",
                    required=False,
                ),
            ],
            outputs=[_port("reports", "Structured briefing", "Yapısal veri özeti", "reports")],
            settings={"report_kind": "data_understanding", "instructions": ""},
            enabled=has_tables,
            evidence_layer="agent_proposal",
            catalog_id="agent.report",
        ),
        PipelineComponent(
            id="document-brief",
            kind="report",
            title=_text("Explain documents", "Belgeleri açıkla"),
            description=_text(
                "Explain extracted document content with page provenance.",
                "Çıkarılan belge içeriğini sayfa kaynağıyla açıklar.",
            ),
            inputs=[
                _port(
                    "document_content",
                    "Document content",
                    "Belge içeriği",
                    "document_content",
                ),
                _port(
                    "document_figures",
                    "Document figures",
                    "Belge şekilleri",
                    "document_figures",
                    required=False,
                ),
            ],
            outputs=[_port("reports", "Document briefing", "Belge özeti", "reports")],
            settings={"report_kind": "document_understanding", "instructions": ""},
            enabled=has_documents,
            evidence_layer="agent_proposal",
            catalog_id="agent.report",
        ),
        PipelineComponent(
            id="understanding-synthesis",
            kind="report",
            title=_text("Synthesize understanding", "Veri anlayışını birleştir"),
            description=_text(
                "Relate structured and document findings without changing data.",
                "Yapısal ve belge bulgularını veriyi değiştirmeden ilişkilendirir.",
            ),
            inputs=[
                _port(
                    "reports",
                    "Upstream briefings",
                    "Önceki özetler",
                    "reports",
                    multiple=True,
                )
            ],
            outputs=[
                _port("reports", "Cross-source synthesis", "Kaynaklar arası sentez", "reports")
            ],
            settings={"report_kind": "cross_source", "instructions": ""},
            enabled=has_tables or has_documents,
            evidence_layer="agent_proposal",
            catalog_id="agent.report",
        ),
        PipelineComponent(
            id="default-ml-pipeline",
            kind="template",
            title=_text("Default ML pipeline", "Varsayılan ML hattı"),
            description=_text(
                "Run integration through evaluation and final reporting with existing hard gates.",
                "Mevcut katı kontrollerle birleştirmeden değerlendirme ve son rapora "
                "kadar çalışır.",
            ),
            inputs=[
                _port("integrated_table", "Integrated table", "Birleşik tablo", "integrated_table"),
                _port(
                    "reports",
                    "Understanding reports",
                    "Anlama raporları",
                    "reports",
                    required=False,
                    multiple=True,
                ),
            ],
            outputs=[
                _port(
                    "trained_models",
                    "Models and metrics",
                    "Modeller ve metrikler",
                    "trained_models",
                ),
                _port("final_report", "Final report", "Son rapor", "final_report"),
            ],
            settings={"pipeline": "default_ml", "customizable": True},
            enabled=has_tables,
            evidence_layer="executor",
            catalog_id="template.default_ml",
        ),
    ]

    raw_connections = [
        ("source-to-intake", "data-source", "structured_files", "intake", "structured_files"),
        ("source-to-docs", "data-source", "documents", "understand-documents", "documents"),
        ("intake-to-schema", "intake", "table_profiles", "schema-discovery", "table_profiles"),
        ("intake-to-integrate", "intake", "table_profiles", "integrate-data", "table_profiles"),
        (
            "schema-to-integrate",
            "schema-discovery",
            "relationship_graph",
            "integrate-data",
            "relationship_graph",
        ),
        (
            "docs-to-review",
            "understand-documents",
            "extracted_tables",
            "review-document-tables",
            "extracted_tables",
        ),
        (
            "review-to-promote",
            "review-document-tables",
            "review_decisions",
            "promote-document-tables",
            "review_decisions",
        ),
        (
            "docs-to-promote",
            "understand-documents",
            "extracted_tables",
            "promote-document-tables",
            "extracted_tables",
        ),
        (
            "promote-to-integrate",
            "promote-document-tables",
            "accepted_tables",
            "integrate-data",
            "accepted_tables",
        ),
        ("intake-to-report", "intake", "table_profiles", "structured-brief", "table_profiles"),
        (
            "schema-to-report",
            "schema-discovery",
            "relationship_graph",
            "structured-brief",
            "relationship_graph",
        ),
        (
            "docs-to-document-brief",
            "understand-documents",
            "document_content",
            "document-brief",
            "document_content",
        ),
        (
            "figures-to-document-brief",
            "understand-documents",
            "document_figures",
            "document-brief",
            "document_figures",
        ),
        (
            "structured-brief-to-synthesis",
            "structured-brief",
            "reports",
            "understanding-synthesis",
            "reports",
        ),
        (
            "document-brief-to-synthesis",
            "document-brief",
            "reports",
            "understanding-synthesis",
            "reports",
        ),
        (
            "integrate-to-ml",
            "integrate-data",
            "integrated_table",
            "default-ml-pipeline",
            "integrated_table",
        ),
        (
            "synthesis-to-ml",
            "understanding-synthesis",
            "reports",
            "default-ml-pipeline",
            "reports",
        ),
    ]
    blueprint = PipelineBlueprint(
        name=_text("Multimodal data-to-ML workspace", "Çok kipli veriden ML'e çalışma alanı"),
        components=components,
        connections=[
            PipelineConnection(
                id=connection_id,
                source_component=source,
                source_port=source_port,
                target_component=target,
                target_port=target_port,
            )
            for connection_id, source, source_port, target, target_port in raw_connections
        ],
    )
    blueprint.validate_connections()
    return blueprint


_LEGACY_SETTING_RULES: dict[str, dict[str, Any]] = {
    "document_understanding": {
        "engine": {item["id"] for item in document_engine_catalog() if item["selectable"]},
        "ocr": {"auto", "always", "never"},
        "extract_tables": bool,
        "extract_figures": bool,
        "use_extracted_tables_for_training": bool,
    },
    "integration": {"include_document_tables": bool},
    "report": {
        "report_types": list,
        "report_kind": {
            "data_understanding",
            "document_understanding",
            "cross_source",
            "eda",
            "model_evaluation",
            "custom",
        },
        "instructions": str,
    },
    "ml_pipeline": {"pipeline": {"default_ml"}, "customizable": bool},
    "template": {
        "pipeline": {"default_ml"},
        "customizable": bool,
        "template": {"default_ml_v1"},
    },
    "problem_discovery": {
        "problem_title": str,
        "target_column": (str, type(None)),
        "task_type": {
            "binary_classification",
            "multiclass_classification",
            "regression",
            "anomaly_detection",
        },
        "primary_metric": {
            "roc_auc",
            "average_precision",
            "f1",
            "balanced_accuracy",
            "accuracy",
            "rmse",
            "mae",
            "r2",
            "mape",
            "silhouette",
        },
    },
    "validation": {"n_folds": int},
    "training": {"candidate_limit": int, "preferred_family": str},
}


def apply_component_updates(
    blueprint: PipelineBlueprint,
    updates: dict[str, dict[str, Any]],
    *,
    configured_by: str,
) -> PipelineBlueprint:
    """Apply bounded planner/human settings; unknown graph structure is rejected."""
    known = {component.id for component in blueprint.components}
    unknown = sorted(set(updates) - known)
    if unknown:
        raise ValueError(f"unknown pipeline components: {', '.join(unknown)}")
    changed: list[PipelineComponent] = []
    for component in blueprint.components:
        raw = updates.get(component.id)
        if raw is None:
            changed.append(component)
            continue
        allowed = _LEGACY_SETTING_RULES.get(component.kind, {})
        settings = dict(component.settings)
        settings.update(dict(raw.get("settings") or {}))
        if component.catalog_id:
            from ads.automation.settings import validate_component_settings

            try:
                settings = validate_component_settings(component.catalog_id, settings)
            except ValidationError as exc:
                raise ValueError(
                    f"one or more settings are not allowed for {component.catalog_id}"
                ) from exc
        else:
            for key, value in settings.items():
                rule = allowed.get(key)
                if rule is None:
                    raise ValueError(f"setting {key!r} is not allowed for {component.kind}")
                if isinstance(rule, set) and value not in rule:
                    raise ValueError(f"invalid value for {component.id}.{key}")
                if isinstance(rule, (type, tuple)) and not isinstance(value, rule):
                    raise ValueError(f"invalid type for {component.id}.{key}")
        enabled = raw.get("enabled", component.enabled)
        if not isinstance(enabled, bool):
            raise ValueError(f"enabled must be boolean for {component.id}")
        control = PipelineNodeControl.model_validate(raw.get("control", component.control))
        changed.append(
            component.model_copy(
                update={
                    "settings": settings,
                    "control": control,
                    "enabled": enabled,
                    "configured_by": configured_by,
                }
            )
        )
    result = blueprint.model_copy(update={"components": changed})
    result.validate_connections()
    return result


def validate_executable_blueprint(blueprint: PipelineBlueprint) -> None:
    """Reject an accepted run whose enabled components lack required inputs."""
    blueprint.validate_connections()
    enabled = {component.id: component for component in blueprint.components if component.enabled}
    incoming = {
        (connection.target_component, connection.target_port)
        for connection in blueprint.connections
        if connection.source_component in enabled and connection.target_component in enabled
    }
    for component in enabled.values():
        missing = [
            port.id
            for port in component.inputs
            if port.required and (component.id, port.id) not in incoming
        ]
        if missing:
            raise ValueError(
                f"pipeline component {component.id!r} is missing inputs: {', '.join(missing)}"
            )
