"""Integration executor tests.

The load-bearing assertion is grain preservation: if a one-to-many table is
joined without aggregation, the ABT gains rows and every downstream metric is
computed on duplicated entities. That must fail loudly, not silently.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.contracts import IntegrationPlanProposal
from ads.integration import IntegrationError, build_sql, execute_plan


@pytest.fixture
def toy_frames() -> dict[str, pd.DataFrame]:
    return {
        "physicians": pd.DataFrame(
            {"physician_id": [1, 2, 3], "specialty": ["cardio", "onco", "peds"]}
        ),
        "compensation": pd.DataFrame(
            {"physician_id": [1, 2, 3], "annual_comp": [100.0, 200.0, 300.0]}
        ),
        "transactions": pd.DataFrame(
            {
                "txn_id": ["a", "b", "c", "d", "e"],
                "physician_id": [1, 1, 2, 2, 2],
                "amount": [10.0, 20.0, 30.0, 40.0, 50.0],
            }
        ),
    }


def _plan(**overrides) -> IntegrationPlanProposal:
    base = {
        "base_table": "physicians",
        "base_grain": ["physician_id"],
        "grain_description": "One row per physician.",
        "aggregations": [
            {
                "source_table": "transactions",
                "output_name": "txn_by_physician",
                "group_by": ["physician_id"],
                "aggregations": {"total_amount": "SUM(amount)", "txn_count": "COUNT(*)"},
                "rationale": "N:1 fan-out.",
            }
        ],
        "joins": [
            {
                "left_table": "physicians",
                "right_table": "txn_by_physician",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": "left",
                "rationale": "Attach aggregated metrics.",
            }
        ],
        "warnings": [],
    }
    return IntegrationPlanProposal.model_validate({**base, **overrides})


class TestExecution:
    def test_aggregated_join_preserves_grain(self, toy_frames) -> None:
        result = execute_plan(_plan(), toy_frames)
        assert result.grain_preserved
        assert result.result_rows == 3
        assert "total_amount" in result.frame.columns
        assert "txn_count" in result.frame.columns

    def test_aggregate_values_are_correct(self, toy_frames) -> None:
        frame = execute_plan(_plan(), toy_frames).frame.set_index("physician_id")
        assert frame.loc[1, "total_amount"] == 30.0
        assert frame.loc[2, "total_amount"] == 120.0
        assert frame.loc[2, "txn_count"] == 3

    def test_left_join_keeps_unmatched_base_rows(self, toy_frames) -> None:
        """Physician 3 has no transactions and must survive with nulls."""
        frame = execute_plan(_plan(), toy_frames).frame
        assert len(frame) == 3
        assert frame.loc[frame["physician_id"] == 3, "total_amount"].isna().all()

    def test_one_to_one_join_without_aggregation(self, toy_frames) -> None:
        plan = _plan(
            aggregations=[],
            joins=[
                {
                    "left_table": "physicians",
                    "right_table": "compensation",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "1:1.",
                }
            ],
        )
        result = execute_plan(plan, toy_frames)
        assert result.grain_preserved
        assert "annual_comp" in result.frame.columns

    def test_join_key_not_duplicated_in_output(self, toy_frames) -> None:
        frame = execute_plan(_plan(), toy_frames).frame
        assert list(frame.columns).count("physician_id") == 1


class TestGrainProtection:
    def test_raw_fan_out_join_raises(self, toy_frames) -> None:
        """The executor independently verifies what the agent was told to do."""
        plan = _plan(
            aggregations=[],
            joins=[
                {
                    "left_table": "physicians",
                    "right_table": "transactions",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "left",
                    "rationale": "unaggregated, should be caught",
                }
            ],
        )
        with pytest.raises(IntegrationError, match="fan-out"):
            execute_plan(plan, toy_frames)

    def test_inner_join_row_loss_is_warned_not_raised(self, toy_frames) -> None:
        toy_frames["compensation"] = toy_frames["compensation"].iloc[:2]
        plan = _plan(
            aggregations=[],
            joins=[
                {
                    "left_table": "physicians",
                    "right_table": "compensation",
                    "left_columns": ["physician_id"],
                    "right_columns": ["physician_id"],
                    "how": "inner",
                    "rationale": "drops unmatched",
                }
            ],
        )
        result = execute_plan(plan, toy_frames)
        assert result.result_rows == 2
        assert not result.grain_preserved
        assert any("dropped by an inner join" in w for w in result.warnings)


class TestSqlSafety:
    def test_generated_sql_is_readable(self, toy_frames) -> None:
        sql = build_sql(_plan(), {k: set(v.columns) for k, v in toy_frames.items()})
        assert sql.startswith("WITH")
        assert "GROUP BY" in sql
        assert "LEFT JOIN" in sql

    def test_unsafe_identifier_rejected(self, toy_frames) -> None:
        plan = _plan(base_grain=["physician_id"], base_table="physicians")
        bad = plan.model_dump()
        bad["aggregations"][0]["output_name"] = 'evil"; DROP TABLE x; --'
        with pytest.raises(IntegrationError, match="unsafe identifier"):
            execute_plan(IntegrationPlanProposal.model_validate(bad), toy_frames)

    def test_arbitrary_sql_in_aggregate_rejected(self, toy_frames) -> None:
        bad = _plan().model_dump()
        bad["aggregations"][0]["aggregations"] = {
            "x": "(SELECT COUNT(*) FROM physicians)"
        }
        with pytest.raises(IntegrationError, match="Unsupported aggregate"):
            execute_plan(IntegrationPlanProposal.model_validate(bad), toy_frames)

    def test_disallowed_function_rejected(self, toy_frames) -> None:
        bad = _plan().model_dump()
        bad["aggregations"][0]["aggregations"] = {"x": "read_csv(amount)"}
        with pytest.raises(IntegrationError, match="not allowed"):
            execute_plan(IntegrationPlanProposal.model_validate(bad), toy_frames)

    def test_aggregate_on_unknown_column_rejected(self, toy_frames) -> None:
        bad = _plan().model_dump()
        bad["aggregations"][0]["aggregations"] = {"x": "SUM(nonexistent)"}
        with pytest.raises(IntegrationError, match="unknown column"):
            execute_plan(IntegrationPlanProposal.model_validate(bad), toy_frames)

    def test_count_star_allowed(self, toy_frames) -> None:
        result = execute_plan(_plan(), toy_frames)
        assert result.frame["txn_count"].sum() == 5


class TestEndToEnd:
    """Run the plan the agent actually produced, against the real sample data."""

    @staticmethod
    def _sample_plan() -> IntegrationPlanProposal:
        return IntegrationPlanProposal.model_validate(
            {
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
                            "last_txn_date": "MAX(txn_date)",
                        },
                        "rationale": "N:1 fan-out over 15,000 rows.",
                    },
                    {
                        "source_table": "ledger_2019_2024",
                        "output_name": "ledger_by_provider",
                        "group_by": ["provider_ref"],
                        "aggregations": {
                            "total_ledger_amount": "SUM(amount)",
                            "ledger_entry_count": "COUNT(*)",
                        },
                        "rationale": "N:1 fan-out over 20,000 rows.",
                    },
                ],
                "joins": [
                    {
                        "left_table": "physicians__physician_master",
                        "right_table": "physicians__compensation",
                        "left_columns": ["physician_id"],
                        "right_columns": ["physician_id"],
                        "how": "left",
                        "rationale": "1:1, 100% matched.",
                    },
                    {
                        "left_table": "physicians__physician_master",
                        "right_table": "txn_by_physician",
                        "left_columns": ["physician_id"],
                        "right_columns": ["physician_id"],
                        "how": "left",
                        "rationale": "Aggregated transaction metrics.",
                    },
                    {
                        "left_table": "physicians__physician_master",
                        "right_table": "ledger_by_provider",
                        "left_columns": ["physician_id"],
                        "right_columns": ["provider_ref"],
                        "how": "left",
                        "rationale": "Aggregated ledger metrics via provider_ref.",
                    },
                ],
                "warnings": [],
            }
        )

    def test_abt_preserves_physician_grain(self, frames) -> None:
        result = execute_plan(self._sample_plan(), frames)
        assert result.base_rows == 800
        assert result.grain_preserved, "aggregation must prevent row multiplication"
        assert result.frame["physician_id"].is_unique

    def test_abt_carries_target_and_aggregates(self, frames) -> None:
        frame = execute_plan(self._sample_plan(), frames).frame
        for column in (
            "annual_comp",
            "total_comp_ytd",
            "total_txn_amount",
            "txn_count",
            "total_ledger_amount",
        ):
            assert column in frame.columns

    def test_orphan_providers_excluded_from_aggregate(self, frames) -> None:
        """The 5.8% orphan ledger rows must not attach to any physician."""
        result = execute_plan(self._sample_plan(), frames)
        matched = result.frame["ledger_entry_count"].sum()
        assert matched < len(frames["ledger_2019_2024"])
        assert matched == pytest.approx(len(frames["ledger_2019_2024"]) * 0.942, rel=0.02)

    def test_leakage_trap_survives_into_abt(self, frames) -> None:
        """total_comp_ytd must reach the ABT so the leakage audit can catch it."""
        frame = execute_plan(self._sample_plan(), frames).frame
        correlation = frame[["annual_comp", "total_comp_ytd"]].corr().iloc[0, 1]
        assert correlation > 0.95
