"""Pydantic settings schemas for registered automation capabilities."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ads.contracts.base import FrozenModel


class EmptySettings(FrozenModel):
    pass


class DocumentExtractSettings(FrozenModel):
    engine: Literal["docling", "unstructured", "marker", "mineru", "text_layer"] = "docling"
    ocr: Literal["auto", "always", "never"] = "auto"
    extract_tables: bool = True
    extract_figures: bool = True
    use_extracted_tables_for_training: Literal[False] = False


class IntegrationSettings(FrozenModel):
    include_document_tables: bool = False


class ReportSettings(FrozenModel):
    report_types: list[str] = Field(default_factory=list)
    report_kind: Literal[
        "data_understanding",
        "document_understanding",
        "cross_source",
        "eda",
        "model_evaluation",
        "custom",
    ] = "data_understanding"
    instructions: str = ""


class ProblemSettings(FrozenModel):
    problem_title: str | None = None
    target_column: str | None = None
    task_type: (
        Literal[
            "binary_classification",
            "multiclass_classification",
            "regression",
            "anomaly_detection",
        ]
        | None
    ) = None
    primary_metric: (
        Literal[
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
        ]
        | None
    ) = None


class ValidationSettings(FrozenModel):
    n_folds: int = Field(default=5, ge=2, le=20)


class TrainingSettings(FrozenModel):
    candidate_limit: int | None = Field(default=None, ge=1, le=20)
    preferred_family: str | None = None


class DefaultMLTemplateSettings(FrozenModel):
    template: Literal["default_ml_v1"] = "default_ml_v1"
    pipeline: Literal["default_ml"] | None = None
    customizable: bool = True


SETTINGS_MODELS: dict[str, type[FrozenModel]] = {
    "data.upload": EmptySettings,
    "data.profile_tables": EmptySettings,
    "data.find_relationships": EmptySettings,
    "document.extract": DocumentExtractSettings,
    "document.review_tables": EmptySettings,
    "document.promote_tables": EmptySettings,
    "data.integrate": IntegrationSettings,
    "agent.report": ReportSettings,
    "agent.define_problem": ProblemSettings,
    "agent.plan_validation": ValidationSettings,
    "analysis.eda": EmptySettings,
    "analysis.leakage": EmptySettings,
    "data.features": EmptySettings,
    "data.split": EmptySettings,
    "ml.train": TrainingSettings,
    "ml.evaluate": EmptySettings,
    "report.final": EmptySettings,
    "template.default_ml": DefaultMLTemplateSettings,
}


def settings_model(catalog_id: str) -> type[FrozenModel]:
    try:
        return SETTINGS_MODELS[catalog_id]
    except KeyError as exc:
        raise ValueError(f"no settings schema registered for {catalog_id!r}") from exc


def validate_component_settings(catalog_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    return settings_model(catalog_id).model_validate(raw).model_dump(mode="json", exclude_none=True)


def component_settings_schema(catalog_id: str) -> dict[str, Any]:
    return settings_model(catalog_id).model_json_schema()


__all__ = [
    "SETTINGS_MODELS",
    "component_settings_schema",
    "settings_model",
    "validate_component_settings",
]
