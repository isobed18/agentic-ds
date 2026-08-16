"""Deterministic measurement of whether a modelling decision is sound.

Nothing in this package calls an LLM. These are the measurements the Gate
Evaluator and the agent validators check model output against — the veto power
that keeps an agent from asserting a bad decision into existence.
"""

from ads.discovery.leakage import (
    IDENTIFIER_PROXY_UNIQUE_RATE,
    LEAKAGE_CORRELATION,
    LEAKAGE_MUTUAL_INFORMATION,
    LeakageOptions,
    audit_leakage,
    correlation_strength,
    leakage_digest,
    normalised_mutual_information,
)
from ads.discovery.measurements import (
    analysis_measurement_bundle,
    datacard_measurements,
    eda_measurements,
    integration_measurements,
    measurement_digest,
    source_measurement_bundle,
)
from ads.discovery.support import (
    IMBALANCE_WARN_RATE,
    MAX_TARGET_NULL_RATE,
    MIN_MINORITY_COUNT,
    MIN_ROWS,
    MIN_ROWS_PER_FEATURE,
    compute_support,
    support_digest,
    usable_feature_columns,
)
from ads.discovery.validation_signals import (
    detect_validation_signals,
    full_coverage_group_columns,
    recommend_strategy,
    validation_signals_digest,
)

__all__ = [
    "IDENTIFIER_PROXY_UNIQUE_RATE",
    "IMBALANCE_WARN_RATE",
    "LEAKAGE_CORRELATION",
    "LEAKAGE_MUTUAL_INFORMATION",
    "MAX_TARGET_NULL_RATE",
    "MIN_MINORITY_COUNT",
    "MIN_ROWS",
    "MIN_ROWS_PER_FEATURE",
    "LeakageOptions",
    "audit_leakage",
    "correlation_strength",
    "compute_support",
    "detect_validation_signals",
    "full_coverage_group_columns",
    "leakage_digest",
    "normalised_mutual_information",
    "recommend_strategy",
    "support_digest",
    "usable_feature_columns",
    "validation_signals_digest",
    "analysis_measurement_bundle",
    "datacard_measurements",
    "eda_measurements",
    "integration_measurements",
    "measurement_digest",
    "source_measurement_bundle",
]
