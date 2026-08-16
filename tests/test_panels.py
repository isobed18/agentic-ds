"""Analysis panels: severity, ordering, and the aggregate-only boundary.

These panels are what a person looks at to decide whether the data supports the
model, so the load-bearing assertions are that severity matches what was
measured and that nothing beyond an aggregate reaches the browser.
"""

from __future__ import annotations

import json

from ads.api.panels import (
    HIGH_MISSING_RATE,
    HIGH_OUTLIER_RATE,
    STRONG_CORRELATION,
    eda_panels,
    schema_graph,
    source_panels,
    validation_panels,
)


def _eda_payload(**overrides) -> dict:
    payload = {
        "target_column": "annual_comp",
        "row_count": 800,
        "feature_columns": ["tenure", "region"],
        "covered_columns": ["tenure", "region"],
        "target_distribution": {
            "target_column": "annual_comp",
            "kind": "numeric_summary",
            "total_count": 800,
            "non_null_count": 712,
            "null_rate": 0.11,
            "numeric": {"min": 1.0, "max": 9.0, "mean": 5.0, "std": 2.0,
                        "p25": 3.0, "p50": 5.0, "p75": 7.0},
            "histogram": [
                {"lower": 1.0, "upper": 5.0, "count": 400},
                {"lower": 5.0, "upper": 9.0, "count": 312},
            ],
        },
        "missingness": [
            {"column": "annual_comp", "null_count": 88, "null_rate": 0.11},
            {"column": "tenure", "null_count": 0, "null_rate": 0.0},
        ],
        "correlation_matrix": {
            "columns": ["a", "b"],
            "values": [[1.0, 0.995], [0.995, 1.0]],
        },
        "target_relationships": [
            {"column": "total_comp_ytd", "pearson_correlation": 0.995,
             "adjusted_mutual_information": 0.9},
            {"column": "tenure", "pearson_correlation": 0.21,
             "adjusted_mutual_information": 0.1},
        ],
        "outliers": [
            {"column": "tenure", "lower_fence": 0.0, "upper_fence": 40.0,
             "evaluated_count": 800, "outlier_count": 80, "outlier_rate": 0.10,
             "p25": 5.0, "p50": 12.0, "p75": 20.0},
        ],
    }
    payload.update(overrides)
    return payload


def _by_id(panels: list[dict]) -> dict[str, dict]:
    return {panel["id"]: panel for panel in panels}


class TestSeverityReflectsMeasurement:
    def test_missingness_above_the_threshold_is_a_warning(self) -> None:
        panels = _by_id(eda_panels(_eda_payload()))
        assert panels["missing_values"]["severity"] == "warning"
        assert "annual_comp" in panels["missing_values"]["insights"][0]

    def test_missingness_below_the_threshold_is_not(self) -> None:
        payload = _eda_payload(
            missingness=[{"column": "tenure", "null_count": 8, "null_rate": 0.01}]
        )
        assert _by_id(eda_panels(payload))["missing_values"]["severity"] == "review"

    def test_no_missingness_at_all_is_ok(self) -> None:
        payload = _eda_payload(
            missingness=[{"column": "tenure", "null_count": 0, "null_rate": 0.0}]
        )
        assert _by_id(eda_panels(payload))["missing_values"]["severity"] == "ok"

    def test_a_near_perfect_predictor_is_flagged_as_an_issue(self) -> None:
        """0.995 correlation with the target is leakage until proven otherwise."""
        panels = _by_id(eda_panels(_eda_payload()))
        relationships = panels["feature_relationships"]
        assert relationships["severity"] == "issue"
        assert "leakage" in " ".join(relationships["insights"]).lower()

    def test_weak_relationships_are_not_flagged(self) -> None:
        payload = _eda_payload(
            target_relationships=[
                {"column": "tenure", "pearson_correlation": 0.2,
                 "adjusted_mutual_information": 0.1}
            ]
        )
        assert _by_id(eda_panels(payload))["feature_relationships"]["severity"] == "review"

    def test_outlier_share_above_the_threshold_is_an_issue(self) -> None:
        assert _by_id(eda_panels(_eda_payload()))["outliers"]["severity"] == "issue"

    def test_the_most_severe_analysis_is_first(self) -> None:
        """The strip is read left to right; the worst finding must not be offscreen."""
        panels = eda_panels(_eda_payload())
        ranks = ["ok", "info", "review", "warning", "issue"]
        positions = [ranks.index(p["severity"]) for p in panels]
        assert positions == sorted(positions, reverse=True)


