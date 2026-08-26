"""Closed registry of graph edge contracts.

The visual editor and Planner may name contracts, but only host-registered IDs
can enter a blueprint.  Legacy aliases keep stored v1 workspaces readable while
new catalog definitions use versioned ``ads.*@1`` identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass

from ads.contracts.base import ArtifactType


@dataclass(frozen=True)
class ContractDefinition:
    contract_id: str
    artifact_type: ArtifactType | None
    description: str
    durable: bool = True


_DEFINITIONS = (
    ContractDefinition("ads.structured_files@1", None, "Uploaded structured source files", False),
    ContractDefinition("ads.documents@1", None, "Uploaded source documents", False),
    ContractDefinition("ads.table_profiles@1", ArtifactType.DATA_CARD, "Measured table profiles"),
    ContractDefinition(
        "ads.relationship_graph@1",
        ArtifactType.MEASUREMENT_BUNDLE,
        "Measured relationship evidence",
    ),
    ContractDefinition(
        "ads.document_content@1", ArtifactType.DOCUMENT_EXTRACTION, "Normalized document content"
    ),
    ContractDefinition(
        "ads.document_table_candidates@1",
        ArtifactType.DOCUMENT_EXTRACTION,
        "Unreviewed document table candidates",
    ),
    ContractDefinition(
        "ads.document_table_review@1",
        ArtifactType.DOCUMENT_TABLE_REVIEW,
        "Human candidate decisions",
    ),
    ContractDefinition(
        "ads.document_figures@1", ArtifactType.DOCUMENT_EXTRACTION, "Document figure metadata"
    ),
    ContractDefinition(
        "ads.table_asset@1", ArtifactType.TABLE_ASSET, "Immutable ordered Parquet table"
    ),
    ContractDefinition(
        "ads.integration_plan@1", ArtifactType.INTEGRATION_PLAN, "Measured integration decision"
    ),
    ContractDefinition("ads.staging_report@1", ArtifactType.STAGING_REPORT, "Bounded agent report"),
    ContractDefinition(
        "ads.runtime_plan@1", ArtifactType.STAGING_WORKSPACE, "Accepted runtime configuration"
    ),
    ContractDefinition(
        "ads.problem_definition@1", ArtifactType.PROBLEM_DEFINITION, "Accepted ML problem"
    ),
    ContractDefinition(
        "ads.validation_strategy@1", ArtifactType.VALIDATION_STRATEGY, "Validation semantics"
    ),
    ContractDefinition(
        "ads.eda_report@1", ArtifactType.EDA_REPORT, "Deterministic EDA measurements"
    ),
    ContractDefinition(
        "ads.leakage_report@1", ArtifactType.LEAKAGE_REPORT, "Leakage audit evidence"
    ),
    ContractDefinition(
        "ads.feature_spec@1", ArtifactType.FEATURE_SPEC, "Feature construction specification"
    ),
    ContractDefinition(
        "ads.split_manifest@1", ArtifactType.SPLIT_MANIFEST, "Exact durable split selections"
    ),
    ContractDefinition(
        "ads.training_report@1", ArtifactType.TRAINED_MODEL, "Candidate training results"
    ),
    ContractDefinition(
        "ads.evaluation_report@1", ArtifactType.EVALUATION_REPORT, "Model evaluation"
    ),
    ContractDefinition("ads.final_report@1", ArtifactType.FINAL_REPORT, "Published final report"),
    ContractDefinition(
        "ads.model_assets@1", ArtifactType.TRAINED_MODEL, "Persisted fitted model assets"
    ),
)

CONTRACT_REGISTRY = {definition.contract_id: definition for definition in _DEFINITIONS}

LEGACY_CONTRACT_ALIASES = {
    "structured_files": "ads.structured_files@1",
    "documents": "ads.documents@1",
    "table_profiles": "ads.table_profiles@1",
    "relationship_graph": "ads.relationship_graph@1",
    "document_content": "ads.document_content@1",
    "extracted_tables": "ads.document_table_candidates@1",
    "review_decisions": "ads.document_table_review@1",
    "accepted_tables": "ads.table_asset@1",
    "document_figures": "ads.document_figures@1",
    "integrated_table": "ads.table_asset@1",
    "reports": "ads.staging_report@1",
    "runtime_plan": "ads.runtime_plan@1",
    "problem_definition": "ads.problem_definition@1",
    "validation_strategy": "ads.validation_strategy@1",
    "eda_artifacts": "ads.eda_report@1",
    "leakage_report": "ads.leakage_report@1",
    "feature_spec": "ads.feature_spec@1",
    "split_manifest": "ads.split_manifest@1",
    "trained_models": "ads.training_report@1",
    "evaluation_report": "ads.evaluation_report@1",
    "final_report": "ads.final_report@1",
    "model_artifacts": "ads.model_assets@1",
}


def canonical_contract_id(contract_id: str) -> str:
    canonical = LEGACY_CONTRACT_ALIASES.get(contract_id, contract_id)
    if canonical not in CONTRACT_REGISTRY:
        raise ValueError(f"unregistered graph contract {contract_id!r}")
    return canonical


def contract_definition(contract_id: str) -> ContractDefinition:
    return CONTRACT_REGISTRY[canonical_contract_id(contract_id)]


__all__ = [
    "CONTRACT_REGISTRY",
    "ContractDefinition",
    "LEGACY_CONTRACT_ALIASES",
    "canonical_contract_id",
    "contract_definition",
]
