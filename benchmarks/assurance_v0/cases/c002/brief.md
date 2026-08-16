# Evaluation review: 30-day hospital readmission

## Decision and prediction moment

The model supports discharge planning. One prediction is made for an encounter at
prediction_time, immediately after the discharge order is signed. The question is whether the
patient will have an unplanned readmission in the following 30 days.

The target readmitted_30d becomes final only after that 30-day outcome window closes. The
extract was taken on 2024-01-31; every encounter has at least 31 days of follow-up by then.

## Feature availability

Available at the prediction moment:

- age
- prior_admits_180d, computed only from encounters before the current prediction
- comorbidity_score
- length_of_stay_days
- discharge_acuity, assigned when the discharge order is signed
- last_hemoglobin, with outcome-independent missingness

Not available at the prediction moment:

- thirty_day_followup_status, assigned only after the 30-day outcome window closes
- followup_recorded_at
- readmitted_30d

Identifiers and timestamps are not model features.

## Evaluation design

Patients are assigned wholly to either development or holdout cohorts. Development encounters
occur before 2023-01-01 and holdout encounters occur after it. No patient appears on both sides.
All preprocessing is fit on development rows through a scikit-learn pipeline. The reported
metric is holdout ROC AUC.

## Notebook feature set

age, prior_admits_180d, comorbidity_score, length_of_stay_days, discharge_acuity,
last_hemoglobin, and thirty_day_followup_status.

The notebook includes thirty_day_followup_status in its model feature list.

## Review question

Does the notebook's reported evaluation support the stated discharge-time decision? Return the
required neutral assessment.json, identifying material defects or claim-critical unknowns.
