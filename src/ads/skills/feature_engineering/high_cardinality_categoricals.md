---
skill_id: fe.high_cardinality_categoricals
trigger: has_high_cardinality_categorical
applies_to: [feature_investigation, model_investigation]
source: adapted from business-science/ai-data-science-team (MIT)
---

# High-cardinality categoricals

One-hot encoding a column with hundreds of levels produces a sparse, wide matrix
that overfits and slows training. Bucket first, then encode.

## Rules

1. Compute category frequency on training rows. Bucket categories below a chosen
   threshold into an `Other` level, then one-hot encode what remains.
2. If cardinality remains high, target encoding is an option only when it is fit
   independently inside every cross-validation fold.
3. Drop row identifiers and constant columns; they carry no generalisable signal.
4. Treat threshold values as experiment parameters, not universal domain facts.

## Leakage warning

Target encoding fit on the full dataset leaks the target into training features.
It must be a transformer inside the fitted pipeline, never a precomputed column.

## Implementation

Use an unfitted scikit-learn transformer inside a `Pipeline`, so learned
statistics come only from training rows. The current repository does not provide
a custom rare-category transformer; do not import one by name.
