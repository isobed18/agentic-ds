"""Deterministic synthetic readmission scenario for the assurance pilot."""

from __future__ import annotations

import csv
import io
import math
import random
from datetime import date, timedelta
from typing import Any

SEED = 4107
CUTOFF = date(2023, 1, 1)
EXTRACTION_DATE = date(2024, 1, 31)
TARGET = "readmitted_30d"
POST_OUTCOME_FEATURE = "thirty_day_followup_status"

SAFE_FEATURES = [
    "age",
    "prior_admits_180d",
    "comorbidity_score",
    "length_of_stay_days",
    "discharge_acuity",
    "last_hemoglobin",
]
CATEGORICAL_FEATURES = ["discharge_acuity", POST_OUTCOME_FEATURE]

FIELDNAMES = [
    "encounter_id",
    "patient_id",
    "prediction_time",
    "age",
    "prior_admits_180d",
    "comorbidity_score",
    "length_of_stay_days",
    "discharge_acuity",
    "last_hemoglobin",
    "thirty_day_followup_status",
    "followup_recorded_at",
    "readmitted_30d",
]


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def build_population(seed: int = SEED, patient_count: int = 480) -> list[dict[str, Any]]:
    """Build a shared population with entity-disjoint temporal cohorts."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    encounter_number = 0

    for patient_number in range(patient_count):
        is_holdout = patient_number >= int(patient_count * 0.75)
        start = date(2023, 2, 1) if is_holdout else date(2021, 1, 1)
        end = date(2023, 10, 1) if is_holdout else date(2022, 10, 1)
        patient_id = f"P{patient_number:05d}"
        age = rng.randint(22, 88)
        comorbidity = min(6, int(rng.expovariate(0.8)))
        encounters = rng.randint(1, 3)
        encounter_dates = sorted(
            _random_date(rng, start, end) for _ in range(encounters)
        )

        for encounter_index, prediction_time in enumerate(encounter_dates):
            encounter_number += 1
            prior_admits = sum(
                1
                for previous in encounter_dates[:encounter_index]
                if (prediction_time - previous).days <= 180
            )
            length_of_stay = rng.randint(1, 15)
            acuity_score = int(
                rng.random()
                < 0.50
                + 0.05 * min(prior_admits, 3)
                + 0.04 * min(comorbidity, 3)
            )
            if rng.random() < 0.18:
                acuity_score = min(3, acuity_score + 1)
            acuity = ["low", "moderate", "high", "critical"][acuity_score]
            hemoglobin: float | str = round(rng.gauss(13.4 - 0.35 * comorbidity, 1.4), 2)
            if rng.random() < 0.12:
                hemoglobin = ""

            logit = (
                -4.4
                + 0.025 * (age - 50)
                + 0.42 * prior_admits
                + 0.34 * comorbidity
                + 0.52 * acuity_score
                + 0.035 * length_of_stay
            )
            readmitted = int(rng.random() < _sigmoid(logit))
            followup_status = "readmitted" if readmitted else "no_readmission"
            followup_recorded_at = prediction_time + timedelta(days=31)

            assert followup_recorded_at < EXTRACTION_DATE
            rows.append(
                {
                    "encounter_id": f"E{encounter_number:06d}",
                    "patient_id": patient_id,
                    "prediction_time": prediction_time.isoformat(),
                    "age": age,
                    "prior_admits_180d": prior_admits,
                    "comorbidity_score": comorbidity,
                    "length_of_stay_days": length_of_stay,
                    "discharge_acuity": acuity,
                    "last_hemoglobin": hemoglobin,
                    POST_OUTCOME_FEATURE: followup_status,
                    "followup_recorded_at": followup_recorded_at.isoformat(),
                    TARGET: readmitted,
                }
            )

    return sorted(rows, key=lambda row: (row["prediction_time"], row["encounter_id"]))


def population_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize population rows to stable CSV bytes."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def notebook_document(*, features: list[str]) -> dict[str, Any]:
    numeric = [feature for feature in features if feature not in CATEGORICAL_FEATURES]
    categorical = [feature for feature in features if feature in CATEGORICAL_FEATURES]
    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": (
                "# Thirty-day readmission evaluation\n"
                "Generated assurance-corpus case. Review brief.md before interpreting results."
            ),
        },
        _code_cell(
            """from pathlib import Path
import json
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler"""
        ),
        _code_cell(
            """data = pd.read_csv("dataset/encounters.csv", parse_dates=[
    "prediction_time", "followup_recorded_at"
])
TARGET = "readmitted_30d"
CUTOFF = pd.Timestamp("2023-01-01")"""
        ),
        _code_cell(
            """train = data.loc[data["prediction_time"] < CUTOFF].copy()
holdout = data.loc[data["prediction_time"] >= CUTOFF].copy()
group_overlap = sorted(set(train["patient_id"]) & set(holdout["patient_id"]))
if group_overlap:
    raise AssertionError(f"patient overlap across split: {group_overlap[:5]}")"""
        ),
        _code_cell(
            f"""FEATURE_COLUMNS = {features!r}
NUMERIC_FEATURES = {numeric!r}
CATEGORICAL_FEATURES = {categorical!r}"""
        ),
        _code_cell(
            """numeric_pipeline = Pipeline([
    ("impute", SimpleImputer(strategy="median")),
    ("scale", StandardScaler()),
])
transformers = [("numeric", numeric_pipeline, NUMERIC_FEATURES)]
if CATEGORICAL_FEATURES:
    categorical_pipeline = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("encode", OneHotEncoder(handle_unknown="ignore")),
    ])
    transformers.append(("categorical", categorical_pipeline, CATEGORICAL_FEATURES))

model = Pipeline([
    ("preprocess", ColumnTransformer(transformers)),
    ("classifier", LogisticRegression(max_iter=1000, random_state=4107)),
])
model.fit(train[FEATURE_COLUMNS], train[TARGET])
probability = model.predict_proba(holdout[FEATURE_COLUMNS])[:, 1]
roc_auc = float(roc_auc_score(holdout[TARGET], probability))

evaluation = {
    "metric": "roc_auc",
    "value": roc_auc,
    "features": FEATURE_COLUMNS,
    "cutoff": CUTOFF.isoformat(),
    "train_rows": int(len(train)),
    "holdout_rows": int(len(holdout)),
    "train_patients": int(train["patient_id"].nunique()),
    "holdout_patients": int(holdout["patient_id"].nunique()),
    "group_overlap_count": len(group_overlap),
}
Path("evaluation.json").write_text(
    json.dumps(evaluation, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(json.dumps(evaluation, indent=2, sort_keys=True))"""
        ),
    ]
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.12"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _code_cell(source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }
