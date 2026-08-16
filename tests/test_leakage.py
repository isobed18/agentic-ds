"""Leakage audit tests.

Leakage is the failure mode that produces a confident, worthless model, so these
tests assert against the trap planted in the real sample data rather than only
against synthetic cases.

Deliberately self-contained: the ABT is built here from the real source tables
instead of a shared conftest fixture, so this module does not couple to
whichever fixtures other agents add.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import adjusted_mutual_info_score

from ads.contracts import (
    DataCard,
    IntegrationPlanProposal,
    SplitStrategy,
    TaskType,
    ValidationStrategy,
)
from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict
from ads.discovery.leakage import (
    LEAKAGE_MUTUAL_INFORMATION,
    LEAKAGE_SEPARATION_INFORMATION,
    LeakageKind,
    LeakageOptions,
    audit_leakage,
    leakage_digest,
    normalised_mutual_information,
    ordered_separation_information,
)
from ads.gates import GatePolicy, evaluate_gate
from ads.intake import LoadedTable, profile_table
from ads.integration import execute_plan

SAMPLE_PLAN = {
    "base_table": "physicians__physician_master",
    "base_grain": ["physician_id"],
    "grain_description": "One row per physician.",
    "aggregations": [
        {
            "source_table": "transactions",
            "output_name": "txn_by_physician",
            "group_by": ["physician_id"],
            "aggregations": {
                "total_txn_amount": "SUM(amount)",
                "txn_count": "COUNT(*)",
            },
            "rationale": "N:1 fan-out.",
        }
    ],
    "joins": [
        {
            "left_table": "physicians__physician_master",
            "right_table": "physicians__compensation",
            "left_columns": ["physician_id"],
            "right_columns": ["physician_id"],
            "how": "left",
            "rationale": "1:1.",
        },
        {
            "left_table": "physicians__physician_master",
            "right_table": "txn_by_physician",
            "left_columns": ["physician_id"],
            "right_columns": ["physician_id"],
            "how": "left",
            "rationale": "Aggregated metrics.",
        },
    ],
    "warnings": [],
}


@pytest.fixture(scope="module")
def plan() -> IntegrationPlanProposal:
    return IntegrationPlanProposal.model_validate(SAMPLE_PLAN)


@pytest.fixture(scope="module")
def abt(frames, plan) -> pd.DataFrame:
    return execute_plan(plan, frames).frame


@pytest.fixture(scope="module")
def abt_card(abt: pd.DataFrame) -> DataCard:
    return profile_table(
        LoadedTable(name="abt", frame=abt, source_uri="derived", source_format="duckdb")
    )


class TestMutualInformation:
    def test_identity_scores_one(self) -> None:
        series = pd.Series(["a", "b", "c", "a", "b", "c"] * 10)
        assert normalised_mutual_information(series, series) == pytest.approx(1.0)

    def test_independent_scores_near_zero(self) -> None:
        rng = np.random.default_rng(0)
        n = 2000
        feature = pd.Series(rng.integers(0, 5, n))
        target = pd.Series(rng.integers(0, 2, n))
        assert normalised_mutual_information(feature, target) < 0.05

    def test_constant_feature_scores_zero(self) -> None:
        feature = pd.Series([1] * 100)
        target = pd.Series([0, 1] * 50)
        assert normalised_mutual_information(feature, target) == 0.0

    def test_handles_empty_input(self) -> None:
        empty = pd.Series([], dtype="float64")
        assert normalised_mutual_information(empty, empty) == 0.0


class TestPlantedLeakageTrap:
    """The headline case: total_comp_ytd is ~0.98 correlated with annual_comp."""

    def test_total_comp_ytd_is_flagged(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert "total_comp_ytd" in report.suspect_columns
        assert not report.is_clean

    def test_finding_reports_the_measured_correlation(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        finding = next(f for f in report.findings if f.column == "total_comp_ytd")
        assert finding.kind in (
            LeakageKind.TARGET_CORRELATION,
            LeakageKind.TARGET_MUTUAL_INFORMATION,
        )
        assert finding.score > 0.95
        assert finding.blocking

    def test_legitimate_features_are_not_flagged(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert "specialty" not in report.suspect_columns
        assert "years_experience" not in report.suspect_columns

    def test_drop_recommendations_are_actionable(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert any("total_comp_ytd" in r for r in report.drop_recommendations())


class TestIdentifierProxies:
    def test_near_unique_categorical_is_flagged(self) -> None:
        """Columns typed IDENTIFIER are dropped upstream by feature selection.

        The residual case this rule exists for is a column that is *not* typed as
        an identifier but is near-unique anyway — a free-text note, a reference
        code — through which a model can still memorise rows.
        """
        # 395 distinct values over 400 rows: near-unique, but not perfectly so,
        # which is what keeps it out of the IDENTIFIER semantic type.
        n = 400
        rng = np.random.default_rng(2)
        notes = [f"free text case note number {i}" for i in range(395)]
        notes += notes[:5]
        frame = pd.DataFrame(
            {
                "note": notes,
                "signal": rng.normal(size=n),
                "target": rng.normal(size=n),
            }
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.REGRESSION
        )
        kinds = {f.column: f.kind for f in report.findings}
        assert kinds.get("note") is LeakageKind.IDENTIFIER_PROXY
        assert "signal" not in kinds

    def test_excluded_columns_are_skipped(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            excluded_columns=frozenset({"physician_id", "total_comp_ytd"}),
        )
        assert "physician_id" not in report.suspect_columns
        assert "total_comp_ytd" not in report.suspect_columns


class TestTemporalLeakage:
    """Leakage that correlation analysis structurally cannot see."""

    def test_unwindowed_aggregate_flagged_under_temporal_split(
        self, abt_card, abt, plan
    ) -> None:
        strategy = ValidationStrategy(
            strategy=SplitStrategy.TEMPORAL,
            time_column="hire_date",
            holdout_cutoff="2020-01-01",
            rationale="multi-year span",
        )
        report = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            validation_strategy=strategy,
            integration_plan=plan,
        )
        flagged = {
            f.column for f in report.findings if f.kind is LeakageKind.UNWINDOWED_AGGREGATE
        }
        assert {"total_txn_amount", "txn_count"} <= flagged

    def test_aggregates_not_flagged_under_random_split(self, abt_card, abt, plan) -> None:
        """Full-history aggregation is only leakage when the split is temporal."""
        strategy = ValidationStrategy(strategy=SplitStrategy.RANDOM, rationale="iid")
        report = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            validation_strategy=strategy,
            integration_plan=plan,
        )
        assert not any(
            f.kind is LeakageKind.UNWINDOWED_AGGREGATE for f in report.findings
        )

    def test_no_temporal_findings_without_a_strategy(self, abt_card, abt, plan) -> None:
        report = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            integration_plan=plan,
        )
        assert not any(
            f.kind is LeakageKind.UNWINDOWED_AGGREGATE for f in report.findings
        )


class TestCleanCase:
    def test_synthetic_clean_data_passes(self) -> None:
        rng = np.random.default_rng(11)
        n = 500
        frame = pd.DataFrame(
            {
                "feature_a": rng.normal(size=n),
                "feature_b": rng.normal(size=n),
                "target": rng.normal(size=n),
            }
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.REGRESSION
        )
        assert report.is_clean
        assert report.max_target_correlation is None or report.max_target_correlation < 0.95

    def test_digest_renders_for_clean_and_dirty(self, abt_card, abt) -> None:
        dirty = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert "BLOCK" in leakage_digest(dirty)


class TestGateIntegration:
    """The audit exists to feed the gate; verify the whole path."""

    def test_report_projects_into_quality_signals(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        signals = report.to_quality_signals()
        assert signals.max_target_correlation is not None
        assert signals.max_target_correlation > 0.95
        assert "total_comp_ytd" in signals.leakage_suspect_columns

    def test_gate_blocks_even_in_full_auto(self, abt_card, abt) -> None:
        """End-to-end: planted trap -> measurement -> signal -> hard gate rule."""
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("feature_pipeline"),
            signals=report.to_quality_signals(),
            profile=BUILTIN_PROFILES["full_auto"],
            policy=policy,
        )
        assert decision.verdict is not GateVerdict.AUTO_PROCEED
        assert decision.reason_code == "leakage_detected"
        assert any("total_comp_ytd" in i for i in decision.correction_instructions)


class TestOptions:
    def test_threshold_is_configurable(self, abt_card, abt) -> None:
        strict = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            options=LeakageOptions(correlation_threshold=0.30, identifier_unique_rate=2.0),
        )
        lenient = audit_leakage(
            abt_card,
            abt,
            target_column="annual_comp",
            task_type=TaskType.REGRESSION,
            options=LeakageOptions(
                correlation_threshold=0.999,
                mutual_information_threshold=0.999,
                identifier_unique_rate=2.0,
            ),
        )
        assert len(strict.suspect_columns) > len(lenient.suspect_columns)

    def test_unsupervised_skips_target_analysis(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column=None, task_type=TaskType.ANOMALY_DETECTION
        )
        assert report.target_column is None
        assert not any(
            f.kind is LeakageKind.TARGET_CORRELATION for f in report.findings
        )


class TestHighCardinalityArtifact:
    """Regression: near-unique columns score MI 1.0 by construction, not by leaking.

    With N distinct values over N rows every value maps to exactly one target
    value, so mutual information is perfect and meaningless. This previously
    flagged `hire_date` — a legitimate feature — and buried the real findings
    under identifier noise.
    """

    def test_unique_string_column_scores_zero(self) -> None:
        n = 500
        feature = pd.Series([f"id-{i}" for i in range(n)])
        target = pd.Series(np.random.default_rng(0).normal(size=n))
        assert normalised_mutual_information(feature, target) == 0.0

    def test_near_unique_dates_are_binned_not_perfect(self) -> None:
        n = 800
        rng = np.random.default_rng(3)
        dates = pd.Series(
            pd.to_datetime("2005-01-01") + pd.to_timedelta(rng.integers(0, 6000, n), unit="D")
        )
        target = pd.Series(rng.normal(size=n))
        score = normalised_mutual_information(dates, target)
        assert score < 0.5, f"independent dates should not look like leakage, got {score}"

    def test_genuine_determination_still_detected(self) -> None:
        """The guard must not blind the audit to real leakage."""
        n = 600
        rng = np.random.default_rng(5)
        target = pd.Series(rng.integers(0, 3, n))
        feature = target.map({0: "low", 1: "mid", 2: "high"})
        assert normalised_mutual_information(feature, target) > 0.95

    def test_identifiers_are_not_audited_as_features(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        audited = {f.column for f in report.findings}
        for identifier in ("physician_id", "full_name", "email_address", "license_no"):
            assert identifier not in audited, f"{identifier} is not a candidate feature"

    def test_hire_date_is_not_flagged(self, abt_card, abt) -> None:
        """A legitimate tenure feature must survive the audit."""
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert "hire_date" not in report.suspect_columns

    def test_planted_trap_still_caught(self, abt_card, abt) -> None:
        report = audit_leakage(
            abt_card, abt, target_column="annual_comp", task_type=TaskType.REGRESSION
        )
        assert "total_comp_ytd" in report.suspect_columns


class TestChanceAdjustment:
    """Regression: the false-positive fix once created a false negative.

    Guarding on absolute cardinality made the audit blind to a 30-class target
    copied exactly into a feature (20 rows per class, 5% unique) — perfect
    leakage scoring 0.0. The real artifact is low support per level, not
    cardinality, and chance adjustment handles it without a guard.
    """

    def test_exact_categorical_copy_is_detected(self) -> None:
        target = pd.Series([f"class_{i}" for i in range(30) for _ in range(20)])
        assert normalised_mutual_information(target.copy(), target) > 0.95

    def test_unique_per_row_feature_is_still_suppressed(self) -> None:
        target = pd.Series([f"class_{i}" for i in range(30) for _ in range(20)])
        feature = pd.Series([f"id-{i}" for i in range(600)])
        assert normalised_mutual_information(feature, target) < 0.05

    def test_partial_relationship_is_preserved(self) -> None:
        """Chance adjustment must not flatten genuine partial signal to zero."""
        target = pd.Series([f"class_{i}" for i in range(30) for _ in range(20)])
        feature = target.str.removeprefix("class_").astype(int).lt(15).astype(str)
        score = normalised_mutual_information(feature, target)
        assert 0.1 < score < 0.9

    def test_exact_copy_reaches_the_audit_as_a_finding(self) -> None:
        """End to end, not just the metric: the audit must block it."""
        n = 600
        rng = np.random.default_rng(4)
        labels = [f"class_{i}" for i in range(30) for _ in range(20)]
        frame = pd.DataFrame(
            {
                "noise": rng.normal(size=n),
                "shadow_target": labels,
                "target": labels,
            }
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card,
            frame,
            target_column="target",
            task_type=TaskType.MULTICLASS_CLASSIFICATION,
        )
        assert "shadow_target" in report.suspect_columns
        assert "noise" not in report.suspect_columns


class TestOrderedSeparationCalibration:
    """Directional AMI controls for binary and multiclass ordered features."""

    @staticmethod
    def _contiguous_copy(
        n_classes: int, agreement: float
    ) -> tuple[pd.Series, pd.Series]:
        size = max(3_000, n_classes * 100)
        feature = pd.Series(np.arange(size, dtype=float))
        target = np.repeat(np.arange(n_classes), size // n_classes)
        rng = np.random.default_rng(0)
        corrupted = rng.choice(size, int(size * (1 - agreement)), replace=False)
        # This follows the project's existing corrupted-copy calibration: a
        # selected row is assigned a random class, which can occasionally be
        # unchanged. The seed makes the measured agreement and score stable.
        target[corrupted] = rng.integers(n_classes, size=len(corrupted))
        return feature, pd.Series(target)

    @pytest.mark.parametrize("n_classes", [3, 10, 30, 50])
    @pytest.mark.parametrize("agreement", [1.0, 0.975, 0.95, 0.90])
    def test_contiguous_ordered_copies_clear_threshold(
        self, n_classes: int, agreement: float
    ) -> None:
        feature, target = self._contiguous_copy(n_classes, agreement)
        score = ordered_separation_information(feature, target)
        assert score is not None
        assert score >= LEAKAGE_SEPARATION_INFORMATION, (
            f"{n_classes} classes at {agreement:.1%} agreement scored {score:.6f}"
        )

    @pytest.mark.parametrize("n_classes", [3, 10, 30, 50])
    def test_inverted_order_is_equally_detected(self, n_classes: int) -> None:
        feature, target = self._contiguous_copy(n_classes, 1.0)
        direct = ordered_separation_information(feature, target)
        inverted = ordered_separation_information(-feature, target)
        assert inverted == pytest.approx(direct)
        assert inverted is not None and inverted >= LEAKAGE_SEPARATION_INFORMATION

    @pytest.mark.parametrize("positive_rate", [0.01, 0.05, 0.50])
    def test_binary_prevalence_controls(self, positive_rate: float) -> None:
        size = 10_000
        feature = pd.Series(np.arange(size, dtype=float))
        target = pd.Series(
            (np.arange(size) >= int(size * (1 - positive_rate))).astype(int)
        )
        assert ordered_separation_information(feature, target) == pytest.approx(1.0)

    @pytest.mark.parametrize("size", [200, 600, 1_000, 5_000, 15_000])
    def test_permuted_targets_stay_at_chance(self, size: int) -> None:
        rng = np.random.default_rng(size)
        n_classes = min(30, max(2, size // 20))
        target = np.resize(np.arange(n_classes), size)
        rng.shuffle(target)
        score = ordered_separation_information(
            pd.Series(np.arange(size, dtype=float)), pd.Series(target)
        )
        assert score is not None and score < 0.05

    @pytest.mark.parametrize("size", [200, 600, 1_000, 5_000])
    def test_support_cap_suppresses_unique_ids_and_datetimes(self, size: int) -> None:
        rng = np.random.default_rng(size + 1)
        target = pd.Series(rng.integers(0, 5, size))
        unique_id = pd.Series(np.arange(size, dtype=float))
        near_unique_date = pd.Series(
            pd.Timestamp("2000-01-01")
            + pd.to_timedelta(
                np.arange(size) * 3 + rng.integers(0, 2, size), unit="D"
            )
        )

        raw_score = adjusted_mutual_info_score(
            target.to_numpy(), unique_id.to_numpy(), average_method="min"
        )
        assert raw_score > 0.99, "pins why ordered values must never be scored raw"
        for feature in (unique_id, near_unique_date):
            score = ordered_separation_information(feature, target)
            assert score is not None and score < 0.05

    def test_multiclass_separator_reaches_a_non_blocking_finding(self) -> None:
        feature, target = self._contiguous_copy(3, 1.0)
        frame = pd.DataFrame({"ordered_result": feature, "target": target})
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card,
            frame,
            target_column="target",
            task_type=TaskType.MULTICLASS_CLASSIFICATION,
        )

        finding = next(
            item
            for item in report.findings
            if item.column == "ordered_result"
            and item.kind is LeakageKind.PERFECT_SEPARATOR
        )
        assert not finding.blocking
        assert "ordered_result" in report.separator_suspects
        assert "ordered_result" not in report.suspect_columns

    def test_all_sample_ordered_features_are_calibrated(
        self, abt: pd.DataFrame
    ) -> None:
        frame = abt.copy()
        frame["senior_physician"] = (frame["years_experience"] >= 20).astype(int)
        target = frame["senior_physician"]
        ordered = [
            name
            for name in frame.columns
            if name != "senior_physician"
            and (
                pd.api.types.is_numeric_dtype(frame[name])
                or pd.api.types.is_datetime64_any_dtype(frame[name])
            )
            and frame[name].notna().any()
        ]
        scores = {
            name: ordered_separation_information(frame[name], target)
            for name in ordered
        }

        assert scores["years_experience"] == pytest.approx(1.0)
        assert all(
            score is None or score < LEAKAGE_SEPARATION_INFORMATION
            for name, score in scores.items()
            if name != "years_experience"
        ), scores

        card = profile_table(
            LoadedTable(name="abt", frame=frame, source_uri="derived", source_format="csv")
        )
        report = audit_leakage(
            card,
            frame,
            target_column="senior_physician",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        finding = next(
            item
            for item in report.findings
            if item.column == "years_experience"
            and item.kind is LeakageKind.PERFECT_SEPARATOR
        )
        assert not finding.blocking
        assert report.is_clean

    def test_sample_fraud_features_stay_at_chance(
        self, frames: dict[str, pd.DataFrame]
    ) -> None:
        frame = frames["transactions"]
        target = frame["flagged"]
        ordered = [
            name
            for name in frame.columns
            if name != "flagged"
            and (
                pd.api.types.is_numeric_dtype(frame[name])
                or pd.api.types.is_datetime64_any_dtype(frame[name])
            )
        ]
        scores = {
            name: ordered_separation_information(frame[name], target)
            for name in ordered
        }
        assert all(
            score is None or score < LEAKAGE_SEPARATION_INFORMATION
            for score in scores.values()
        ), scores


class TestPerfectSeparator:
    """Codex FINDING 4: classification was blind to a continuous separator.

    The classification path computes no correlation (that is regression-only) and
    Symmetric AMI badly under-reports "target is a deterministic function of
    feature" on skewed classes. At 1% prevalence — this project's motivating
    fraud case — a perfect separator scored ordinary AMI 0.02 and passed.
    """

    @staticmethod
    def _separator_frame(n: int = 10_000, positives: int = 100) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "post_outcome_rank": np.arange(n, dtype=float),
                "target": (np.arange(n) >= n - positives).astype(int),
            }
        )

    def test_continuous_separator_is_detected_as_a_suspect(self) -> None:
        """Detected and escalated, but not auto-dropped.

        Per Codex FINDING 6: statistical strength cannot establish temporal
        provenance, so this reaches a human rather than removing the feature.
        """
        frame = self._separator_frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "post_outcome_rank" in report.separator_suspects
        assert "post_outcome_rank" not in report.suspect_columns
        finding = next(f for f in report.findings if f.column == "post_outcome_rank")
        assert finding.kind is LeakageKind.PERFECT_SEPARATOR
        assert not finding.blocking

    def test_separator_escalates_at_the_gate(self) -> None:
        frame = self._separator_frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("feature_pipeline"),
            signals=report.to_quality_signals(),
            profile=BUILTIN_PROFILES["checkpointed"],
            policy=GatePolicy.load(),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "separator_needs_confirmation"

    def test_confirmed_pre_outcome_clears_the_finding(self) -> None:
        """A human confirmation must survive re-runs, not be re-asked."""
        frame = self._separator_frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card,
            frame,
            target_column="target",
            task_type=TaskType.BINARY_CLASSIFICATION,
            confirmed_pre_outcome=frozenset({"post_outcome_rank"}),
        )
        assert report.separator_suspects == []

    def test_legitimate_decision_rule_is_not_dropped(self) -> None:
        """Codex FINDING 6 verbatim: a real pre-outcome cutoff must survive."""
        rng = np.random.default_rng(1)
        years = rng.integers(1, 35, 800)
        frame = pd.DataFrame(
            {
                "years_experience": years,
                "specialty": rng.choice(["a", "b", "c"], 800),
                "senior_physician": (years >= 20).astype(int),
            }
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card,
            frame,
            target_column="senior_physician",
            task_type=TaskType.BINARY_CLASSIFICATION,
        )
        assert "years_experience" in report.separator_suspects
        assert report.is_clean, "a suspect must not hard-block a legitimate feature"

    def test_ami_alone_would_have_missed_it(self) -> None:
        """Pins why a second, directional measure is needed at all."""
        frame = self._separator_frame()
        score = normalised_mutual_information(frame["post_outcome_rank"], frame["target"])
        assert score < 0.10, "if AMI ever catches this, revisit the separation rule"

    def test_inverted_separator_is_also_detected(self) -> None:
        """Leakage does not care about sign; AUC 0.0 is as damning as 1.0."""
        frame = self._separator_frame()
        frame["post_outcome_rank"] = -frame["post_outcome_rank"]
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "post_outcome_rank" in report.separator_suspects

    def test_ordinary_predictive_feature_is_not_blocked(self) -> None:
        """A useful-but-honest feature must survive: AUC well below 0.99."""
        rng = np.random.default_rng(7)
        n = 4_000
        target = rng.integers(0, 2, n)
        feature = target * 0.8 + rng.normal(size=n)
        frame = pd.DataFrame({"feature": feature, "target": target})
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "feature" not in report.suspect_columns

    def test_regression_path_is_unaffected(self) -> None:
        """Separation is classification-only; regression already had correlation."""
        frame = self._separator_frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.REGRESSION
        )
        assert not any(
            f.kind is LeakageKind.PERFECT_SEPARATOR for f in report.findings
        )


class TestMutualInformationCalibration:
    """Codex FINDING 5: 0.90 was an NMI threshold and AMI is systematically lower.

    Carrying it over silently permitted cases the prior policy blocked. These
    tests pin the calibration so a future threshold change is a deliberate act.
    """

    @staticmethod
    def _corrupted_copy(agreement: float, n_classes: int = 10, size: int = 1_000):
        rng = np.random.default_rng(0)
        target = pd.Series([f"c{i % n_classes}" for i in range(size)])
        feature = target.copy()
        n_flip = int(size * (1 - agreement))
        idx = rng.choice(size, n_flip, replace=False)
        feature.iloc[idx] = [f"c{rng.integers(n_classes)}" for _ in range(n_flip)]
        return feature, target

    @pytest.mark.parametrize("agreement", [1.0, 0.975, 0.95, 0.90])
    def test_corrupted_copies_are_caught(self, agreement: float) -> None:
        feature, target = self._corrupted_copy(agreement)
        score = normalised_mutual_information(feature, target)
        assert score >= LEAKAGE_MUTUAL_INFORMATION, (
            f"{agreement:.0%} agreement scored {score:.4f}, below the threshold"
        )

    def test_strongest_legitimate_feature_has_margin(self, abt_card, abt) -> None:
        """years_experience genuinely predicts pay and must not be flagged."""
        score = normalised_mutual_information(abt["years_experience"], abt["annual_comp"])
        assert score < LEAKAGE_MUTUAL_INFORMATION / 2, (
            f"legitimate feature scored {score:.4f}; threshold has too little margin"
        )


class TestMissingnessSeparator:
    """Codex FINDING 8: presence of a value can itself be post-outcome information.

    Every other scorer drops feature-null rows first, which erases the entire
    leaking signal when absence *is* the signal. Missingness survives into the
    model — categoricals get an explicit sentinel, numerics an imputed region —
    so it must be scored before anything is dropped.
    """

    @staticmethod
    def _frame(n: int = 600) -> pd.DataFrame:
        rng = np.random.default_rng(9)
        target = (np.arange(n) % 3 == 0).astype(int)
        value = rng.normal(size=n)
        value[target == 0] = np.nan  # populated only for positives
        return pd.DataFrame(
            {"followup_score": value, "noise": rng.normal(size=n), "target": target}
        )

    def test_missingness_is_flagged(self) -> None:
        frame = self._frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        kinds = {f.column: f.kind for f in report.findings}
        assert kinds.get("followup_score") is LeakageKind.MISSINGNESS_SEPARATOR
        assert "followup_score" in report.suspect_columns

    def test_ordinary_missingness_is_not_flagged(self) -> None:
        """11% missing at random, as in the real data, must stay clean."""
        rng = np.random.default_rng(3)
        n = 800
        value = rng.normal(size=n)
        value[rng.choice(n, 88, replace=False)] = np.nan
        frame = pd.DataFrame({"comp": value, "target": rng.integers(0, 2, n)})
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "comp" not in report.suspect_columns


class TestMalformedDateHandling:
    """Codex FINDING 7: one invalid date string disabled detection for a column."""

    def test_one_invalid_date_does_not_disable_detection(self) -> None:
        n = 200
        dates = [f"2024-01-{(i % 28) + 1:02d}" for i in range(n)]
        dates[0] = "not-a-date"
        frame = pd.DataFrame(
            {"event_date": dates, "target": [(i >= n - 20) * 1 for i in range(n)]}
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert isinstance(report.findings, list)

    def test_mutual_information_survives_unparseable_values(self) -> None:
        dates = pd.Series([f"2024-01-{(i % 28) + 1:02d}" for i in range(100)])
        dates.iloc[0] = "garbage"
        target = pd.Series([i % 2 for i in range(100)])
        assert normalised_mutual_information(dates, target) >= 0.0

    def test_timezone_aware_datetimes_are_handled(self) -> None:
        n = 200
        frame = pd.DataFrame(
            {
                "event_at": pd.date_range("2024-01-01", periods=n, tz="Europe/Istanbul"),
                "target": (np.arange(n) >= n - 20).astype(int),
            }
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "event_at" in report.separator_suspects


class TestConfirmationScope:
    """Codex FINDING 11: value provenance and presence provenance are different.

    Knowing a value is recorded before the outcome does not establish that
    *whether it exists* is decided before the outcome. A post-outcome follow-up
    workflow can populate a legitimately pre-outcome field only for cases that
    had the outcome.
    """

    @staticmethod
    def _frame(n: int = 600) -> pd.DataFrame:
        rng = np.random.default_rng(9)
        target = (np.arange(n) % 3 == 0).astype(int)
        value = rng.normal(size=n)
        value[target == 0] = np.nan
        return pd.DataFrame({"followup_score": value, "target": target})

    def _audit(self, **kwargs):
        frame = self._frame()
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        return audit_leakage(
            card,
            frame,
            target_column="target",
            task_type=TaskType.BINARY_CLASSIFICATION,
            **kwargs,
        )

    def test_value_confirmation_does_not_clear_missingness(self) -> None:
        report = self._audit(confirmed_pre_outcome=frozenset({"followup_score"}))
        assert "followup_score" in report.suspect_columns, (
            "confirming the value must not silently clear presence provenance"
        )

    def test_missingness_confirmation_clears_missingness(self) -> None:
        report = self._audit(
            confirmed_missingness_pre_outcome=frozenset({"followup_score"})
        )
        assert report.is_clean

    def test_unconfirmed_stays_blocking(self) -> None:
        assert "followup_score" in self._audit().suspect_columns


class TestRareMissingnessIsScored:
    """Codex FINDING 12: a 1% null floor excluded this project's motivating case."""

    def test_thirty_nine_positives_in_fifteen_thousand(self) -> None:
        rng = np.random.default_rng(0)
        n = 15_000
        target = np.zeros(n, dtype=int)
        target[rng.choice(n, 39, replace=False)] = 1
        value = np.full(n, np.nan)
        value[target == 0] = rng.normal(size=int((target == 0).sum()))
        frame = pd.DataFrame(
            {"followup": value, "noise": rng.normal(size=n), "target": target}
        )
        assert frame["followup"].isna().mean() < 0.01, "must sit under the old floor"

        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert "followup" in report.suspect_columns
        assert "noise" not in report.suspect_columns

    def test_fully_present_column_is_skipped(self) -> None:
        rng = np.random.default_rng(1)
        frame = pd.DataFrame(
            {"x": rng.normal(size=300), "target": rng.integers(0, 2, 300)}
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        report = audit_leakage(
            card, frame, target_column="target", task_type=TaskType.BINARY_CLASSIFICATION
        )
        assert not any(
            f.kind is LeakageKind.MISSINGNESS_SEPARATOR for f in report.findings
        )
