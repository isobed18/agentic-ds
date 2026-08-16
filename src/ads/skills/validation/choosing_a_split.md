---
skill_id: validation.choosing_a_split
trigger: always (stage 4)
applies_to: [validation_strategy]
source: original
---

# Choosing a validation strategy

This decision determines whether every later number is real. It is a CRITICAL
risk stage and gates to a human by default.

## Decision rules, in precedence order

1. **Entity repeats AND time spans multiple periods** → `grouped_temporal`.
   Group on the entity key, hold out the most recent period.
2. **Entity repeats across rows** (e.g. many transactions per physician) →
   `grouped`. A random split puts the same physician in both train and test, so
   the model memorises the entity rather than learning the pattern.
3. **Time-ordered data, one row per entity** → `temporal`. Hold out the most
   recent slice; never sample randomly across time.
4. **Imbalanced classification, no grouping or ordering** → `stratified`.
5. **Otherwise** → `random`.

## Signals to read from the profile

- Repeated entity: a column with `semantic_type == identifier` whose
  `unique_rate` is well below 1.0 on the ABT.
- Time ordering: any `datetime` column with `span_days` covering multiple
  reporting periods.
- Imbalance: `minority_class_rate` below 0.2.

## What to tell the human

Present the trade-off in business terms, not ML terms. A domain expert can
choose between *"split at 2023-06 (realistic, 18% less training data)"* and
*"random split (optimistic, likely overstates performance)"*. They cannot
usefully answer *"which validation strategy should I use?"*.
