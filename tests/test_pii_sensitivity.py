import json
from pathlib import Path

import pandas as pd

from ads.contracts import Sensitivity, SensitivityEvidenceCode
from ads.intake.profiler import ProfileOptions, assess_sensitivity, profile_column

CASES = json.loads((Path(__file__).parent / "fixtures" / "pii_sensitivity_cases.json").read_text())


def test_multilingual_pii_calibration_has_no_fixture_errors() -> None:
    """Counter-fixture for both privacy misses and feature-dropping false alarms.

    Previous classifier on this exact fixture: TP=8, FN=4, FP=11, TN=1.
    """
    outcomes = []
    for case in CASES:
        sensitivity = assess_sensitivity(case["name"], pd.Series(case["values"])).sensitivity
        outcomes.append((case["expected"], sensitivity.value))

    assert outcomes.count(("pii", "pii")) == 12
    assert outcomes.count(("pii", "internal")) == 0
    assert outcomes.count(("internal", "pii")) == 0
    assert outcomes.count(("internal", "internal")) == 12


def test_sensitivity_evidence_is_row_free_coded_and_measured() -> None:
    email = profile_column(
        "contact",
        pd.Series([f"person{index}@example.test" for index in range(10)]),
        10,
        ProfileOptions(),
    )
    assert email.sensitivity is Sensitivity.PII
    assert email.sample_values == []
    assert email.sensitivity_evidence[0].code is SensitivityEvidenceCode.EMAIL_SHAPE
    assert email.sensitivity_evidence[0].match_count == 10
    assert email.sensitivity_evidence[0].measured_count == 10
    assert email.sensitivity_evidence[0].match_rate == 1.0


def test_aggregate_name_does_not_override_valid_value_shape() -> None:
    column = profile_column(
        "email_count",
        pd.Series([f"person{index}@example.test" for index in range(10)]),
        10,
        ProfileOptions(),
    )
    assert column.sensitivity is Sensitivity.PII
    assert [item.code for item in column.sensitivity_evidence] == [
        SensitivityEvidenceCode.EMAIL_SHAPE
    ]


def test_checksum_shape_detects_an_opaque_turkish_identifier() -> None:
    def valid_tckn(prefix: str) -> str:
        digits = [int(character) for character in prefix]
        digits.append(((sum(digits[0:9:2]) * 7) - sum(digits[1:8:2])) % 10)
        digits.append(sum(digits[:10]) % 10)
        return "".join(str(digit) for digit in digits)

    values = [valid_tckn(prefix) for prefix in ("100000001", "100000002", "100000003")]
    assessment = assess_sensitivity("opaque_reference", pd.Series(values))
    assert assessment.sensitivity is Sensitivity.PII
    assert assessment.evidence[0].code is SensitivityEvidenceCode.TURKISH_ID_CHECKSUM
    assert assessment.evidence[0].match_rate == 1.0
