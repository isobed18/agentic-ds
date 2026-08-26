"""Executable, deterministic training-script export tests."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.preprocessing import StandardScaler

from ads.contracts import IntegrationPlan, JoinStep, SplitStrategy, TaskType, ValidationStrategy
from ads.intake import LoadedTable, profile_table
from ads.integration import execute_plan
from ads.training import default_candidates, export_training_script, train_candidates


def _subprocess_env() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, existing]))
    return environment


def test_exported_script_runs_and_reproduces_recorded_metric(tmp_path: Path) -> None:
    rng = np.random.default_rng(9917)
    x = rng.normal(size=180)
    z = rng.normal(size=180)
    base = pd.DataFrame({"row_id": np.arange(180), "x": x})
    features = pd.DataFrame({"row_id": np.arange(180), "z": z})
    base["target"] = 4.0 * x - 2.5 * z + rng.normal(scale=0.03, size=180)
    base.loc[base.index[::13], "target"] = np.nan
    plan = IntegrationPlan(
        base_table="base",
        base_grain=["row_id"],
        grain_description="One row per synthetic entity.",
        joins=[
            JoinStep(
                left_table="base",
                right_table="features",
                left_columns=["row_id"],
                right_columns=["row_id"],
                how="left",
                rationale="Attach one-to-one source features.",
            )
        ],
    )
    strategy = ValidationStrategy(
        strategy=SplitStrategy.RANDOM,
        n_folds=3,
        test_size=0.2,
        rationale="Seeded independent synthetic entities.",
    )
    abt = execute_plan(plan, {"base": base, "features": features}).frame
    report = train_candidates(
        abt,
        strategy,
        StandardScaler,
        default_candidates(TaskType.REGRESSION)[:2],
        target_column="target",
        task_type=TaskType.REGRESSION,
    )
    base_path = tmp_path / "base.csv"
    feature_path = tmp_path / "features.csv"
    base.to_csv(base_path, index=False)
    features.to_csv(feature_path, index=False)
    source_cards = [
        profile_table(
            LoadedTable(name="base", frame=base, source_uri=str(base_path), source_format="csv")
        ),
        profile_table(
            LoadedTable(
                name="features",
                frame=features,
                source_uri=str(feature_path),
                source_format="csv",
            )
        ),
    ]
    script = export_training_script(
        report,
        strategy,
        plan,
        target_column="target",
        task_type=TaskType.REGRESSION,
        source_cards=source_cards,
    )

    assert script == export_training_script(
        report,
        strategy,
        plan,
        target_column="target",
        task_type=TaskType.REGRESSION,
        source_cards=source_cards,
    )
    assert "ads.agents" not in script
    assert "ads.llm" not in script
    assert "build_sql(" in script

    script_path = tmp_path / "train.py"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(),
    )

    assert completed.returncode == 0, completed.stderr
    match = re.search(r"^rmse: .* holdout=([-+0-9.eE]+)$", completed.stdout, re.MULTILINE)
    assert match, completed.stdout
    recorded = report.winner.evaluation_for(report.primary_metric).holdout_score
    assert float(match.group(1)) == pytest.approx(recorded, rel=1e-9, abs=1e-12)
    assert report.target_null_rows_dropped == 14