class TestChartsCarryOnlyAggregates:
    def test_no_panel_contains_a_raw_value(self) -> None:
        """The same boundary the agents sit behind, applied to the browser.

        Every number in a panel must be a count, rate, quantile or correlation.
        This asserts the shape of what is emitted rather than scanning for a
        magic string, since a chart of raw observations would be structurally
        different — a scatter of points rather than a tally.
        """
        for panel in eda_panels(_eda_payload()):
            chart = panel["chart"]
            assert chart["kind"] in {"bar", "histogram", "hbar", "heatmap", "box", "donut", "empty"}
            assert "rows" not in chart
            assert "points" not in chart
            assert "observations" not in chart

    def test_a_numeric_target_uses_measured_bins(self) -> None:
        chart = _by_id(eda_panels(_eda_payload()))["target_distribution"]["chart"]
        assert chart["kind"] == "histogram"
        assert [b["count"] for b in chart["bins"]] == [400, 312]

    def test_a_run_recorded_before_binning_still_draws_something(self) -> None:
        """Artifacts are immutable, so older runs carry quantiles and no bins.

        A box built from the quantiles shows real measured spread. An empty
        frame would imply the distribution was never measured, which is false.
        """
        payload = _eda_payload()
        payload["target_distribution"]["histogram"] = []
        chart = _by_id(eda_panels(payload))["target_distribution"]["chart"]
        assert chart["kind"] == "box"
        assert chart["series"][0]["p50"] == 5.0

    def test_outliers_without_quartiles_fall_back_to_shares(self) -> None:
        payload = _eda_payload()
        for item in payload["outliers"]:
            item.pop("p25")
        chart = _by_id(eda_panels(payload))["outliers"]["chart"]
        assert chart["kind"] == "hbar"
        assert chart["series"][0]["value"] == 0.10


class TestSchemaGraph:
    def _plan(self, **overrides) -> dict:
        plan = {
            "base_table": "physicians",
            "joins": [
                {"left_table": "physicians", "right_table": "txn_by_physician",
                 "left_columns": ["physician_id"], "right_columns": ["physician_id"],
                 "how": "left"},
            ],
            "aggregations": [
                {"source_table": "transactions", "output_name": "txn_by_physician",
                 "group_by": ["physician_id"], "aggregations": {"total": "SUM(amount)"}},
            ],
            "evidence": [
                {"from_table": "transactions", "from_columns": ["physician_id"],
                 "to_table": "physicians", "to_columns": ["physician_id"],
                 "overlap_rate": 0.98},
            ],
        }
        plan.update(overrides)
        return plan

    def test_a_derived_table_inherits_the_measurement_of_its_source(self) -> None:
        """The aggregate itself was never measured for overlap; its source was.

        Without this the most common join in the pipeline — aggregate a
        one-to-many table, then join the aggregate — would always show as
        unmeasured, which reads as a warning about a join that is fine.
        """
        graph = schema_graph(self._plan())
        edge = graph["edges"][0]
        assert edge["overlap_rate"] == 0.98
        assert edge["confidence"] == "high"

    def test_an_aggregate_records_where_it_came_from(self) -> None:
        node = next(n for n in schema_graph(self._plan())["nodes"] if n["role"] == "aggregate")
        assert node["id"] == "txn_by_physician"
        assert node["source_table"] == "transactions"

    def test_an_unmeasured_join_is_unmeasured_not_low(self) -> None:
        """Colouring an unknown red would assert a finding nobody made."""
        graph = schema_graph(self._plan(evidence=[]))
        assert graph["edges"][0]["overlap_rate"] is None
        assert graph["edges"][0]["confidence"] == "unmeasured"

    def test_a_poor_overlap_is_reported_as_low(self) -> None:
        plan = self._plan()
        plan["evidence"][0]["overlap_rate"] = 0.55
        assert schema_graph(plan)["edges"][0]["confidence"] == "low"

    def test_the_graph_is_json_serialisable(self) -> None:
        json.dumps(schema_graph(self._plan()))


def test_thresholds_are_named_not_inline() -> None:
    """Each is a product decision about what deserves a person's attention."""
    assert 0 < HIGH_MISSING_RATE < 1
    assert 0 < HIGH_OUTLIER_RATE < 1
    assert 0 < STRONG_CORRELATION <= 1


