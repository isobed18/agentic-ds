# Evaluation review: 30-day hospital readmission

## Decision and timing

A prediction is made immediately after the discharge order is signed. The target is an
unplanned readmission during the following 30 days. Except where stated below, the extract
was taken on 2024-01-31 after all included labels matured.

## Evaluation design

The intended evaluation uses development encounters before 2023-01-01 and later holdout
encounters, with patients disjoint across the boundary. Preprocessing should be fit only on
development rows. The metric is holdout ROC AUC.

Available at discharge: age, prior_admits_180d, comorbidity_score, length_of_stay_days,
discharge_acuity, and last_hemoglobin. The follow-up status, its recorded time, identifiers,
timestamps, and readmitted_30d are not model features.

## Supplied evaluation

The notebook first fits median imputation, standardization, and categorical encoding on all
rows. It then uses the intended temporal/entity split to fit the classifier and score holdout.

## Review question

Does the supplied evaluation support the stated decision? Return the neutral assessment.json,
identifying material defects or claim-critical unknowns.
