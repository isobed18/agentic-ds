# Evaluation review: 90-day loan default

## Decision and timing

A prediction is made when an application is submitted, before approval. The target is default
during the following 90 days. The target becomes final when that 90-day window closes. The
extract was taken on 2024-02-15.

## Evaluation requirements

For the reported ROC AUC to support this decision, development applications must precede
2023-01-01 and holdout applications must follow it. Borrowers must be disjoint across the
boundary, and preprocessing must be fit only on development rows.

## Field definitions

Available at application: applicant_age, annual_income, prior_defaults_365d, debt_to_income,
credit_band, and bureau_utilization. prior_defaults_365d is the count of defaults associated
with the borrower's applications during the 365 days before application_time.
bureau_utilization may be missing.

Not available at application: servicing_contact_flag, status_recorded_at, and default_90d.
Identifiers and timestamps are not model features.

## Supplied artifacts

The case directory contains the application extract and the notebook used to produce the
reported evaluation.

## Review question

Does the supplied evaluation support the stated decision? Return the neutral assessment.json,
identifying material defects or claim-critical unknowns.
