"""Deterministic synthetic loan-default scenario for the assurance half-corpus."""

from __future__ import annotations

import csv
import io
import math
import random
from datetime import date, timedelta
from typing import Any

SEED = 7319
CUTOFF = date(2023, 1, 1)
EXTRACTION_DATE = date(2024, 2, 15)
TARGET = "default_90d"
POST_OUTCOME_FEATURE = "servicing_contact_flag"
SAFE_FEATURES = [
    "applicant_age",
    "annual_income",
    "prior_defaults_365d",
    "debt_to_income",
    "credit_band",
    "bureau_utilization",
]
CATEGORICAL_FEATURES = ["credit_band", POST_OUTCOME_FEATURE]
FIELDNAMES = [
    "application_id",
    "borrower_id",
    "application_time",
    "applicant_age",
    "annual_income",
    "prior_defaults_365d",
    "debt_to_income",
    "credit_band",
    "bureau_utilization",
    "servicing_contact_flag",
    "status_recorded_at",
    "default_90d",
]


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def build_population(seed: int = SEED, borrower_count: int = 520) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    application_number = 0
    for borrower_number in range(borrower_count):
        is_holdout = borrower_number >= int(borrower_count * 0.75)
        start = date(2023, 2, 1) if is_holdout else date(2020, 1, 1)
        end = date(2023, 9, 1) if is_holdout else date(2022, 9, 1)
        borrower_id = f"B{borrower_number:05d}"
        applicant_age = rng.randint(20, 78)
        annual_income = round(math.exp(rng.gauss(10.8, 0.45)), 2)
        application_count = rng.randint(1, 3)
        application_dates = sorted(
            _random_date(rng, start, end) for _ in range(application_count)
        )
        previous_defaults: list[date] = []

        for application_time in application_dates:
            application_number += 1
            prior_defaults = sum(
                1
                for previous in previous_defaults
                if (application_time - previous).days <= 365
            )
            debt_to_income = min(0.95, max(0.05, rng.betavariate(2.3, 4.0)))
            utilization: float | str = round(min(1.0, max(0.0, rng.gauss(0.48, 0.22))), 3)
            if rng.random() < 0.10:
                utilization = ""
            latent_utilization = 0.48 if utilization == "" else float(utilization)
            logit = (
                -3.4
                + 2.5 * debt_to_income
                + 0.85 * prior_defaults
                + 1.1 * latent_utilization
                - 0.000006 * annual_income
            )
            defaulted = int(rng.random() < _sigmoid(logit))
            if defaulted:
                previous_defaults.append(application_time + timedelta(days=60))
            band_score = max(
                0,
                min(3, int(3 - debt_to_income * 3 - prior_defaults + rng.gauss(0, 0.5))),
            )
            credit_band = ["D", "C", "B", "A"][band_score]
            # Deliberately weak post-outcome signal: only 58% agreement with the target.
            agrees = rng.random() < 0.58
            contact = defaulted if agrees else 1 - defaulted
            status_recorded_at = application_time + timedelta(days=91)
            assert status_recorded_at < EXTRACTION_DATE
            rows.append(
                {
                    "application_id": f"A{application_number:06d}",
                    "borrower_id": borrower_id,
                    "application_time": application_time.isoformat(),
                    "applicant_age": applicant_age,
                    "annual_income": annual_income,
                    "prior_defaults_365d": prior_defaults,
                    "debt_to_income": round(debt_to_income, 4),
                    "credit_band": credit_band,
                    "bureau_utilization": utilization,
                    POST_OUTCOME_FEATURE: "contacted" if contact else "not_contacted",
                    "status_recorded_at": status_recorded_at.isoformat(),
                    TARGET: defaulted,
                }
            )
    return sorted(rows, key=lambda row: (row["application_time"], row["application_id"]))


