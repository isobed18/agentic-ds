# Evaluation review: 30-day hospital readmission

## Decision and timing

A prediction is made immediately after the discharge order is signed. The target is an
unplanned readmission during the following 30 days. The target becomes final when that
30-day window closes. The extract was taken on 2024-01-31.

## Evaluation requirements

For the reported ROC AUC to support this decision, development encounters must precede
2023-01-01 and holdout encounters must follow it. Patients must be disjoint across the
boundary, and preprocessing must be fit only on development rows.

## Field definitions

Available at discharge: age, prior_admits_180d, comorbidity_score, length_of_stay_days,
discharge_acuity, and last_hemoglobin. prior_admits_180d is the count of admissions for the
patient during the 180 days before prediction_time. last_hemoglobin may be missing.

Not available at discharge: thirty_day_followup_status, followup_recorded_at, and
readmitted_30d. Identifiers and timestamps are not model features.

## Supplied artifacts

The case directory contains the encounter extract and the notebook used to produce the
reported evaluation.

## Review question

Does the supplied evaluation support the stated decision? Return the neutral assessment.json,
identifying material defects or claim-critical unknowns.
