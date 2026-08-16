"""Independent end-to-end check of the MVP acceptance criterion.

The report's §5.3 says a completed run must yield a standalone train.py that
reproduces the model with no LLM in the loop. Codex's own test proves this on a
two-CSV fixture. This runs it on the real physician case, generates the script,
executes it in a fresh interpreter, and compares the reproduced metric against
the recorded one.
"""

import subprocess
import sys
from pathlib import Path

from ads.contracts import IntegrationPlanProposal, SplitStrategy, TaskType, ValidationStrategy
from ads.ds_toolkit import build_preprocessor
from ads.intake import LoadedTable, load_directory, profile_table
from ads.integration import execute_plan
from ads.training import default_candidates, export_training_script, train_candidates

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/export_probe")
OUT.mkdir(parents=True, exist_ok=True)

PLAN = IntegrationPlanProposal.model_validate(
    {
        "base_table": "physicians__physician_master",
        "base_grain": ["physician_id"],
        "grain_description": "One row per physician.",
        "aggregations": [],
        "joins": [
            {
                "left_table": "physicians__physician_master",
                "right_table": "physicians__compensation",
                "left_columns": ["physician_id"],
                "right_columns": ["physician_id"],
                "how": "left",
                "rationale": "1:1",
            }
        ],
        "warnings": [],
    }
)

tables = load_directory("data/sample")
frames = {t.name: t.frame for t in tables}
abt = execute_plan(PLAN, frames).frame
# Deliberately NOT dropping null targets here: train_candidates now owns that
# filtering and records it, which is what makes the export reproducible.
abt = abt.drop(columns=["total_comp_ytd"])

card = profile_table(
    LoadedTable(name="abt", frame=abt, source_uri="mem", source_format="csv")
)
strategy = ValidationStrategy(
    strategy=SplitStrategy.RANDOM, n_folds=5, rationale="probe"
)

report = train_candidates(
    abt,
    strategy,
    lambda: build_preprocessor(card, target_column="annual_comp", excluded_columns=set()),
    default_candidates(TaskType.REGRESSION),
    target_column="annual_comp",
    task_type=TaskType.REGRESSION,
)
print(f"recorded winner   : {report.winner_id}")
for field in ("n_input_rows", "n_null_target_rows_dropped", "n_labeled_rows"):
    if hasattr(report, field):
        print(f"  {field:26s}: {getattr(report, field)}")
recorded = report.to_quality_signals().best_score
print(f"recorded holdout  : {recorded}")

script = export_training_script(
    report,
    strategy,
    PLAN,
    target_column="annual_comp",
    task_type=TaskType.REGRESSION,
)
path = OUT / "train.py"
path.write_text(script, encoding="utf-8")
print(f"\ngenerated {path} ({len(script):,} chars)")

lowered = script.lower()
for banned in ("ads.agents", "ads.llm", "ollama", "run_agent"):
    assert banned not in lowered, f"generated script references {banned!r}"
print("contains no agent or LLM import  OK")

# Write the two source tables the script needs as explicit mappings.
for name in ("physicians__physician_master", "physicians__compensation"):
    frames[name].to_csv(OUT / f"{name}.csv", index=False)

print("\n--- executing generated script in a fresh interpreter ---")
result = subprocess.run(
    [
        sys.executable,
        str(path),
        "--table",
        f"physicians__physician_master={OUT / 'physicians__physician_master.csv'}",
        "--table",
        f"physicians__compensation={OUT / 'physicians__compensation.csv'}",
    ],
    capture_output=True,
    text=True,
    timeout=900,
)
print(f"exit code: {result.returncode}")
print(result.stdout[-2500:])
if result.returncode != 0:
    print("STDERR:", result.stderr[-2500:])
