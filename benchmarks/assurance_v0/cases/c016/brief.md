# Evaluation review: 90-day loan default

## Decision and timing

A prediction is made when an application is submitted, before approval. The target is default
during the following 90 days. Except where stated below, the extract was taken on 2024-02-15
after all included labels matured.

## Evaluation design

The intended evaluation uses development applications before 2023-01-01 and later holdout
applications, with borrowers disjoint across the boundary. Preprocessing should be fit only
on development rows. The metric is holdout ROC AUC.

Available at application: applicant_age, annual_income, prior_defaults_365d, debt_to_income,
credit_band, and bureau_utilization. Servicing contact, status_recorded_at, identifiers,
timestamps, and default_90d are not model features.

## Supplied evaluation

The notebook first fits median imputation, standardization, and categorical encoding on all
rows. It then uses the intended temporal/entity split to fit the classifier and score holdout.

## Review question

Does the supplied evaluation support the stated decision? Return the neutral assessment.json,
identifying material defects or claim-critical unknowns.
