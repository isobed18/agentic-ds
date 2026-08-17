---
skill_id: fe.temporal_features
trigger: has_datetime
applies_to: [feature_investigation, model_investigation]
source: adapted from business-science/ai-data-science-team (MIT)
---

# Features from datetime columns

A raw timestamp is almost never useful to a tree model. Decompose it.

## Rules

1. Derive from each datetime column: year, month, day-of-week, day-of-month,
   quarter, and `is_weekend`.
2. Derive **elapsed** features relative to a fixed reference — for example
   `days_since_hire`. These are usually stronger than the raw date.
3. Cyclical encoding (`sin`/`cos` of month and day-of-week) helps linear models;
   it is unnecessary for gradient-boosted trees.
4. Never emit the raw datetime as a model feature. Either drop it or convert to
   a numeric offset.

## Leakage warning

Feature values must use only information available at each row's prediction
time. A date after the prediction moment is unavailable regardless of which
validation strategy is used. Historical aggregates must be windowed per row,
not computed over the full extract.

This is the single most common way a temporal project produces an excellent
validation score and a worthless model.
