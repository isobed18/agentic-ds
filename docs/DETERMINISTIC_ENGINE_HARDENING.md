# Deterministic engine hardening

## PII sensitivity decision

The intake classifier now combines exact, normalized column-name phrases with
bounded value-shape checks. It emits row-free `SensitivityEvidence` entries:
stable codes identify the detector, while value detectors include match count,
sample count, and match rate. The controls still consume only the final
`Sensitivity` value; evidence is explanatory and cannot weaken redaction.

Calibration uses `tests/fixtures/pii_sensitivity_cases.json`: twelve realistic
PII columns (English and Turkish) and twelve deliberately difficult non-PII
columns. It contains no production records.

| classifier | PII caught | PII missed | safe columns flagged | safe columns kept |
|---|---:|---:|---:|---:|
| previous substring + long-digit rule | 8/12 | 4/12 | 11/12 | 1/12 |
| normalized phrases + validated shapes | 12/12 | 0/12 | 0/12 | 12/12 |

This is a regression calibration, not a population false-positive estimate.
Before deployment as a general privacy guarantee it needs labelled columns from
multiple real organizations and languages. Unknown free text remains the hardest
case: a column can contain occasional PII without having a PII-shaped name or a
dominant validated value format.

### Why no trained detector was adopted

- [Microsoft Presidio](https://github.com/microsoft/presidio) is the strongest
  general framework reviewed. Its structured module scans cells, but Turkish
  entity coverage still requires configuring a local spaCy, Stanza, or
  Transformers recognizer. It solves span detection in prose rather than the
  whole-column sensitivity decision and would not remove the need for this
  calibrated decision layer.
- [Capital One DataProfiler](https://github.com/capitalone/DataProfiler) ships a
  trained structured-data labeler, but sensitive labelling is in its TensorFlow
  installation and its published entity set is predominantly English/US. The
  slim installation explicitly disables that labeler.
- [GLiNER](https://github.com/urchade/GLiNER) and Turkish Hugging Face PII models
  can run locally and are promising optional free-text detectors. They add model
  weights and a transformer/ONNX runtime, and none found had a documented
  column-level Turkish calibration with hard negatives such as counts, scores,
  identifiers, and timestamps.

The result is to keep the deterministic classifier as the mandatory floor and
leave a local model as a future additive detector. An additive model may promote
`internal` to `pii`; it must never demote a deterministic PII result. Its model
weights, version, import behavior, and calibration corpus must be pinned before
adoption.

## Next weakest engines

A five-case adversarial probe found the next two weaknesses in
`intake/profiler.py`:

1. **Datetime inference can be confidently wrong.** `31.01.2024` and
   `01.02.2024` qualified as datetimes, but the second value parsed as 2 January
   rather than 1 February. Excel serial dates and epoch milliseconds were both
   classified categorical. Locale and numeric-date strategy therefore need to
   be explicit inputs; an ambiguous format must not silently choose US ordering.
2. **Semantic typing has no evidence or uncertainty state.** Turkish
   `evet/hayir` was categorical, and sixty unique numeric values named
   `urun_kodu` were classified continuous. The current identifier-name vocabulary
   is English and storage type can override business meaning.

Leakage detection has a source calibration table and multiple structural
families; repeated-entity validation now measures containment; integration
verifies realized grain. Those engines still have limits, but the measured
intake errors above are more immediate because they distort every downstream
stage before an agent can investigate.
