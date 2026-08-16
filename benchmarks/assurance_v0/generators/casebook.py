"""Opaque case registry and isolated artifact construction for the half-corpus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from generators import lending, readmission
from generators.mutations import (
    add_cross_fold_duplicates,
    contaminate_entity_split,
    include_post_outcome_feature,
    make_labels_immature,
    make_missingness_outcome_dependent,
    use_full_history,
)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    scenario: str
    variant: str


_VARIANTS = [
    "safe",
    "post_outcome_feature",
    "entity_contamination",
    "temporal_lookahead",
    "unwindowed_history",
    "global_fit_before_split",
    "cross_fold_duplicate",
    "outcome_dependent_missingness",
    "immature_labels",
    "underspecified",
]
CASE_SPECS = {
    f"c{index:03d}": CaseSpec(f"c{index:03d}", scenario, variant)
    for index, (scenario, variant) in enumerate(
        [
            *[("readmission", variant) for variant in _VARIANTS],
            *[("lending", variant) for variant in _VARIANTS],
        ],
        start=1,
    )
}
NOTEBOOK_ONLY = {
    "post_outcome_feature",
    "temporal_lookahead",
    "global_fit_before_split",
}
DATA_ONLY = {
    "unwindowed_history",
    "cross_fold_duplicate",
    "outcome_dependent_missingness",
    "immature_labels",
}
BRIEF_ONLY = {"underspecified"}


def build_case(case_id: str) -> tuple[str, str, dict[str, Any]]:
    """Return dataset filename, CSV content, and notebook document for one case."""
    spec = CASE_SPECS[case_id]
    if spec.scenario == "readmission":
        return _build_readmission(spec.variant)
    if spec.scenario == "lending":
        return _build_lending(spec.variant)
    raise ValueError(f"unsupported scenario: {spec.scenario}")


def _build_readmission(variant: str) -> tuple[str, str, dict[str, Any]]:
    rows = readmission.build_population()
    features = list(readmission.SAFE_FEATURES)
    notebook = readmission.notebook_document(features=features)

    if variant == "post_outcome_feature":
        features = include_post_outcome_feature(features, readmission.POST_OUTCOME_FEATURE)
        notebook = readmission.notebook_document(features=features)
    elif variant == "entity_contamination":
        rows = contaminate_entity_split(
            rows,
            entity_field="patient_id",
            time_field="prediction_time",
        )
        notebook["cells"][3]["source"] = _temporal_split_without_assert(
            entity_field="patient_id",
            time_field="prediction_time",
        )
    elif variant == "temporal_lookahead":
        notebook["cells"][3]["source"] = _random_group_split_cell(
            entity_field="patient_id",
            seed=readmission.SEED,
        )
    elif variant == "unwindowed_history":
        rows = use_full_history(
            rows,
            entity_field="patient_id",
            history_field="prior_admits_180d",
        )
    elif variant == "global_fit_before_split":
        notebook["cells"][5]["source"] = _global_fit_cell(
            entity_field="patient_id",
            seed=readmission.SEED,
        )
    elif variant == "cross_fold_duplicate":
        rows = add_cross_fold_duplicates(
            rows,
            entity_field="patient_id",
            record_field="encounter_id",
            time_field="prediction_time",
            recorded_at_field="followup_recorded_at",
            entity_prefix="RDX",
            record_prefix="R",
        )
    elif variant == "outcome_dependent_missingness":
        rows = make_missingness_outcome_dependent(
            rows,
            feature_field="last_hemoglobin",
            target_field=readmission.TARGET,
        )
    elif variant == "immature_labels":
        rows = make_labels_immature(
            rows,
            record_field="encounter_id",
            time_field="prediction_time",
            recorded_at_field="followup_recorded_at",
            horizon_days=30,
        )
    elif variant not in {"safe", "underspecified"}:
        raise ValueError(f"unsupported readmission variant: {variant}")

    return "encounters.csv", readmission.population_csv(rows), notebook


def _build_lending(variant: str) -> tuple[str, str, dict[str, Any]]:
    rows = lending.build_population()
    features = list(lending.SAFE_FEATURES)
    notebook = lending.notebook_document(features=features)

    if variant == "post_outcome_feature":
        features = include_post_outcome_feature(features, lending.POST_OUTCOME_FEATURE)
        notebook = lending.notebook_document(features=features)
    elif variant == "entity_contamination":
        rows = contaminate_entity_split(
            rows,
            entity_field="borrower_id",
            time_field="application_time",
        )
        notebook["cells"][3]["source"] = _temporal_split_without_assert(
            entity_field="borrower_id",
            time_field="application_time",
        )
    elif variant == "temporal_lookahead":
        notebook = lending.notebook_document(
            features=features,
            split_mode="random_group",
        )
    elif variant == "unwindowed_history":
        rows = use_full_history(
            rows,
            entity_field="borrower_id",
            history_field="prior_defaults_365d",
        )
    elif variant == "global_fit_before_split":
        notebook = lending.global_fit_notebook()
    elif variant == "cross_fold_duplicate":
        rows = add_cross_fold_duplicates(
            rows,
            entity_field="borrower_id",
            record_field="application_id",
            time_field="application_time",
            recorded_at_field="status_recorded_at",
            entity_prefix="LDX",
            record_prefix="L",
        )
    elif variant == "outcome_dependent_missingness":
        rows = make_missingness_outcome_dependent(
            rows,
            feature_field="bureau_utilization",
            target_field=lending.TARGET,
        )
    elif variant == "immature_labels":
        rows = make_labels_immature(
            rows,
            record_field="application_id",
            time_field="application_time",
            recorded_at_field="status_recorded_at",
            horizon_days=90,
        )
    elif variant not in {"safe", "underspecified"}:
        raise ValueError(f"unsupported lending variant: {variant}")

    return "applications.csv", lending.population_csv(rows), notebook


def _temporal_split_without_assert(*, entity_field: str, time_field: str) -> str:
    return f"""train = data.loc[data["{time_field}"] < CUTOFF].copy()
holdout = data.loc[data["{time_field}"] >= CUTOFF].copy()
group_overlap = sorted(
    set(train["{entity_field}"]) & set(holdout["{entity_field}"])
)"""


def _random_group_split_cell(*, entity_field: str, seed: int) -> str:
    return f"""from sklearn.model_selection import GroupShuffleSplit
splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state={seed})
train_index, holdout_index = next(splitter.split(data, groups=data["{entity_field}"]))
train = data.iloc[train_index].copy()
holdout = data.iloc[holdout_index].copy()
group_overlap = sorted(
    set(train["{entity_field}"]) & set(holdout["{entity_field}"])
)"""


def _global_fit_cell(*, entity_field: str, seed: int) -> str:
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
    "train_entities": int(train["{entity_field}"].nunique()),
    "holdout_entities": int(holdout["{entity_field}"].nunique()),
    "group_overlap_count": len(group_overlap),
    "preprocessing_fit_scope": "all_rows_before_split",
}}
Path("evaluation.json").write_text(json.dumps(evaluation, indent=2, sort_keys=True))
print(json.dumps(evaluation, indent=2, sort_keys=True))"""
