---
skill_id: fe.high_cardinality_categoricals
trigger: any categorical column with n_unique > 20
applies_to: [feature_pipeline]
source: adapted from business-science/ai-data-science-team (MIT)
---

# High-cardinality categoricals

One-hot encoding a column with hundreds of levels produces a sparse, wide matrix
that overfits and slows training. Bucket first, then encode.

## Rules

1. Compute each category's frequency. Bucket every category whose frequency is
   **below 5%** into a single `Other` level, then one-hot encode what remains.
2. If cardinality is still above ~50 after bucketing, prefer **target encoding**
   over one-hot — but only inside a cross-validation fold (see below).
3. Drop any string column whose `n_unique` equals the row count: it is an
   identifier, not a feature.
4. Drop constant columns (`n_unique == 1`); they carry no signal.

## Leakage warning

Target encoding fit on the full dataset leaks the target into training features.
It must be fit inside each CV fold. Implement it as a transformer inside the
sklearn `Pipeline`, never as a precomputed column on the dataframe.

## Implementation

Use `ads.ds_toolkit.preprocessing.RareCategoryBucketer`, or
`ads.ds_toolkit.preprocessing.build_categorical_pipeline(...)` which already
chains imputation, bucketing and one-hot encoding.

Both return **unfitted** transformers. Do not emit a transformed DataFrame — the
stage contract requires a `Pipeline` object so that every statistic is fit inside
the training fold. Returning a DataFrame means the bucketing thresholds were
learned from the test fold too, which is exactly the leakage this skill exists to
prevent.
