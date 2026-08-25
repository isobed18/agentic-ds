"""Contract validation tests.

These assert the guarantees the rest of the architecture leans on: contracts are
immutable, reject structurally invalid states, and refuse extra fields (which is
how a hallucinated key from an LLM gets caught at layer 1 rather than silently
carried downstream).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ads.contracts import (
    BUILTIN_PROFILES,
    ArtifactType,
    Cardinality,
    CritiqueResult,
    Metric,
    ProblemCandidateProposal,
    ProblemSupport,
    RelationshipCandidate,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)


def _support(**overrides) -> ProblemSupport:
    base = dict(
        n_rows=1000,
        target_null_rate=0.0,
        n_usable_features=10,
        rows_per_feature=100.0,
    )
    return ProblemSupport(**{**base, **overrides})


class TestImmutability:
    def test_contracts_are_frozen(self) -> None:
        strategy = ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="x")
        with pytest.raises(ValidationError):
            strategy.n_folds = 10  # type: ignore[misc]

    def test_extra_fields_rejected(self) -> None:
        """An LLM inventing a field must fail validation, not be silently kept."""
        with pytest.raises(ValidationError):
            ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="x", made_up_field=1)


class TestValidationStrategy:
    def test_grouped_requires_group_column(self) -> None:
        with pytest.raises(ValidationError, match="group_column"):
            ValidationStrategy(strategy=SplitStrategy.GROUPED, rationale="x")

    def test_temporal_requires_time_column(self) -> None:
        with pytest.raises(ValidationError, match="time_column"):
            ValidationStrategy(strategy=SplitStrategy.TEMPORAL, rationale="x")

    def test_grouped_temporal_requires_both(self) -> None:
        with pytest.raises(ValidationError):
            ValidationStrategy(
                strategy=SplitStrategy.GROUPED_TEMPORAL,
                group_column="physician_id",
                rationale="x",
            )
        ok = ValidationStrategy(
            strategy=SplitStrategy.GROUPED_TEMPORAL,
            group_column="physician_id",
            time_column="txn_date",
            rationale="entity repeats across a 2019-2024 span",
        )
        assert ok.group_column == "physician_id"

    def test_random_needs_no_extra_columns(self) -> None:
        assert ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="iid").n_folds == 5


class TestProblemCandidate:
    @pytest.mark.parametrize(
        "task_type",
        [TaskType.BINARY_CLASSIFICATION, TaskType.REGRESSION, TaskType.MULTICLASS_CLASSIFICATION],
    )
    def test_supervised_requires_target(self, task_type: TaskType) -> None:
        with pytest.raises(ValidationError, match="target_column"):
            ProblemCandidateProposal(
                title="t",
                title_tr="t",
                task_type=task_type,
                business_rationale="r",
                business_rationale_tr="r",
                primary_metric=Metric.ROC_AUC,
            )

    def test_anomaly_detection_needs_no_target(self) -> None:
        candidate = ProblemCandidateProposal(
            title="Anomaly detection",
            title_tr="Anomali tespiti",
            task_type=TaskType.ANOMALY_DETECTION,
            business_rationale="no label available",
            business_rationale_tr="etiket mevcut değil",
            primary_metric=Metric.SILHOUETTE,
        )
        assert candidate.target_column is None

    def test_metric_must_match_task_type(self) -> None:
        """A closed vocabulary is worthless if any metric fits any task."""
        with pytest.raises(ValidationError, match="not valid for"):
            ProblemCandidateProposal(
                title="t",
                title_tr="t",
                task_type=TaskType.REGRESSION,
                target_column="annual_comp",
                business_rationale="r",
                business_rationale_tr="r",
                primary_metric=Metric.ROC_AUC,
            )

    def test_blocking_reasons_make_it_non_viable(self) -> None:
        support = _support(blocking_reasons=["minority_class_below_floor"])
        assert not support.is_viable
        assert _support().is_viable


class TestRelationshipCandidate:
    def test_mismatched_key_widths_rejected(self) -> None:
        with pytest.raises(ValidationError, match="equal length"):
            RelationshipCandidate(
                from_table="a",
                from_columns=["x"],
                to_table="b",
                to_columns=["y", "z"],
                overlap_rate=1.0,
                orphan_rate=0.0,
                n_from_distinct=1,
                n_to_distinct=1,
            )

    def test_confidence_zero_when_dtypes_incompatible(self) -> None:
        rel = RelationshipCandidate(
            from_table="a",
            from_columns=["x"],
            to_table="b",
            to_columns=["y"],
            overlap_rate=0.99,
            orphan_rate=0.01,
            parent_coverage=1.0,
            n_from_distinct=10,
            n_to_distinct=10,
            cardinality=Cardinality.MANY_TO_ONE,
            dtype_compatible=False,
        )
        assert rel.confidence == 0.0

    def test_confidence_rewards_coverage_and_names(self) -> None:
        weak = RelationshipCandidate(
            from_table="a",
            from_columns=["x"],
            to_table="b",
            to_columns=["y"],
            overlap_rate=0.9,
            orphan_rate=0.1,
            parent_coverage=0.05,
            n_from_distinct=10,
            n_to_distinct=200,
        )
        strong = RelationshipCandidate(
            from_table="a",
            from_columns=["physician_id"],
            to_table="b",
            to_columns=["physician_id"],
            overlap_rate=0.9,
            orphan_rate=0.1,
            parent_coverage=1.0,
            name_affinity=1.0,
            n_from_distinct=10,
            n_to_distinct=10,
        )
        assert strong.confidence > weak.confidence


class TestCritique:
    def test_critique_has_no_confidence_field(self) -> None:
        """Guards the design rule that self-reported confidence never gates anything."""
        assert "confidence" not in CritiqueResult.model_fields

    def test_artifact_type_and_summary(self) -> None:
        critique = CritiqueResult(stage_id="eda", rubric_version="eda.v1")
        assert critique.artifact_type is ArtifactType.CRITIQUE
        assert critique.summary()["stage_id"] == "eda"
        assert critique.has_errors is False


class TestAutonomyProfiles:
    def test_all_builtin_profiles_are_named_consistently(self) -> None:
        for key, profile in BUILTIN_PROFILES.items():
            assert profile.name == key

    def test_full_auto_has_no_checkpoints(self) -> None:
        assert BUILTIN_PROFILES["full_auto"].checkpoint_stages == []

    def test_supervised_is_the_most_restrictive(self) -> None:
        supervised = BUILTIN_PROFILES["supervised"]
        checkpointed = BUILTIN_PROFILES["checkpointed"]
        assert len(supervised.checkpoint_stages) > len(checkpointed.checkpoint_stages)
        assert supervised.max_auto_retries < checkpointed.max_auto_retries
