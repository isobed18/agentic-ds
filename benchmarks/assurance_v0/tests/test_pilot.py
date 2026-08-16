"""Counter-checks for the Assurance Corpus v0.1 pilot seams."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from generators.readmission import POST_OUTCOME_FEATURE  # noqa: E402
from harness.execute_notebook import execute_notebook  # noqa: E402
from harness.materialize import materialize  # noqa: E402
from harness.score import score_assessment  # noqa: E402


class PilotVerticalSliceTests(unittest.TestCase):
    def test_siblings_share_population_and_materialization_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = materialize(root)
            second = materialize(root)

            self.assertEqual(first, second)
            self.assertEqual(
                first["c001"]["dataset/encounters.csv"],
                first["c002"]["dataset/encounters.csv"],
            )
            self.assertNotEqual(
                first["c001"]["analysis.ipynb"],
                first["c002"]["analysis.ipynb"],
            )

    def test_mutation_changes_feature_cell_without_changing_population(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialize(root)
            safe = json.loads((root / "cases/c001/analysis.ipynb").read_text())
            mutated = json.loads((root / "cases/c002/analysis.ipynb").read_text())

            safe_feature_cell = safe["cells"][4]["source"]
            mutated_feature_cell = mutated["cells"][4]["source"]
            self.assertNotIn(POST_OUTCOME_FEATURE, safe_feature_cell)
            self.assertIn(POST_OUTCOME_FEATURE, mutated_feature_cell)
            self.assertEqual(
                (root / "cases/c001/dataset/encounters.csv").read_bytes(),
                (root / "cases/c002/dataset/encounters.csv").read_bytes(),
            )

    def test_notebooks_execute_and_fixture_assessments_score(self) -> None:
        schema = json.loads(
            (BENCHMARK_ROOT / "schemas/assessment.schema.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materialize(root)
            metrics: dict[str, float] = {}

            for case_id in ("c001", "c002"):
                notebook = root / f"cases/{case_id}/analysis.ipynb"
                execute_notebook(notebook, root / f"results/{case_id}.executed.ipynb")
                evaluation = json.loads(
                    (root / f"cases/{case_id}/evaluation.json").read_text(encoding="utf-8")
                )
                metrics[case_id] = evaluation["value"]

                oracle = json.loads(
                    (BENCHMARK_ROOT / f"oracle/{case_id}.json").read_text(encoding="utf-8")
                )
                assessment = json.loads(
                    (BENCHMARK_ROOT / f"pilot_assessments/{case_id}.json").read_text(
                        encoding="utf-8"
                    )
                )
                score = score_assessment(oracle, assessment, schema)
                self.assertTrue(score["disposition_correct"])
                self.assertFalse(score["unsafe_pass"])
                self.assertFalse(score["safe_rejection"])

            self.assertGreater(metrics["c002"], metrics["c001"] + 0.20)
            self.assertGreater(metrics["c002"], 0.99)


if __name__ == "__main__":
    unittest.main()
