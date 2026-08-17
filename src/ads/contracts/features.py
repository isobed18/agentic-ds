"""Executor-owned contract for the feature preprocessing boundary."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType
from ads.contracts.datacard import DataCard, SemanticType


class FeatureSpec(Artifact):
    """Deterministic column routing consumed by fold-local model training.

    This artifact describes which source columns enter each registered
    transformer. It is not a fitted pipeline and contains no learned statistics.
    Those are fitted independently inside training folds and verified by the
    training executor.
    """

    artifact_type: ClassVar[ArtifactType] = ArtifactType.FEATURE_SPEC
    schema_version: ClassVar[str] = "1"

    target_column: str
    input_columns: list[str] = Field(min_length=1)
    excluded_columns: list[str] = Field(default_factory=list)
    numeric_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    datetime_columns: list[str] = Field(default_factory=list)
    dropped_columns: list[str] = Field(default_factory=list)
    preprocessing_scope: Literal["fit_per_training_fold"] = "fit_per_training_fold"
    source_mutation_allowed: Literal[False] = False
    selection_authority: Literal["deterministic_floor"] = "deterministic_floor"

    @model_validator(mode="after")
    def _columns_are_accounted_for_once(self) -> FeatureSpec:
        if len(self.input_columns) != len(set(self.input_columns)):
            raise ValueError("Feature input columns must be unique.")
        routed = [
            *self.numeric_columns,
            *self.categorical_columns,
            *self.datetime_columns,
            *self.dropped_columns,
        ]
        if len(routed) != len(set(routed)):
            raise ValueError("A feature column cannot appear in more than one route.")
        expected = set(self.input_columns) - {self.target_column}
        if set(routed) != expected:
            missing = sorted(expected - set(routed))
            extra = sorted(set(routed) - expected)
            raise ValueError(
                f"Feature routes must account for every non-target input; "
                f"missing={missing}, extra={extra}."
            )
        if not set(self.excluded_columns) <= set(self.dropped_columns):
            raise ValueError("Every explicit exclusion must use the dropped route.")
        return self

    @classmethod
    def from_card(
        cls,
        card: DataCard,
        *,
        target_column: str,
        excluded_columns: set[str] | frozenset[str],
    ) -> FeatureSpec:
        """Route a profiled frame through the registered deterministic floor."""
        if target_column not in card.column_names:
            raise ValueError(f"Target column {target_column!r} is absent from the DataCard.")
        unknown = set(excluded_columns) - set(card.column_names)
        if unknown:
            raise ValueError(f"Feature exclusions name absent columns: {sorted(unknown)}")
        numeric_types = {
            SemanticType.NUMERIC_CONTINUOUS,
            SemanticType.NUMERIC_DISCRETE,
        }
        categorical_types = {SemanticType.CATEGORICAL, SemanticType.BOOLEAN}
        numeric: list[str] = []
        categorical: list[str] = []
        datetime: list[str] = []
        dropped: list[str] = []
        excluded = set(excluded_columns)
        for column in card.columns:
            if column.name == target_column:
                continue
            if column.name in excluded:
                dropped.append(column.name)
            elif column.semantic_type in numeric_types:
                numeric.append(column.name)
            elif column.semantic_type in categorical_types:
                categorical.append(column.name)
            elif column.semantic_type is SemanticType.DATETIME:
                datetime.append(column.name)
            else:
                dropped.append(column.name)
        return cls(
            target_column=target_column,
            input_columns=card.column_names,
            excluded_columns=sorted(excluded),
            numeric_columns=numeric,
            categorical_columns=categorical,
            datetime_columns=datetime,
            dropped_columns=dropped,
        )

    def summary(self) -> dict[str, object]:
        return {
            "target_column": self.target_column,
            "modeled_columns": (
                len(self.numeric_columns)
                + len(self.categorical_columns)
                + len(self.datetime_columns)
            ),
            "dropped_columns": len(self.dropped_columns),
            "preprocessing_scope": self.preprocessing_scope,
            "selection_authority": self.selection_authority,
        }


__all__ = ["FeatureSpec"]
