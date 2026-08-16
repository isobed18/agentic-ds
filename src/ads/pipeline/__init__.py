"""Orchestrated adapters for the deterministic ADS pipeline."""

from ads.pipeline.rubrics import build_pipeline_rubrics
from ads.pipeline.stages import (
    ABT_FRAME_KEY,
    DROPPED_FEATURES_KEY,
    FINAL_MARKDOWN_KEY,
    INTEGRATION_GRAIN_PRESERVED_KEY,
    MODEL_FRAME_KEY,
    SPLIT_DIAGNOSTICS_KEY,
    TRAINING_FRAME_COLUMNS_KEY,
    FinalReport,
    configure_full_pipeline_state,
    configure_pipeline_state,
)
from ads.pipeline.workflow import (
    build_default_registry,
    build_default_spec,
    build_full_registry,
    build_full_spec,
    build_full_spec_definition,
)
from ads.store import register_artifact_type

register_artifact_type(FinalReport)

__all__ = [
    "ABT_FRAME_KEY",
    "DROPPED_FEATURES_KEY",
    "FINAL_MARKDOWN_KEY",
    "MODEL_FRAME_KEY",
    "SPLIT_DIAGNOSTICS_KEY",
    "TRAINING_FRAME_COLUMNS_KEY",
    "FinalReport",
    "INTEGRATION_GRAIN_PRESERVED_KEY",
    "build_default_registry",
    "build_default_spec",
    "build_full_registry",
    "build_full_spec",
    "build_full_spec_definition",
    "build_pipeline_rubrics",
    "configure_full_pipeline_state",
    "configure_pipeline_state",
]
