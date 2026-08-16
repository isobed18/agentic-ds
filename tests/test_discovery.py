"""Deterministic problem-support measurement tests.

These encode policy, not just behaviour. The thresholds here decide whether a
human is offered a project as viable, so a silent drift in them is a product
change — each is asserted explicitly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ads.agents.problem_discovery import (
    attach_support,
    build_context,
    validate_candidates_are_distinct,
    validate_metric_matches_task,
    validate_targets_exist,
    validate_task_matches_target_shape,
)
from ads.contracts import (
    DataCard,
    Metric,
    ProblemDiscoveryProposal,
    SemanticType,
    Sensitivity,
    TaskType,
)
from ads.discovery import MIN_MINORITY_COUNT, compute_support, usable_feature_columns
from ads.intake import LoadedTable, profile_table


def _card(frame: pd.DataFrame, name: str = "abt") -> DataCard:
    return profile_table(
        LoadedTable(name=name, frame=frame, source_uri="mem", source_format="csv")
    )


#: Positives planted in `rare_flag`. Exact rather than sampled: at a 0.3% rate
#: over 800 rows the RNG can produce zero positives, which makes the column a
#: constant and blocks it for the wrong reason.
RARE_POSITIVES = 12


@pytest.fixture
def abt() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 800

    rare_flag = np.zeros(n, dtype=int)
    rare_flag[rng.permutation(n)[:RARE_POSITIVES]] = 1

    return pd.DataFrame(
        {
            "physician_id": np.arange(1, n + 1),
            "specialty": rng.choice(["cardio", "onco", "peds"], n),
            "years_experience": rng.integers(1, 35, n),
            "annual_comp": rng.normal(300_000, 50_000, n).round(2),
            "region_code": "TR",
            "rare_flag": rare_flag,
            "balanced_flag": (rng.random(n) < 0.4).astype(int),
        }
    )


class TestFeatureSelection:
    def test_identifiers_and_constants_excluded(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        names = {c.name for c in usable_feature_columns(card, "annual_comp")}
        assert "physician_id" not in names, "an id feature lets the model memorise rows"
        assert "region_code" not in names, "a constant carries no signal"
        assert "annual_comp" not in names, "the target is not a feature"
        assert "specialty" in names

    def test_pii_is_ineligible_independently_of_text_semantics(self) -> None:
        frame = pd.DataFrame(
            {
                "contact_email": [f"person{i}@example.test" for i in range(120)],
                "public_comment": [
                    f"Detailed public product review number {i} with useful narrative."
                    for i in range(120)
                ],
                "target": np.linspace(0.0, 1.0, 120),
            }
        )
        card = _card(frame)
        email = card.column("contact_email")
        comment = card.column("public_comment")
        assert email is not None and comment is not None
        assert email.semantic_type is SemanticType.TEXT
        assert comment.semantic_type is SemanticType.TEXT
        assert email.sensitivity is Sensitivity.PII
        assert comment.sensitivity is not Sensitivity.PII

        features = {
            item.name for item in usable_feature_columns(card, "target")
        }
        targets = {item.name for item in card.candidate_targets()}
        assert "contact_email" not in features
        assert "contact_email" not in targets
        assert "public_comment" in features

    def test_explicit_exclusions_respected(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        names = {
            c.name
            for c in usable_feature_columns(card, "annual_comp", frozenset({"specialty"}))
        }
        assert "specialty" not in names


class TestBlockingReasons:
    def test_rare_positive_class_blocks_classification(self, abt: pd.DataFrame) -> None:
        """The headline case: 'fraud detection' rejected on a measured count."""
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="rare_flag", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert not support.is_viable
        assert support.n_classes == 2, "must block on the count, not on being constant"
        assert support.minority_class_count == RARE_POSITIVES
        assert support.minority_class_count < MIN_MINORITY_COUNT
        assert any("minority_class_below_floor" in r for r in support.blocking_reasons)

    def test_balanced_class_is_viable(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="balanced_flag", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert support.is_viable
        assert support.n_classes == 2

    def test_regression_target_is_viable(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert support.is_viable
        assert support.n_rows == 800
        assert support.n_usable_features > 0

    def test_identifier_target_blocked(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="physician_id", task_type=TaskType.REGRESSION
        )
        assert not support.is_viable
        assert any("target_is_identifier" in r for r in support.blocking_reasons)

    def test_constant_target_blocked(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="region_code",
            task_type=TaskType.MULTICLASS_CLASSIFICATION,
        )
        assert not support.is_viable

    def test_unknown_target_blocked(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="does_not_exist", task_type=TaskType.REGRESSION
        )
        assert not support.is_viable
        assert any("unknown_target" in r for r in support.blocking_reasons)

    def test_categorical_target_blocked_for_regression(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="specialty", task_type=TaskType.REGRESSION
        )
        assert not support.is_viable
        assert any("task_target_mismatch" in r for r in support.blocking_reasons)

    def test_multiclass_target_blocked_for_binary(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="specialty",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        assert not support.is_viable

    def test_too_few_rows_blocked(self) -> None:
        frame = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [10.0, 20.0, 30.5]})
        support = compute_support(
            _card(frame), frame, target_column="y", task_type=TaskType.REGRESSION
        )
        assert not support.is_viable
        assert any("insufficient_rows" in r for r in support.blocking_reasons)

    def test_mostly_null_target_blocked(self, abt: pd.DataFrame) -> None:
        abt = abt.copy()
        abt.loc[abt.index[:700], "annual_comp"] = np.nan
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert not support.is_viable
        assert any("target_too_sparse" in r for r in support.blocking_reasons)


class TestWarnings:
    def test_partial_target_missingness_warns_not_blocks(self, abt: pd.DataFrame) -> None:
        abt = abt.copy()
        abt.loc[abt.index[:88], "annual_comp"] = np.nan  # ~11%, as in the real data
        card = _card(abt)
        support = compute_support(
            card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert support.is_viable
        assert any("target_missingness" in w for w in support.warnings)

    def test_imbalance_above_floor_warns(self) -> None:
        rng = np.random.default_rng(3)
        n = 5000
        frame = pd.DataFrame(
            {
                "feature": rng.normal(size=n),
                "label": (rng.random(n) < 0.03).astype(int),
            }
        )
        support = compute_support(
            _card(frame), frame, target_column="label",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        assert support.is_viable, "above the floor, so workable but hard"
        assert any("severe_imbalance" in w for w in support.warnings)


class TestUnsupervised:
    def test_anomaly_detection_needs_no_target(self, abt: pd.DataFrame) -> None:
        support = compute_support(
            _card(abt), abt, target_column=None, task_type=TaskType.ANOMALY_DETECTION
        )
        assert support.is_viable
        assert support.n_classes is None


def _proposal(**overrides) -> ProblemDiscoveryProposal:
    candidate = {
        "title": "Predict physician compensation",
        "task_type": "regression",
        "target_column": "annual_comp",
        "business_rationale": "HR benchmarking for offer decisions.",
        "evidence_columns": ["years_experience", "specialty"],
        "primary_metric": "rmse",
    }
    candidate.update(overrides)
    return ProblemDiscoveryProposal.model_validate({"candidates": [candidate]})


class TestProblemDiscoveryValidators:
    @pytest.fixture
    def context(self, abt: pd.DataFrame):
        return build_context(_card(abt))

    def test_valid_proposal_passes(self, context) -> None:
        proposal = _proposal()
        assert validate_targets_exist(proposal, context) == []
        assert validate_task_matches_target_shape(proposal, context) == []
        assert validate_metric_matches_task(proposal, context) == []

    def test_unknown_target_rejected(self, context) -> None:
        failures = validate_targets_exist(_proposal(target_column="salary"), context)
        assert any(f.code == "unknown_target" for f in failures)

    def test_unknown_evidence_column_rejected(self, context) -> None:
        failures = validate_targets_exist(
            _proposal(evidence_columns=["nonexistent"]), context
        )
        assert any(f.code == "unknown_column" for f in failures)

    def test_binary_on_multivalue_target_rejected(self, context) -> None:
        proposal = _proposal(
            target_column="specialty", task_type="binary_classification",
            primary_metric="roc_auc",
        )
        failures = validate_task_matches_target_shape(proposal, context)
        assert any(f.code == "task_target_mismatch" for f in failures)

    def test_regression_on_categorical_rejected(self, context) -> None:
        proposal = _proposal(target_column="specialty")
        failures = validate_task_matches_target_shape(proposal, context)
        assert any(f.code == "task_target_mismatch" for f in failures)

    def test_duplicate_framings_rejected(self, context) -> None:
        one = _proposal().candidates[0].model_dump(mode="json")
        two = {**one, "title": "A differently-titled but identical framing"}
        proposal = ProblemDiscoveryProposal.model_validate({"candidates": [one, two]})
        failures = validate_candidates_are_distinct(proposal, context)
        assert any(f.code == "duplicate_framing" for f in failures)

    def test_candidate_ids_are_system_assigned(self, context) -> None:
        """Models emit empty strings for fields that carry no meaning to them."""
        from ads.contracts import ProblemCandidateProposal

        assert "candidate_id" not in ProblemCandidateProposal.model_fields


class TestAttachSupport:
    def test_support_is_measured_not_authored(self, abt: pd.DataFrame) -> None:
        card = _card(abt)
        result = attach_support(_proposal(), card, abt)
        candidate = result.candidates[0]
        assert candidate.support.n_rows == 800
        assert candidate.support.is_viable

    def test_viable_candidates_ranked_first(self, abt: pd.DataFrame) -> None:
        blocked = _proposal().candidates[0].model_dump(mode="json")
        blocked.update(
            target_column="rare_flag",
            task_type="binary_classification", primary_metric="average_precision",
            title="Fraud detection",
        )
        viable = _proposal().candidates[0].model_dump(mode="json")
        proposal = ProblemDiscoveryProposal.model_validate(
            {"candidates": [blocked, viable]}
        )
        result = attach_support(proposal, _card(abt), abt)

        assert result.candidates[0].title == "Predict physician compensation"
        assert len(result.viable()) == 1
        assert len(result.blocked()) == 1

    def test_blocked_candidates_are_kept_not_hidden(self, abt: pd.DataFrame) -> None:
        """'Your fraud data has 2 labelled cases' is a finding, not noise."""
        proposal = _proposal(
            target_column="rare_flag",
            task_type="binary_classification", primary_metric="average_precision",
        )
        result = attach_support(proposal, _card(abt), abt)
        assert len(result.candidates) == 1
        assert not result.candidates[0].support.is_viable

    def test_candidate_set_summary(self, abt: pd.DataFrame) -> None:
        result = attach_support(_proposal(), _card(abt), abt, user_intent="predict pay")
        assert result.summary()["n_viable"] == 1
        assert result.user_intent == "predict pay"


class TestContext:
    def test_context_lists_measured_target_counts(self, abt: pd.DataFrame) -> None:
        context = build_context(_card(abt))
        rendered = context.render()
        assert "CANDIDATE TARGET COLUMNS" in rendered
        assert "annual_comp" in rendered

    def test_user_intent_included_when_given(self, abt: pd.DataFrame) -> None:
        context = build_context(_card(abt), user_intent="detect fraud")
        assert "detect fraud" in context.render()

    def test_context_fits_budget(self, abt: pd.DataFrame) -> None:
        assert build_context(_card(abt)).estimated_tokens() < 3000


class TestMetricVocabulary:
    def test_metric_enum_is_closed(self) -> None:
        assert Metric("roc_auc") is Metric.ROC_AUC
        with pytest.raises(ValueError):
            Metric("some_made_up_metric")