class TestSplitProtectionIsVisible:
    """Strategies are not ranked; each prevents a different leak.

    `grouped` is not a safer `temporal` — it provides no temporal protection at
    all. That partial order is enforced deterministically elsewhere, but until
    these panels existed it was invisible: a reader saw the word "temporal" and
    had no way to know it does nothing about entity repetition.
    """

    def _strategy(self, strategy: str, **signals) -> dict:
        base = {
            "n_rows": 800, "n_usable_rows": 712, "repeated_entity_keys": [],
            "temporal_spans": [], "minority_class_rate": None,
        }
        base.update(signals)
        return {
            "strategy": strategy, "n_folds": 5, "test_size": 0.2,
            "group_column": None, "time_column": None, "holdout_cutoff": None,
            "detected_signals": base,
        }

    def _protection(self, payload: dict) -> dict:
        return next(p for p in validation_panels(payload) if p["id"] == "split_protection")

    def test_covering_every_required_protection_is_not_an_issue(self) -> None:
        payload = self._strategy(
            "temporal",
            temporal_spans=[{"column": "hire_date", "min_date": "2005-01-05",
                             "max_date": "2022-10-11", "span_days": 6488}],
        )
        assert self._protection(payload)["severity"] == "info"

    def test_a_missing_required_protection_is_an_issue(self) -> None:
        """The counter-case. Repeated entities plus a time span needs both."""
        payload = self._strategy(
            "temporal",
            repeated_entity_keys=[{"column": "physician_id"}],
            temporal_spans=[{"column": "hire_date", "min_date": "2005-01-05",
                             "max_date": "2022-10-11", "span_days": 6488}],
        )
        panel = self._protection(payload)
        assert panel["severity"] == "issue"
        assert "entity isolation" in panel["insights"][0].lower()

    def test_grouped_does_not_count_as_temporal_protection(self) -> None:
        """The specific confusion the protection model exists to prevent."""
        payload = self._strategy(
            "grouped",
            temporal_spans=[{"column": "hire_date", "min_date": "2005-01-05",
                             "max_date": "2022-10-11", "span_days": 6488}],
        )
        panel = self._protection(payload)
        assert panel["severity"] == "issue"
        rows = {row[0]: row for row in panel["table"]["rows"]}
        assert rows["Temporal ordering"] == ["Temporal ordering", "yes", "no"]
        assert rows["Entity isolation"] == ["Entity isolation", "no", "yes"]

    def test_random_provides_nothing(self) -> None:
        panel = self._protection(self._strategy("random"))
        assert all(row[2] == "no" for row in panel["table"]["rows"])

    def test_rows_dropped_for_a_missing_target_are_surfaced(self) -> None:
        setup = next(
            p for p in validation_panels(self._strategy("random")) if p["id"] == "split_setup"
        )
        assert setup["severity"] == "warning"
        assert "712" in setup["insights"][0] and "800" in setup["insights"][0]


class TestSourcePanelsDescribeTheDataNotTheArtifact:
    """Every source card once carried the identical sentence.

    "Schema, size and quality statistics for this source table" describes the
    artifact, is true of every table in every dataset, and tells a person
    nothing about the data they are trying to understand. These assert that the
    copy is derived from what was measured about that specific table.
    """

    def _card(self, name: str, **overrides) -> dict:
        payload = {
            "table_name": name,
            "n_rows": 800,
            "candidate_primary_keys": [["physician_id"]],
            "issues": [],
            "columns": [
                {"name": "physician_id", "semantic_type": "identifier",
                 "sensitivity": "internal", "null_rate": 0.0, "n_unique": 800},
                {"name": "specialty", "semantic_type": "categorical",
                 "sensitivity": "internal", "null_rate": 0.0, "n_unique": 8},
            ],
        }
        payload.update(overrides)
        return {"name": name, "payload": payload}

    def test_the_measured_grain_is_stated_in_words(self) -> None:
        panel = source_panels([self._card("physicians")])[0]
        assert "one row per physician_id" in panel["description"]
        assert "800 rows" in panel["description"]

    def test_the_key_field_is_read_from_the_right_name(self) -> None:
        """It is `candidate_primary_keys`; reading `candidate_keys` reported
        "0 candidate key(s)" for every table whose keys had in fact been found."""
        panel = source_panels([self._card("physicians")])[0]
        assert "no column or combination was measured unique" not in panel["description"]

    def test_an_unkeyed_table_says_so_rather_than_claiming_a_grain(self) -> None:
        panel = source_panels([self._card("mystery", candidate_primary_keys=[])])[0]
        assert "one row's identity is unclear" in panel["description"]

    def test_two_tables_do_not_receive_the_same_description(self) -> None:
        panels = source_panels([
            self._card("physicians"),
            self._card("ledger", n_rows=20_000,
                       candidate_primary_keys=[["entry_id"]],
                       columns=[{"name": "entry_id", "semantic_type": "identifier",
                                 "sensitivity": "internal", "null_rate": 0.0,
                                 "n_unique": 20_000}]),
        ])
        assert panels[0]["description"] != panels[1]["description"]

    def test_informational_notes_do_not_raise_severity(self) -> None:
        """The counter-test, and the reason this was worth fixing.

        Intake records renames and detected delimiters alongside real problems.
        Counting every entry made a cleanly-loaded table read as five issues,
        and two of the four sample tables were shown as blocking when nothing
        was wrong with either — a false positive of exactly the kind this
        project has been burned by before.
        """
        notes = [{"severity": "info", "code": "column_renamed",
                  "detail": "Renamed 'Physician ID' to physician_id"}] * 5
        panel = source_panels([self._card("physicians", issues=notes)])[0]
        assert panel["severity"] == "info"
        assert any("informational note" in text for text in panel["insights"])

    def test_a_genuine_warning_still_raises_severity(self) -> None:
        """The other direction. Suppressing info must not suppress real findings."""
        mixed = [
            {"severity": "info", "code": "column_renamed", "detail": "Renamed a column"},
            {"severity": "warn", "code": "header_row_inferred",
             "detail": "Header detected at row 2"},
        ]
        panel = source_panels([self._card("physicians", issues=mixed)])[0]
        assert panel["severity"] == "issue"
        assert any("Header detected at row 2" in text for text in panel["insights"])