def population_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def notebook_document(*, features: list[str], split_mode: str = "temporal") -> dict[str, Any]:
    numeric = [feature for feature in features if feature not in CATEGORICAL_FEATURES]
    categorical = [feature for feature in features if feature in CATEGORICAL_FEATURES]
    split_source = """train = data.loc[data["application_time"] < CUTOFF].copy()
holdout = data.loc[data["application_time"] >= CUTOFF].copy()
group_overlap = sorted(set(train["borrower_id"]) & set(holdout["borrower_id"]))
if group_overlap:
    raise AssertionError(f"borrower overlap across split: {group_overlap[:5]}")"""
    if split_mode == "random_group":
        split_source = """from sklearn.model_selection import GroupShuffleSplit
splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=7319)
train_index, holdout_index = next(
    splitter.split(data, groups=data["borrower_id"])
)
train = data.iloc[train_index].copy()
holdout = data.iloc[holdout_index].copy()
group_overlap = sorted(set(train["borrower_id"]) & set(holdout["borrower_id"]))"""

    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": (
                "# Ninety-day loan default evaluation\n"
                "Review brief.md before interpreting results."
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
            """data = pd.read_csv("dataset/applications.csv", parse_dates=[
    "application_time", "status_recorded_at"
])
TARGET = "default_90d"
CUTOFF = pd.Timestamp("2023-01-01")"""
        ),
        _code_cell(split_source),
        _code_cell(
            f"""FEATURE_COLUMNS = {features!r}
NUMERIC_FEATURES = {numeric!r}
CATEGORICAL_FEATURES = {categorical!r}"""
        ),
        _code_cell(_model_source(seed=SEED, entity="borrower")),
    ]
    return _document(cells)


def global_fit_notebook() -> dict[str, Any]:
    document = notebook_document(features=list(SAFE_FEATURES))
    document["cells"][5]["source"] = _global_fit_source(seed=SEED, entity="borrower")
    return document


def _model_source(*, seed: int, entity: str) -> str:
    return f"""numeric_pipeline = Pipeline([
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
    ("classifier", LogisticRegression(max_iter=1000, random_state={seed})),
])
model.fit(train[FEATURE_COLUMNS], train[TARGET])
probability = model.predict_proba(holdout[FEATURE_COLUMNS])[:, 1]
roc_auc = float(roc_auc_score(holdout[TARGET], probability))
evaluation = {{
    "metric": "roc_auc",
    "value": roc_auc,
    "features": FEATURE_COLUMNS,
    "train_rows": int(len(train)),
    "holdout_rows": int(len(holdout)),
    "train_entities": int(train["{entity}_id"].nunique()),
    "holdout_entities": int(holdout["{entity}_id"].nunique()),
    "group_overlap_count": len(group_overlap),
}}
Path("evaluation.json").write_text(json.dumps(evaluation, indent=2, sort_keys=True))
print(json.dumps(evaluation, indent=2, sort_keys=True))"""


def _global_fit_source(*, seed: int, entity: str) -> str:
    return f"""numeric_pipeline = Pipeline([
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
preprocess = ColumnTransformer(transformers)
all_features = preprocess.fit_transform(data[FEATURE_COLUMNS])
classifier = LogisticRegression(max_iter=1000, random_state={seed})
classifier.fit(all_features[train.index], train[TARGET])
probability = classifier.predict_proba(all_features[holdout.index])[:, 1]
roc_auc = float(roc_auc_score(holdout[TARGET], probability))
evaluation = {{
    "metric": "roc_auc",
    "value": roc_auc,
    "features": FEATURE_COLUMNS,
    "train_rows": int(len(train)),
    "holdout_rows": int(len(holdout)),
    "train_entities": int(train["{entity}_id"].nunique()),
    "holdout_entities": int(holdout["{entity}_id"].nunique()),
    "group_overlap_count": len(group_overlap),
    "preprocessing_fit_scope": "all_rows_before_split",
}}
Path("evaluation.json").write_text(json.dumps(evaluation, indent=2, sort_keys=True))
print(json.dumps(evaluation, indent=2, sort_keys=True))"""


def _document(cells: list[dict[str, Any]]) -> dict[str, Any]:
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
