"""Split diagnostics and the degenerate-split gate rule.

The motivating measurement, from real sample data: `grouped_temporal` on the
15,000-row transaction ABT is a *correct* split that retains 20.6% of rows and
yields 5-row validation folds. Correct and unusable are different properties,
and only the second was going unmeasured.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.contracts import IntegrationPlanProposal, SplitStrategy, ValidationStrategy
from ads.contracts.gates import BUILTIN_PROFILES, GateVerdict, QualitySignals
from ads.gates import GatePolicy, StageHistory, evaluate_gate
from ads.integration import execute_plan
from ads.splitting import describe_split

CHECKPOINTED = BUILTIN_PROFILES["checkpointed"]
FULL_AUTO = BUILTIN_PROFILES["full_auto"]


def _strategy(strategy: SplitStrategy, **kwargs) -> ValidationStrategy:
    return ValidationStrategy(strategy=strategy, rationale="test", **kwargs)


@pytest.fixture(scope="module")
def transaction_abt(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    plan = IntegrationPlanProposal(
        base_table="transactions",
        base_grain=["txn_id"],
        grain_description="One row per transaction.",
    )
    return execute_plan(plan, frames).frame


class TestDiagnosticsOnRealData:
    def test_grouped_temporal_is_measurably_degenerate(self, transaction_abt) -> None:
        """Pins the exact cost that motivated this module."""
        diagnostics = describe_split(
            transaction_abt,
            _strategy(
                SplitStrategy.GROUPED_TEMPORAL,
                group_column="physician_id",
                time_column="txn_date",
                holdout_cutoff="2024-01-01",
            ),
        )
        assert diagnostics.retained_rate < 0.30
        assert diagnostics.n_purged_rows > 10_000
        assert diagnostics.min_validation_fold_size < 30

    def test_temporal_alone_retains_everything(self, transaction_abt) -> None:
        diagnostics = describe_split(
            transaction_abt,
            _strategy(
                SplitStrategy.TEMPORAL,
                time_column="txn_date",
                holdout_cutoff="2024-01-01",
            ),
        )
        assert diagnostics.retained_rate == 1.0
        assert diagnostics.min_validation_fold_size > 1_000

    def test_grouped_alone_retains_everything(self, transaction_abt) -> None:
        diagnostics = describe_split(
            transaction_abt,
            _strategy(SplitStrategy.GROUPED, group_column="physician_id"),
        )
        assert diagnostics.retained_rate == 1.0

    def test_digest_reports_the_purge(self, transaction_abt) -> None:
        diagnostics = describe_split(
            transaction_abt,
            _strategy(
                SplitStrategy.GROUPED_TEMPORAL,
                group_column="physician_id",
                time_column="txn_date",
                holdout_cutoff="2024-01-01",
            ),
        )
        digest = diagnostics.digest()
        assert "purged" in digest
        assert "validation fold sizes" in digest


class TestDegenerateSplitGate:
    def test_degenerate_split_escalates(self, transaction_abt) -> None:
        """End to end: a correct-but-unusable split must reach a human."""
        diagnostics = describe_split(
            transaction_abt,
            _strategy(
                SplitStrategy.GROUPED_TEMPORAL,
                group_column="physician_id",
                time_column="txn_date",
                holdout_cutoff="2024-01-01",
            ),
        )
        policy = GatePolicy.load()
        decision = evaluate_gate(
            stage=policy.stage("validation_strategy"),
            signals=diagnostics.to_quality_signals(),
            profile=CHECKPOINTED,
            policy=policy,
            history=StageHistory(attempts=1),
        )
        assert decision.verdict is GateVerdict.ESCALATE
        assert decision.reason_code == "degenerate_split"

    def test_healthy_split_does_not_trigger_the_rule(self, transaction_abt) -> None:
        diagnostics = describe_split(
            transaction_abt,
            _strategy(
                SplitStrategy.TEMPORAL,
                time_column="txn_date",
                holdout_cutoff="2024-01-01",
            ),
        )
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=diagnostics.to_quality_signals(),
            profile=CHECKPOINTED,
            policy=GatePolicy.load(),
        )
        assert "degenerate_split" not in decision.triggered_rules

    def test_tiny_folds_alone_are_enough_to_escalate(self) -> None:
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=QualitySignals(split_retained_rate=1.0, min_validation_fold_size=4),
            profile=CHECKPOINTED,
        )
        assert decision.reason_code == "degenerate_split"

    def test_coverage_is_not_reported_as_noise_by_default(self) -> None:
        """Coverage and precision are different problems.

        Per Codex's pushback: a split retaining 49% of ten million rows is not
        noisy, it describes a narrower population. The retained-rate threshold is
        therefore disabled by default and the fold-size floor carries the case.
        """
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=QualitySignals(split_retained_rate=0.2, min_validation_fold_size=5),
            profile=CHECKPOINTED,
        )
        message = decision.human_prompt.context_summary
        assert "5 rows" in message
        assert "20.0%" not in message, "coverage must not be cited as a noise cause"

    def test_low_coverage_alone_does_not_escalate_by_default(self) -> None:
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=QualitySignals(split_retained_rate=0.2, min_validation_fold_size=5_000),
            profile=CHECKPOINTED,
        )
        assert "degenerate_split" not in decision.triggered_rules

    def test_configured_coverage_floor_is_honoured(self) -> None:
        """A deployment with a defined minimum coverage can still enforce one."""
        policy = GatePolicy.from_dict({"thresholds": {"min_split_retained_rate": 0.5}})
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=QualitySignals(split_retained_rate=0.2, min_validation_fold_size=5_000),
            profile=CHECKPOINTED,
            policy=policy,
        )
        assert decision.reason_code == "degenerate_split"
        assert "narrower population" in decision.human_prompt.context_summary

    def test_rule_is_opt_in_like_other_signal_rules(self) -> None:
        signals = QualitySignals(split_retained_rate=0.1, min_validation_fold_size=2)
        assert (
            evaluate_gate(
                stage=GatePolicy.load().stage("eda"), signals=signals, profile=FULL_AUTO
            ).verdict
            is GateVerdict.AUTO_PROCEED
        )

    def test_absent_signals_do_not_fire(self) -> None:
        decision = evaluate_gate(
            stage=GatePolicy.load().stage("eda"),
            signals=QualitySignals(),
            profile=CHECKPOINTED,
        )
        assert decision.verdict is GateVerdict.AUTO_PROCEED
