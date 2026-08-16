"""Structural and counter-tests for the Assurance Corpus half-corpus."""

from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from generators.casebook import CASE_SPECS, build_case  # noqa: E402
from harness.materialize import materialize  # noqa: E402
from harness.score import score_assessment  # noqa: E402


class HalfCorpusStructureTests(unittest.TestCase):
    def test_registry_has_two_complete_scenario_families(self) -> None:
        self.assertEqual(len(CASE_SPECS), 20)
        self.assertEqual(
            {spec.scenario for spec in CASE_SPECS.values()},
            {"readmission", "lending"},
        )
        for scenario in ("readmission", "lending"):
            variants = {
                spec.variant
                for spec in CASE_SPECS.values()
                if spec.scenario == scenario
            }
            self.assertEqual(len(variants), 10)
            self.assertIn("safe", variants)
            self.assertIn("underspecified", variants)

    def test_materialization_enforces_artifact_plane_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hashes = materialize(Path(temporary))

        # Notebook-only, data-only, and brief-only representatives.
        self.assertEqual(
            hashes["c001"]["dataset/encounters.csv"],
            hashes["c006"]["dataset/encounters.csv"],
        )
        self.assertEqual(
            hashes["c001"]["analysis.ipynb"],
            hashes["c005"]["analysis.ipynb"],
        )
        self.assertEqual(
            hashes["c001"]["dataset/encounters.csv"],
            hashes["c010"]["dataset/encounters.csv"],
        )
        self.assertEqual(
            hashes["c001"]["analysis.ipynb"],
            hashes["c010"]["analysis.ipynb"],
        )


    def test_public_payload_does_not_disclose_mutation_labels(self) -> None:
        forbidden = sorted(
            {
                spec.variant
                for spec in CASE_SPECS.values()
                if spec.variant not in {"safe", "underspecified"}
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialize(root)

            for path in sorted((root / "cases").rglob("*")):
                if not path.is_file():
                    continue
                public_text = (
                    path.relative_to(root / "cases").as_posix()
                    + "\n"
                    + path.read_text(encoding="utf-8")
                )
                for label in forbidden:
                    self.assertNotIn(
                        label,
                        public_text,
                        f"{path} discloses hidden mutation label {label!r}",
                    )

            for checksum_path in sorted((root / "cases").glob("*/checksums.json")):
                checksum = json.loads(checksum_path.read_text(encoding="utf-8"))
                self.assertEqual(set(checksum), {"case_id", "sha256"})

    def test_data_faults_do_not_change_the_safe_control_brief(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hashes = materialize(Path(temporary))

        siblings = {
            "c001": {"c003", "c005", "c007", "c008", "c009"},
            "c011": {"c013", "c015", "c017", "c018", "c019"},
        }
        for safe_id, mutated_ids in siblings.items():
            for mutated_id in mutated_ids:
                self.assertEqual(
                    hashes[safe_id]["brief.md"],
                    hashes[mutated_id]["brief.md"],
                    f"{mutated_id} changes both the data fault and its public brief",
                )

    def test_public_briefs_do_not_state_known_diagnoses(self) -> None:
        forbidden_phrases = {
            "after outcomes are known",
            "cross the evaluation boundary",
            "does not preserve the future holdout period",
            "follows the intended split",
            "including defaults after",
            "including encounters after",
            "not yet mature",
            "otherwise safe features",
            "uses only the application-time features",
        }
        for brief_path in sorted((BENCHMARK_ROOT / "cases").glob("*/brief.md")):
            brief = brief_path.read_text(encoding="utf-8").lower()
            for phrase in forbidden_phrases:
                self.assertNotIn(
                    phrase,
                    brief,
                    f"{brief_path} states the diagnosis with {phrase!r}",
                )

    def test_entity_contamination_is_not_duplicate_contamination(self) -> None:
        _, safe_csv, _ = build_case("c001")
        _, contaminated_csv, _ = build_case("c003")
        safe_rows = list(csv.DictReader(io.StringIO(safe_csv)))
        contaminated_rows = list(csv.DictReader(io.StringIO(contaminated_csv)))

        def overlap(rows: list[dict[str, str]]) -> set[str]:
            development = {
                row["patient_id"]
                for row in rows
                if row["prediction_time"] < "2023-01-01"
            }
            holdout = {
                row["patient_id"]
                for row in rows
                if row["prediction_time"] >= "2023-01-01"
            }
            return development & holdout

        self.assertFalse(overlap(safe_rows))
        self.assertEqual(len(overlap(contaminated_rows)), 12)
        self.assertEqual(len(safe_rows), len(contaminated_rows))

    def test_weak_post_outcome_signal_is_not_a_target_copy(self) -> None:
        _, lending_csv, notebook = build_case("c012")
        rows = list(csv.DictReader(io.StringIO(lending_csv)))
        agreements = [
            (row["servicing_contact_flag"] == "contacted")
            == (row["default_90d"] == "1")
            for row in rows
        ]
        agreement_rate = sum(agreements) / len(agreements)

        self.assertGreater(agreement_rate, 0.53)
        self.assertLess(agreement_rate, 0.63)
        self.assertIn(
            "servicing_contact_flag",
            notebook["cells"][4]["source"],
        )

    def test_low_gap_constructions_have_distinct_oracle_axes(self) -> None:
        procedural = json.loads(
            (BENCHMARK_ROOT / "oracle/c006.json").read_text(encoding="utf-8")
        )
        weak_signal = json.loads(
            (BENCHMARK_ROOT / "oracle/c012.json").read_text(encoding="utf-8")
        )
        self.assertEqual(procedural["evidence_mode"], "procedural_only")
        self.assertEqual(
            procedural["invalidity_mechanism"],
            "global_fit_before_split",
        )
        self.assertEqual(weak_signal["evidence_mode"], "signal_bearing")
        self.assertEqual(
            weak_signal["invalidity_mechanism"],
            "post_outcome_feature",
        )
        self.assertEqual(procedural["measurements"]["effect_bin"], "pending_thresholds")
        self.assertEqual(weak_signal["measurements"]["effect_bin"], "pending_thresholds")

    def test_scorer_exposes_unsafe_pass_instead_of_only_correctness(self) -> None:
        schema = json.loads(
            (BENCHMARK_ROOT / "schemas/assessment.schema.json").read_text(
                encoding="utf-8"
            )
        )
        oracle = json.loads(
            (BENCHMARK_ROOT / "oracle/c002.json").read_text(encoding="utf-8")
        )
        assessment = json.loads(
            (BENCHMARK_ROOT / "pilot_assessments/c002.json").read_text(
                encoding="utf-8"
            )
        )
        unsafe = deepcopy(assessment)
        unsafe["disposition"] = "supported"
        unsafe["findings"] = []

        score = score_assessment(oracle, unsafe, schema)

        self.assertFalse(score["disposition_correct"])
        self.assertTrue(score["unsafe_pass"])


if __name__ == "__main__":
    unittest.main()
