"""Intake, profiling and key-detection tests against the messy sample dataset.

Assertions are exact where the generator is deterministic, because the point of
these tests is to catch silent regressions in numbers a human will later act on
at a gate — an orphan rate that drifts from 5.8% to 42% changes a business
decision.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from tests.conftest import COMPENSATION, LEDGER, MASTER, TRANSACTIONS

from ads.contracts import DataCard, SemanticType, Sensitivity
from ads.intake import (
    LoadedTable,
    detect_primary_keys,
    detect_relationships,
    load_csv,
    load_directory,
    load_excel,
    normalize_columns,
    profile_table,
    profile_tables,
    relationships_digest,
)
from ads.intake.keys import KeyDetectionOptions, measure_relationship, name_affinity
from ads.intake.profiler import datacard_digest, infer_semantic_type

# The identifier guard is the real consumer of these names. Importing it, rather
# than restating its regex here, means the two cannot drift apart silently.
from ads.integration.executor import IntegrationError, _quote

# The materialize guard is the one #89 actually failed at; import its regex so
# the intake test measures the same rule the run enforces, not a copy of it.
from ads.sandbox.materialize import _SAFE_TABLE


class TestLoaders:
    def test_all_tables_discovered(self, loaded_tables: list[LoadedTable]) -> None:
        names = {t.name for t in loaded_tables}
        assert names == {LEDGER, MASTER, COMPENSATION, TRANSACTIONS}

    def test_semicolon_delimiter_detected(self, cards_by_name: dict[str, DataCard]) -> None:
        ledger = cards_by_name[LEDGER]
        assert ledger.n_columns == 6, "wrong delimiter would collapse this to 1 column"
        assert ledger.n_rows == 20_000

    def test_excel_title_rows_detected_and_reported(
        self, cards_by_name: dict[str, DataCard]
    ) -> None:
        master = cards_by_name[MASTER]
        codes = {i.code for i in master.issues}
        assert "header_row_inferred" in codes
        assert "physician_id" in master.column_names

    def test_all_null_column_dropped_with_warning(self, cards_by_name: dict[str, DataCard]) -> None:
        master = cards_by_name[MASTER]
        assert "unused_column" not in master.column_names
        assert "empty_columns_dropped" in {i.code for i in master.issues}

    def test_column_names_normalized(self, cards_by_name: dict[str, DataCard]) -> None:
        assert "physician_id" in cards_by_name[MASTER].column_names
        assert "annual_comp" in cards_by_name[COMPENSATION].column_names

    def test_duplicate_columns_are_deduped_and_flagged(self, tmp_path: Path) -> None:
        path = tmp_path / "dupes.csv"
        path.write_text("a,a,b\n1,2,3\n4,5,6\n", encoding="utf-8")
        table = load_csv(path)
        assert list(table.frame.columns) == ["a", "a_1", "b"]
        assert "duplicate_column_name" in {i.code for i in table.issues}

    def test_blank_header_gets_positional_name(self) -> None:
        frame = pd.DataFrame([[1, 2]], columns=["Unnamed: 0", "  Real Name  "])
        normalized, issues = normalize_columns(frame)
        assert list(normalized.columns) == ["column_0", "real_name"]
        assert "blank_column_name" in {i.code for i in issues}

    def test_file_named_after_a_year_produces_a_usable_table_name(
        self, tmp_path: Path
    ) -> None:
        """A year in the file name used to stop the run before any plan existed.

        `2026-yili-calisma-takvimi-excel.xlsx` slugged to `2026_yili_...`, which
        `_quote` refuses because a SQL identifier cannot start with a digit.
        Intake and profiling both succeeded, then preparation died with
        IntegrationError and no plan was produced.
        """
        path = tmp_path / "2026-yili-calisma-takvimi-excel.csv"
        path.write_text("kalem,tutar\nkira,100\nsu,200\n", encoding="utf-8")

        table = load_csv(path)

        assert table.name.startswith("_2026_yili")
        _quote(table.name)  # raises IntegrationError if it starts with a digit

    def test_numbered_shard_file_gets_a_safe_table_name_reported_at_intake(
        self, tmp_path: Path
    ) -> None:
        """A sharded export (00000.csv) used to fail deep in a run (#89).

        `_slugify` prefixes the leading digit, so `00000` becomes `_00000` and
        the run no longer dies in `materialize_frame_copies` with "Table name
        '00000' cannot be materialized safely for execution". This guards that
        safety directly against the materialize regex, and asserts the rename is
        now surfaced at intake -- like a renamed column -- instead of silently,
        which was the second half of the report.
        """
        path = tmp_path / "00000.csv"
        path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

        table = load_csv(path)

        assert table.name == "_00000"
        _quote(table.name)  # raises IntegrationError if it starts with a digit
        # The exact guard the run failed at, imported so the two cannot drift.
        assert _SAFE_TABLE.fullmatch(table.name)
        assert "table_name_normalized" in {i.code for i in table.issues}

    def test_ordinary_file_name_is_not_flagged_as_renamed(self, tmp_path: Path) -> None:
        """A stem that already reads as an identifier needs no notice -- the new
        issue must not fire for every upload."""
        path = tmp_path / "sales.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")

        table = load_csv(path)

        assert table.name == "sales"
        assert "table_name_normalized" not in {i.code for i in table.issues}

    def test_column_named_after_a_year_survives_sql_quoting(self) -> None:
        """Same defect on the column side; a year in a header is just as common."""
        frame = pd.DataFrame([[1, 2]], columns=["2024 Tutar", "kalem"])

        normalized, issues = normalize_columns(frame)

        assert list(normalized.columns) == ["_2024_tutar", "kalem"]
        for name in normalized.columns:
            _quote(name)
        # The rename is surfaced, not applied silently.
        assert "column_renamed" in {i.code for i in issues}

    def test_quote_still_refuses_a_digit_leading_identifier(self) -> None:
        """Guards the fix itself.

        The two tests above would also pass if someone loosened `_IDENTIFIER_RE`
        instead of fixing the slugger, which would reopen the injection surface
        the guard exists to close.
        """
        with pytest.raises(IntegrationError):
            _quote("2024_tutar")

    def test_mixed_type_column_survives_duckdb(self, tmp_path: Path) -> None:
        """A date column with one integer used to make the source unreadable.

        DuckDB infers TIMESTAMP from the leading values of a pandas object
        column and then refuses the integer, so a plain SELECT * died with
        "Unimplemented type for cast (BIGINT -> TIMESTAMP)". A work calendar
        produces exactly this: a date per row, and a day count for the row
        covering a multi-day holiday.
        """
        path = tmp_path / "takvim.xlsx"
        pd.DataFrame(
            {
                "tarih": [datetime(2026, 1, 1), datetime(2026, 4, 23), 9],
                "aciklama": ["Yilbasi", "Ulusal Egemenlik", "Ramazan Bayrami"],
            }
        ).to_excel(path, index=False)

        table = load_excel(path)[0]

        connection = duckdb.connect()
        try:
            connection.register(table.name, table.frame)
            rows = connection.execute(f'SELECT * FROM "{table.name}"').fetch_df()
        finally:
            connection.close()
        assert len(rows) == 3
        # Nothing is dropped: the integer survives as its own text.
        assert "9" in set(table.frame["tarih"].astype(str))

    def test_mixed_type_column_is_reported(self, tmp_path: Path) -> None:
        """Surfaced, not quietly repaired.

        The original defect was silent twice over: no issue was recorded and
        the profiler still described the column as a datetime.
        """
        path = tmp_path / "takvim.xlsx"
        pd.DataFrame(
            {
                "tarih": [datetime(2026, 1, 1), datetime(2026, 4, 23), 9],
                "aciklama": ["a", "b", "c"],
            }
        ).to_excel(path, index=False)

        table = load_excel(path)[0]

        mixed = [i for i in table.issues if i.code == "mixed_column_types"]
        assert len(mixed) == 1
        assert mixed[0].severity == "warn"
        assert "tarih" in mixed[0].detail

    def test_int_and_float_together_are_not_flagged(self, tmp_path: Path) -> None:
        """Guards the fix against being too eager.

        Casting every object column to text would pass the two tests above
        while destroying ordinary numeric columns. int and float in one column
        is normal and DuckDB widens it without complaint.
        """
        path = tmp_path / "tutarlar.csv"
        path.write_text("tutar,kalem\n1,kira\n2.5,su\n3,internet\n", encoding="utf-8")

        table = load_csv(path)

        assert "mixed_column_types" not in {i.code for i in table.issues}
        assert pd.api.types.is_numeric_dtype(table.frame["tutar"])

    def test_turkish_column_names_are_preserved(self) -> None:
        """'Şube' used to arrive as 'ube'.

        The slugger replaced every non-ASCII run with a separator, so Turkish
        letters were deleted rather than transliterated. On data a customer
        recognises, a column name that no longer resembles what they typed is
        worse than a loud failure.
        """
        frame = pd.DataFrame([[1, 2, 3]], columns=["Şube", "Ürün", "Öğrenci Sayısı"])

        normalized, _ = normalize_columns(frame)

        assert list(normalized.columns) == ["şube", "ürün", "öğrenci_sayısı"]
        for name in normalized.columns:
            _quote(name)

    def test_turkish_columns_that_used_to_collide_stay_distinct(self) -> None:
        """'Ölçü' and 'ŞİŞLİ' both became 'l', then were deduped to 'l' and 'l_1'.

        Two unrelated columns merging into one name is the damaging half of the
        old behaviour: every downstream heuristic that reads names as tokens,
        and the agent reading the DataCard, saw 'l' and 'l_1'.
        """
        frame = pd.DataFrame([[1, 2, 3]], columns=["Ölçü", "ŞİŞLİ", "Çıkış"])

        normalized, issues = normalize_columns(frame)

        assert list(normalized.columns) == ["ölçü", "şişli", "çıkış"]
        assert "duplicate_column_name" not in {i.code for i in issues}

    def test_dotted_capital_i_does_not_produce_a_combining_mark(self) -> None:
        """Guards the one letter Python lowercases wrongly for Turkish.

        `"İ".lower()` returns `i` followed by a combining dot above -- one
        character becomes two -- and the result then fails the identifier rule.
        Without the pre-map this test fails while the two above still pass.
        """
        frame = pd.DataFrame([[1]], columns=["İşlem"])

        normalized, _ = normalize_columns(frame)

        name = normalized.columns[0]
        assert name == "işlem"
        assert len(name) == len("işlem")
        assert not any(unicodedata.combining(ch) for ch in name)
        _quote(name)

    def test_identifiers_keep_their_leading_zeros(self, tmp_path: Path) -> None:
        """`007` is not the number seven; the zeros are what make it an identifier.

        Pandas reads it as 7 whatever the quoting, and by the time the frame
        exists the text is gone, so nothing downstream can tell (#281).
        """
        path = tmp_path / "uyeler.csv"
        path.write_text("uye_no,tutar\n007,10\n008,20\n0123,30\n", encoding="utf-8")

        table = load_csv(path)

        assert list(table.frame["uye_no"]) == ["007", "008", "0123"]
        assert "leading_zeros_preserved" in {i.code for i in table.issues}, (
            "keeping them is still a decision about the data, so it is reported"
        )

    def test_the_join_to_a_text_keyed_source_survives(self, tmp_path: Path) -> None:
        """The damaging half: the join vanished, silently.

        Parquet carries its types in the schema, so the same key stayed text
        there while the CSV became an integer. detect_relationships then
        reported two obviously-joined tables as unrelated -- the same outcome
        the `_slugify` docstring records for movieId, by a different route.
        """
        source = tmp_path / "kaynak"
        source.mkdir()
        pd.DataFrame({"uye_no": ["007", "008", "0123"], "ad": ["a", "b", "c"]}).to_parquet(
            source / "uyeler.parquet"
        )
        (source / "odemeler.csv").write_text(
            "uye_no,tutar\n007,10\n008,20\n0123,30\n", encoding="utf-8"
        )

        tables = load_directory(source)
        relationships = detect_relationships(
            profile_tables(tables), {t.name: t.frame for t in tables}
        )

        assert len(relationships) == 1, "three rows match; the join is not optional"
        assert relationships[0].overlap_rate == 1.0

    def test_an_ordinary_number_column_stays_a_number(self, tmp_path: Path) -> None:
        """Guards the fix against reading every integer column as text.

        A blunt `dtype=str` would pass the two tests above and turn every
        measurement in the product into a string.
        """
        path = tmp_path / "olcumler.csv"
        # `0` alone is a real zero, not a padded identifier, and must not trip it.
        path.write_text("adet,tutar\n7,100\n0,250\n12,0\n", encoding="utf-8")

        table = load_csv(path)

        assert pd.api.types.is_integer_dtype(table.frame["adet"])
        assert pd.api.types.is_integer_dtype(table.frame["tutar"])
        assert "leading_zeros_preserved" not in {i.code for i in table.issues}

    def test_a_formula_column_is_not_called_empty(self, tmp_path: Path) -> None:
        """The column is full of formulas; the run said "all-null" (#288).

        Someone looking at that workbook sees numbers. Being told the column was
        empty sends them to the column instead of to how the file was written.
        """
        from openpyxl import Workbook

        path = tmp_path / "formullu.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["a", "b", "toplam"])
        ws.append([1, 2, "=A2+B2"])
        ws.append([3, 4, "=A3+B3"])
        wb.save(path)

        table = load_excel(path)[0]

        codes = {i.code for i in table.issues}
        assert "formula_columns_without_results" in codes
        assert "empty_columns_dropped" not in codes, "the old message was not true"
        detail = next(i.detail for i in table.issues if i.code == "formula_columns_without_results")
        assert "toplam" in detail
        assert "re-save" in detail, "a message with no next step is half a message"

    def test_a_genuinely_empty_column_is_still_called_empty(self, tmp_path: Path) -> None:
        """Guards the fix: not every dropped column is a formula.

        Renaming the message unconditionally would hide the ordinary case, which
        is a real and different thing to tell someone about.
        """
        from openpyxl import Workbook

        path = tmp_path / "bos_kolon.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["a", "b", "kullanilmayan"])
        ws.append([1, 2, None])
        ws.append([3, 4, None])
        wb.save(path)

        table = load_excel(path)[0]

        assert "empty_columns_dropped" in {i.code for i in table.issues}
        assert "formula_columns_without_results" not in {i.code for i in table.issues}

    def test_empty_sheet_reported_not_crashed(self, tmp_path: Path) -> None:
        path = tmp_path / "book.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="Data", index=False)
            pd.DataFrame().to_excel(writer, sheet_name="Blank", index=False)
        tables = {t.sheet_name: t for t in load_excel(path)}
        assert "empty_sheet" in {i.code for i in tables["Blank"].issues}


class TestSemanticTypes:
    def test_primary_key_is_identifier(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[MASTER].column("physician_id")
        assert col is not None
        assert col.semantic_type is SemanticType.IDENTIFIER
        assert col.is_unique

    def test_foreign_key_is_identifier_despite_repetition(
        self, cards_by_name: dict[str, DataCard]
    ) -> None:
        """Regression: FK columns repeat heavily and were misread as measurements."""
        fk = cards_by_name[TRANSACTIONS].column("physician_id")
        assert fk is not None
        assert fk.semantic_type is SemanticType.IDENTIFIER
        assert not fk.is_unique

        provider_ref = cards_by_name[LEDGER].column("provider_ref")
        assert provider_ref is not None
        assert provider_ref.semantic_type is SemanticType.IDENTIFIER

    def test_constant_column_detected(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[MASTER].column("region_code")
        assert col is not None
        assert col.semantic_type is SemanticType.CONSTANT

    def test_datetime_detected(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[TRANSACTIONS].column("txn_date")
        assert col is not None
        assert col.semantic_type is SemanticType.DATETIME
        assert col.datetime is not None
        assert col.datetime.span_days > 2000, "multi-year span drives split strategy"

    def test_binary_label_detected_as_boolean(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[TRANSACTIONS].column("flagged")
        assert col is not None
        assert col.semantic_type is SemanticType.BOOLEAN

    def test_low_cardinality_code_is_not_an_identifier(
        self, cards_by_name: dict[str, DataCard]
    ) -> None:
        """`account_code` matches an id-ish name but has 7 values — it is categorical."""
        col = cards_by_name[LEDGER].column("account_code")
        assert col is not None
        assert col.semantic_type is not SemanticType.IDENTIFIER

    def test_all_null_column_is_empty(self) -> None:
        series = pd.Series([None, None, None], dtype="object")
        assert infer_semantic_type("x", series, 3) is SemanticType.EMPTY

    def test_unique_long_reviews_are_text_not_identifiers(self) -> None:
        reviews = pd.Series(
            [
                f"Review {index}: the package arrived intact and the product worked as expected."
                for index in range(60)
            ]
        )
        assert infer_semantic_type("review_comment", reviews, len(reviews)) is SemanticType.TEXT

    def test_dirty_mixed_format_datetime_retains_parse_evidence(self) -> None:
        values = pd.Series(
            [f"2024-01-{day:02d}" for day in range(1, 21)]
            + [f"{day:02d}/02/2024" for day in range(1, 21)]
            + ["unknown"] * 10
        )
        card = profile_table(
            LoadedTable(
                name="events",
                frame=pd.DataFrame({"event_time": values}),
                source_uri="mem",
                source_format="csv",
            )
        )
        column = card.column("event_time")
        assert column is not None
        assert column.semantic_type is SemanticType.DATETIME
        assert column.datetime is not None
        assert column.datetime.parsed_count == 40
        assert column.datetime.invalid_count == 10
        assert column.datetime.parse_rate == pytest.approx(0.8)
        assert any("unparsed values remain" in note for note in column.notes)

    def test_datetime_inference_samples_across_an_ordered_column(self) -> None:
        values = pd.Series(
            ["not-yet-recorded"] * 100
            + list(pd.date_range("2024-01-01", periods=900, freq="h").astype(str))
        )
        card = profile_table(
            LoadedTable(
                name="events",
                frame=pd.DataFrame({"event_time": values}),
                source_uri="mem",
                source_format="csv",
            )
        )
        column = card.column("event_time")
        assert column is not None
        assert column.semantic_type is SemanticType.DATETIME
        assert column.datetime is not None
        assert column.datetime.parsed_count == 900
        assert column.datetime.invalid_count == 100
        assert column.datetime.parse_rate == pytest.approx(0.9)

    def test_datetime_stats_cover_rows_beyond_inference_sample(self) -> None:
        values = pd.Series(pd.date_range("2020-01-01", periods=600, freq="D").astype(str))
        card = profile_table(
            LoadedTable(
                name="events",
                frame=pd.DataFrame({"event_time": values}),
                source_uri="mem",
                source_format="csv",
            )
        )
        column = card.column("event_time")
        assert column is not None and column.datetime is not None
        assert column.datetime.parsed_count == 600
        assert column.datetime.span_days == 599


class TestSensitivityAndRedaction:
    @pytest.mark.parametrize("column", ["full_name", "email_address", "license_no"])
    def test_pii_columns_flagged(self, cards_by_name: dict[str, DataCard], column: str) -> None:
        col = cards_by_name[MASTER].column(column)
        assert col is not None
        assert col.sensitivity is Sensitivity.PII

    def test_pii_values_never_leave_the_data_plane(
        self, cards_by_name: dict[str, DataCard]
    ) -> None:
        """The single most important privacy assertion in the codebase."""
        for card in cards_by_name.values():
            for col in card.columns:
                if col.sensitivity is Sensitivity.PII:
                    assert col.sample_values == []
                    assert col.top_values == []

    def test_pii_absent_from_llm_facing_digest(self, cards_by_name: dict[str, DataCard]) -> None:
        digest = datacard_digest(cards_by_name[MASTER])
        assert "example-clinic.test" not in digest
        assert "Dr. Physician" not in digest
        assert "PII/redacted" in digest

    def test_non_pii_columns_keep_samples(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[MASTER].column("specialty")
        assert col is not None
        assert col.sample_values

    def test_text_shape_is_row_free_and_visible_to_agents(self) -> None:
        secret = "Confidential narrative that must never enter the digest"
        values = pd.Series([f"{secret}; case number {index}." for index in range(60)])
        card = profile_table(
            LoadedTable(
                name="reviews",
                frame=pd.DataFrame({"comment_message": values}),
                source_uri="mem",
                source_format="csv",
            )
        )
        column = card.column("comment_message")
        assert column is not None and column.text is not None
        assert column.text.measured_count == 60
        assert column.text.p50_length > 40
        assert column.text.p50_token_count >= 8
        assert column.text.dominant_script.value == "latin"
        assert column.text.punctuation_character_rate > 0
        rendered_shape = column.text.model_dump_json()
        digest = datacard_digest(card)
        assert secret not in rendered_shape
        assert secret not in digest
        assert "text_len_p50=" in digest
        assert "script=latin" in digest

    def test_pii_text_shape_reports_pattern_without_values(self) -> None:
        values = pd.Series([f"person{index}@example.test" for index in range(60)])
        card = profile_table(
            LoadedTable(
                name="contacts",
                frame=pd.DataFrame({"contact_email": values}),
                source_uri="mem",
                source_format="csv",
            )
        )
        column = card.column("contact_email")
        assert column is not None and column.text is not None
        assert column.sensitivity is Sensitivity.PII
        assert column.text.email_pattern_rate == 1.0
        assert column.sample_values == []
        digest = datacard_digest(card)
        assert "person0@example.test" not in digest
        assert "email=100.0%" in digest


class TestProfileStatistics:
    def test_target_missingness_measured(self, cards_by_name: dict[str, DataCard]) -> None:
        col = cards_by_name[COMPENSATION].column("annual_comp")
        assert col is not None
        assert col.null_rate == pytest.approx(0.11, abs=0.005)

    def test_rare_label_visible_in_top_values(self, cards_by_name: dict[str, DataCard]) -> None:
        """Statistical support for 'fraud detection' must be inspectable."""
        col = cards_by_name[TRANSACTIONS].column("flagged")
        assert col is not None
        counts = {v.value: v.count for v in col.top_values}
        assert counts["1"] < 100, "planted positive rate is ~0.3%"

    def test_candidate_targets_exclude_identifiers(
        self, cards_by_name: dict[str, DataCard]
    ) -> None:
        targets = {c.name for c in cards_by_name[COMPENSATION].candidate_targets()}
        assert "annual_comp" in targets
        assert "physician_id" not in targets

    def test_digest_is_small_enough_for_local_models(self, cards: list[DataCard]) -> None:
        total = sum(len(datacard_digest(c)) for c in cards)
        assert total < 8_000, f"digest grew to {total} chars; local context budget at risk"

    def test_sampling_marks_the_card(self, tmp_path: Path) -> None:
        from ads.intake.profiler import ProfileOptions

        path = tmp_path / "big.csv"
        pd.DataFrame({"a": range(500)}).to_csv(path, index=False)
        card = profile_table(load_csv(path), ProfileOptions(max_profile_rows=100))
        assert card.sampled
        assert card.profiled_rows == 100
        assert card.n_rows == 500
        assert "profiled_on_sample" in {i.code for i in card.issues}


class TestKeyDetection:
    def test_clean_primary_keys_found(
        self, cards_by_name: dict[str, DataCard], frames: dict[str, pd.DataFrame]
    ) -> None:
        keys = detect_primary_keys(cards_by_name[MASTER], frames[MASTER])
        assert any(k.columns == ["physician_id"] and k.is_clean_key for k in keys)

    def test_composite_key_found_when_no_single_key(self) -> None:
        # No column is unique on its own; only (a_id, b_id) together identify a row.
        frame = pd.DataFrame(
            {"a_id": [1, 1, 2, 2], "b_id": [10, 20, 10, 20], "value": [1.5, 2.5, 1.5, 2.5]}
        )
        card = profile_table(
            LoadedTable(name="t", frame=frame, source_uri="mem", source_format="csv")
        )
        keys = detect_primary_keys(card, frame)
        assert any(set(k.columns) == {"a_id", "b_id"} for k in keys)


class TestRelationshipDetection:
    def _find(self, relationships: list, from_table: str, from_col: str, to_table: str):
        return next(
            (
                r
                for r in relationships
                if r.from_table == from_table
                and r.from_columns == [from_col]
                and r.to_table == to_table
            ),
            None,
        )

    def test_ledger_fk_found_with_correct_row_overlap(self, relationships: list) -> None:
        """The headline number a human sees at the gate: 94.2% matched, 5.8% orphaned."""
        rel = self._find(relationships, LEDGER, "provider_ref", MASTER)
        assert rel is not None, "the primary foreign key must be discovered"
        assert rel.overlap_rate == pytest.approx(0.942, abs=0.01)
        assert rel.orphan_rate == pytest.approx(0.058, abs=0.01)

    def test_distinct_overlap_diverges_from_row_overlap(self, relationships: list) -> None:
        """Many rare orphan ids: distinct overlap is far lower, and that is informative."""
        rel = self._find(relationships, LEDGER, "provider_ref", MASTER)
        assert rel is not None
        assert rel.distinct_overlap_rate < rel.overlap_rate - 0.3

    def test_transactions_fk_found(self, relationships: list) -> None:
        rel = self._find(relationships, TRANSACTIONS, "physician_id", MASTER)
        assert rel is not None
        assert rel.overlap_rate == pytest.approx(1.0, abs=0.001)
        assert rel.name_affinity > 0.9

    def test_spurious_numeric_containment_rejected(self, relationships: list) -> None:
        """Regression: small integer ranges sit inside large id ranges by coincidence."""
        assert self._find(relationships, COMPENSATION, "years_experience", LEDGER) is None
        assert self._find(relationships, TRANSACTIONS, "flagged", LEDGER) is None
        assert self._find(relationships, COMPENSATION, "physician_id", LEDGER) is None

    def test_no_relationship_targets_a_surrogate_row_id(self, relationships: list) -> None:
        for rel in relationships:
            assert rel.to_columns != ["entry_id"], f"spurious FK into a row counter: {rel}"

    def test_one_to_one_pairs_not_double_reported(self, relationships: list) -> None:
        pairs = [
            tuple(
                sorted(
                    [
                        f"{r.from_table}.{r.from_columns[0]}",
                        f"{r.to_table}.{r.to_columns[0]}",
                    ]
                )
            )
            for r in relationships
            if r.cardinality.value == "1:1"
        ]
        assert len(pairs) == len(set(pairs))

    def test_digest_renders_without_error(self, relationships: list) -> None:
        digest = relationships_digest(relationships)
        assert "provider_ref" in digest
        assert relationships_digest([]).startswith("No candidate relationships")

    def test_min_overlap_threshold_respected(self) -> None:
        frames = {
            "child": pd.DataFrame({"ref_id": [1, 2, 3, 4]}),
            "parent": pd.DataFrame({"key_id": [90, 91, 92, 93]}),
        }
        cards = [
            profile_table(LoadedTable(name=n, frame=f, source_uri="mem", source_format="csv"))
            for n, f in frames.items()
        ]
        assert detect_relationships(cards, frames, KeyDetectionOptions()) == []


class TestNameAffinity:
    def test_identical_names_score_high(self) -> None:
        assert name_affinity("physician_id", "physician_id") == 1.0

    def test_generic_tokens_do_not_create_affinity(self) -> None:
        """`entry_id` and `provider_id` share only the filler token `id`."""
        assert name_affinity("entry_id", "provider_id") == 0.0

    def test_unrelated_names_score_zero(self) -> None:
        assert name_affinity("provider_ref", "physician_id") == 0.0


class TestMeasureRelationship:
    def test_dtype_coercion_matches_int_to_string_keys(self) -> None:
        """Enterprise exports store the same id as 1234 and '1234'."""
        rel = measure_relationship(
            "child",
            "ref",
            pd.Series([1, 2, 3]),
            "parent",
            "key",
            pd.Series(["1", "2", "3"]),
        )
        assert rel.overlap_rate == 1.0

    def test_empty_child_is_not_a_relationship(self) -> None:
        rel = measure_relationship(
            "child",
            "ref",
            pd.Series([], dtype="float64"),
            "parent",
            "key",
            pd.Series([1, 2]),
        )
        assert rel.overlap_rate == 0.0

    def test_rows_per_parent_names_both_denominators(self) -> None:
        rel = measure_relationship(
            "items",
            "order_id",
            pd.Series([1, 1, 1, 2]),
            "orders",
            "order_id",
            pd.Series([1, 2, 3, 4]),
        )
        measured = rel.rows_per_parent
        assert measured is not None
        assert measured.child_non_null_rows == 4
        assert measured.matched_child_rows == 4
        assert measured.referenced_parent_count == 2
        assert measured.all_parent_count == 4
        assert measured.mean_rows_per_referenced_parent == pytest.approx(2.0)
        assert measured.mean_rows_per_all_parents == pytest.approx(1.0)
        assert measured.median_rows_per_referenced_parent == pytest.approx(2.0)
        assert measured.p90_rows_per_referenced_parent == pytest.approx(2.8)
        assert measured.median_rows_per_all_parents == pytest.approx(0.5)
