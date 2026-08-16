"""Deterministic leakage audit — the blocking gate before any model is trained.

Target leakage is the single most expensive failure mode in enterprise ML: the
model scores beautifully, ships, and is worthless. It is also entirely
detectable by measurement, so no LLM is involved here. The agent cannot argue
with a correlation coefficient.

Five families of leakage are detected, because each hides from the others' test:

1. **Target-derived features** — a column that is a transformation of the target.
   Caught by correlation (regression) or adjusted mutual information.
   `total_comp_ytd` at 0.9948 correlation with `annual_comp` is the planted case.
2. **Perfect separators** — an ordered feature that nearly determines a binary
   or multiclass target. Measured by directional, chance-adjusted information on
   support-capped quantile bins. Ordinary symmetric AMI under-reports rare binary
   separators, while binary ROC AUC cannot represent multiclass intervals.
3. **Missingness separators** — presence or absence can reveal the target even
   when the observed values do not.
4. **Unwindowed aggregates under a temporal split** — a feature aggregated over
   the *full* history is computed partly from the holdout period. Invisible to
   every statistical test above, and caught structurally by reading the
   IntegrationPlan that produced the column.
5. **Identifier proxies** — a near-unique feature lets a model memorise rows
   rather than learn a pattern.

Findings feed `QualitySignals.max_target_correlation` and
`leakage_suspect_columns`, which `ads.gates` already consumes as a HARD rule
that fires in every autonomy profile including `full_auto`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_mutual_info_score

from ads.contracts.datacard import DataCard, SemanticType
from ads.contracts.integration import IntegrationPlanProposal
from ads.contracts.leakage import LeakageFinding, LeakageKind, LeakageReport
from ads.contracts.problem import SUPERVISED_TASKS, TaskType
from ads.contracts.validation import SplitStrategy, ValidationStrategy
from ads.discovery.support import usable_feature_columns

#: Correlation with the target above which a feature is treated as leakage.
#: Matches `GateThresholds.leakage_correlation`; the gate is the enforcement
#: point, this is the detection point.
LEAKAGE_CORRELATION = 0.95
#: Adjusted mutual information above which a feature is suspect.
#:
#: Calibrated against explicit controls rather than carried over from the NMI
#: era, per Codex FINDING 5 — AMI is systematically lower than NMI, so reusing
#: 0.90 silently loosened policy. Measured on 1,000-row corrupted copies of the
#: target and on legitimate features from the real sample data:
#:
#:   corrupted copy, 100%  agreement : 1.00
#:   corrupted copy, 97.5% agreement : 0.93 - 0.96
#:   corrupted copy, 95%   agreement : 0.86 - 0.93
#:   corrupted copy, 90%   agreement : 0.76 - 0.86
#:   years_experience -> annual_comp : 0.32   <- strongest legitimate feature
#:   specialty / city / hire_date    : < 0.02
#:
#: 0.70 catches any copy agreeing on 90%+ of rows while leaving 2.2x margin above
#: the strongest legitimate relationship measured.
LEAKAGE_MUTUAL_INFORMATION = 0.70
#: Uniqueness above which a feature is an identifier proxy rather than a signal.
IDENTIFIER_PROXY_UNIQUE_RATE = 0.98
#: Minimum rows required in every observed quantile bin. This is the guard that
#: prevents a unique numeric id or near-unique timestamp from becoming one bin
#: per row and scoring almost 1 under ``average_method="min"``.
SEPARATION_MIN_BIN_SUPPORT = 20
#: Absolute ceiling on adaptive quantile resolution. The support ceiling usually
#: binds first; 256 also bounds runtime and variance on very large, ultra-rare
#: targets while still resolving prevalence down to about 0.4% when supported.
SEPARATION_MAX_BINS = 256
#: Directional adjusted mutual information above which an ordered feature is a
#: separator suspect.
#:
#: Calibrated with ``SEPARATION_MIN_BIN_SUPPORT=20`` and
#: ``SEPARATION_MAX_BINS=256``. Scores below are deterministic ranges across the
#: checked class counts/sample sizes; ordered copies use contiguous class regions
#: and fixed-seed random label corruption:
#:
#:   control                                           directional AMI
#:   contiguous copy, 100% agreement, 3/10/30/50 cls   0.942 - 1.000
#:   contiguous copy, 97.5% agreement                  0.855 - 0.964
#:   contiguous copy, 95% agreement                    0.802 - 0.929
#:   contiguous copy, 90% agreement                    0.705 - 0.859
#:   inverted contiguous copies                        0.942 - 1.000
#:   perfect binary, 1% / 5% / balanced                1.000 / 1.000 / 1.000
#:   permuted targets, n=200/600/1,000/5,000/15,000    0.000 - 0.008
#:   unique numeric ids / near-unique datetimes         0.000 - 0.002
#:   sample ordered features -> rare ``flagged``        0.000 - 0.006
#:   sample ordered features -> ``senior_physician``    0.000 - 0.677
#:   years_experience -> senior_physician cutoff        1.000 (non-blocking)
#:
#: 0.70 catches every 90%+ ordered copy in the control matrix. The exact
#: pre-outcome cutoff demonstrates why this detector creates a provenance suspect
#: rather than a blocking leakage verdict.
LEAKAGE_SEPARATION_INFORMATION = 0.70


@dataclass(frozen=True)
class LeakageOptions:
    correlation_threshold: float = LEAKAGE_CORRELATION
    mutual_information_threshold: float = LEAKAGE_MUTUAL_INFORMATION
    identifier_unique_rate: float = IDENTIFIER_PROXY_UNIQUE_RATE
    separation_information_threshold: float = LEAKAGE_SEPARATION_INFORMATION
    separation_min_bin_support: int = SEPARATION_MIN_BIN_SUPPORT
    separation_max_bins: int = SEPARATION_MAX_BINS
    max_bins: int = 20
    """Bins used to discretise continuous columns for mutual information."""


def _discretise(series: pd.Series, max_bins: int) -> pd.Series:
    """Bin a continuous series so mutual information is computable."""
    if series.nunique() <= max_bins:
        return series.astype(str)
    try:
        return pd.qcut(series, q=max_bins, duplicates="drop").astype(str)
    except (ValueError, TypeError):
        return series.astype(str)


def _prepare_for_mi(series: pd.Series, max_bins: int) -> pd.Series:
    """Bin ordered columns; pass categoricals through unchanged.

    Only *ordered* values need binning — as raw distinct floats or timestamps
    they have no categorical structure to compare. Categoricals keep their full
    cardinality: chance adjustment in :func:`normalised_mutual_information`
    handles the near-unique artifact, so discarding levels here would only lose
    real signal.
    """
    if pd.api.types.is_numeric_dtype(series):
        return _discretise(series, max_bins)

    if pd.api.types.is_datetime64_any_dtype(series):
        return _discretise(series.astype("int64"), max_bins)

    parsed = _try_parse_datetime_column(series)
    if parsed is not None:
        return _discretise(parsed.astype("int64"), max_bins)

    return series.astype(str)


def _try_parse_datetime_column(series: pd.Series) -> pd.Series | None:
    """Parse an object column as datetimes, preserving index and NaT.

    Must NOT drop unparseable rows. Returning a shortened series desynchronised
    it from the target: mutual information then raised on mismatched lengths and
    the separation check silently returned ``None``, disabling itself. A single
    invalid date string was enough to switch off leakage detection for the whole
    column (Codex FINDING 7). Alignment with the target is the caller's job.
    """
    as_str = series.astype(str)
    if as_str.str.fullmatch(r"\d+").mean() > 0.8:
        return None
    try:
        parsed = pd.to_datetime(as_str, errors="coerce", format="mixed")
    except (ValueError, TypeError):
        return None
    return parsed if parsed.notna().mean() > 0.9 else None


def normalised_mutual_information(
    feature: pd.Series, target: pd.Series, max_bins: int = 20
) -> float:
    """Chance-adjusted mutual information in [0, 1].

    1.0 means the feature determines the target exactly — which for a *feature*
    means it is the target wearing a different name.

    Uses **adjusted** MI rather than plain normalised MI. Plain NMI is 1.0 for
    any near-unique column by construction: with one row per distinct value each
    value maps to exactly one target value. That artifact once flagged
    `hire_date`, a legitimate tenure feature, as leakage.

    An earlier fix guarded against it by refusing to score high-cardinality
    categoricals at all. Codex found the resulting false negative: a 30-class
    target copied exactly into a feature (20 rows per class, only 5% unique)
    scored 0.0 — perfect leakage, invisible. The guard was on cardinality when
    the real artifact is low *support per level*.

    AMI corrects for the agreement expected by chance, which handles both
    without a guard. Measured on those cases: exact copy 1.00, unique-per-row
    -0.00, near-unique dates -0.00, partial signal 0.33.
    """
    frame = pd.DataFrame({"f": feature, "t": target}).dropna()
    if frame.empty or frame["t"].nunique() < 2 or frame["f"].nunique() < 2:
        return 0.0

    # Continuous and datetime columns are still binned: as raw distinct values
    # they carry no categorical relationship to measure. Categoricals pass
    # through at full cardinality, because AMI no longer needs protecting.
    f = _prepare_for_mi(frame["f"], max_bins)
    t = _prepare_for_mi(frame["t"], max_bins)
    if len(f) != len(t):  # defensive: a preparer must never change length
        return 0.0

    score = adjusted_mutual_info_score(t.to_numpy(), f.to_numpy())
    return float(np.clip(score, 0.0, 1.0))


def _correlation(feature: pd.Series, target: pd.Series) -> float | None:
    """Max absolute Pearson/Spearman correlation, or None if not computable.

    Spearman is included because a monotone-but-nonlinear derivation (a ratio, a
    rank, a bucketed version of the target) leaks just as badly and can hide
    from Pearson.
    """
    frame = pd.DataFrame({"f": feature, "t": target}).dropna()
    if len(frame) < 3 or frame["f"].nunique() < 2 or frame["t"].nunique() < 2:
        return None

    scores: list[float] = []
    pearson = frame["f"].corr(frame["t"])
    if pd.notna(pearson):
        scores.append(abs(float(pearson)))

    # Spearman is Pearson over ranks. Computing it directly avoids a scipy
    # dependency, which matters for the air-gapped install footprint.
    ranked = frame.rank()
    spearman = ranked["f"].corr(ranked["t"])
    if pd.notna(spearman):
        scores.append(abs(float(spearman)))

    return max(scores) if scores else None


def correlation_strength(feature: pd.Series, target: pd.Series) -> float | None:
    """Public deterministic Pearson/Spearman strength used by evidence tools.

    Keeping the implementation here ensures the leakage audit and agent tool
    agree on what a reported correlation means.
    """
    return _correlation(feature, target)


def _feature_columns(
    card: DataCard, target_column: str | None, excluded: frozenset[str]
) -> list[str]:
    """Audit exactly the columns that could actually become features.

    Delegates to :func:`ads.discovery.support.usable_feature_columns` rather than
    filtering separately. Auditing identifiers produced a wall of noise — every
    PII and key column was reported twice, once as an identifier proxy and once
    at a meaningless mutual information of 1.0 — burying the findings that
    mattered.
    """
    return [c.name for c in usable_feature_columns(card, target_column, excluded)]


def audit_leakage(
    card: DataCard,
    frame: pd.DataFrame,
    *,
    target_column: str | None,
    task_type: TaskType,
    validation_strategy: ValidationStrategy | None = None,
    integration_plan: IntegrationPlanProposal | None = None,
    excluded_columns: frozenset[str] = frozenset(),
    confirmed_pre_outcome: frozenset[str] = frozenset(),
    confirmed_missingness_pre_outcome: frozenset[str] = frozenset(),
    options: LeakageOptions | None = None,
) -> LeakageReport:
    """Audit every candidate feature for leakage against the target.

    ``integration_plan`` is optional but strongly recommended: it is the only way
    to detect unwindowed aggregates, which correlation analysis cannot see.
    """
    options = options or LeakageOptions()
    features = _feature_columns(card, target_column, excluded_columns)
    findings: list[LeakageFinding] = []

    findings.extend(_audit_identifier_proxies(card, features, options))

    if target_column and task_type in SUPERVISED_TASKS and target_column in frame.columns:
        findings.extend(
            _audit_target_relationship(
                card, frame, features, target_column, task_type, options
            )
        )
        if task_type is not TaskType.REGRESSION:
            findings.extend(
                _audit_separation(card, frame, features, target_column, options)
            )
            findings.extend(
                _audit_missingness(frame, features, target_column, options)
            )

    findings.extend(
        _audit_temporal(card, frame, features, validation_strategy, integration_plan)
    )

    # Two distinct confirmations, because they are two distinct domain claims.
    # "This value is recorded before the outcome" does not establish "whether
    # this value exists at all is decided before the outcome": a post-outcome
    # follow-up workflow can populate a legitimately pre-outcome field only for
    # cases that had the outcome. Clearing one with the other was a safety
    # bypass (Codex FINDING 11).
    if confirmed_pre_outcome:
        findings = [
            f
            for f in findings
            if not (
                f.column in confirmed_pre_outcome
                and f.kind is LeakageKind.PERFECT_SEPARATOR
            )
        ]
    if confirmed_missingness_pre_outcome:
        findings = [
            f
            for f in findings
            if not (
                f.column in confirmed_missingness_pre_outcome
                and f.kind is LeakageKind.MISSINGNESS_SEPARATOR
            )
        ]

    # Stable, most-severe-first ordering so reports diff cleanly between runs.
    findings.sort(key=lambda f: (not f.blocking, -f.score, f.column))

    return LeakageReport(
        target_column=target_column,
        n_features_checked=len(features),
        findings=findings,
        split_strategy=validation_strategy.strategy if validation_strategy else None,
    )


def _audit_target_relationship(
    card: DataCard,
    frame: pd.DataFrame,
    features: list[str],
    target_column: str,
    task_type: TaskType,
    options: LeakageOptions,
) -> list[LeakageFinding]:
    target = frame[target_column]
    is_regression = task_type is TaskType.REGRESSION
    findings: list[LeakageFinding] = []

    for name in features:
        if name not in frame.columns:
            continue
        series = frame[name]
        profile = card.column(name)
        numeric_pair = pd.api.types.is_numeric_dtype(series) and pd.api.types.is_numeric_dtype(
            target
        )

        if is_regression and numeric_pair:
            score = _correlation(series, target)
            if score is not None and score >= options.correlation_threshold:
                findings.append(
                    LeakageFinding(
                        column=name,
                        kind=LeakageKind.TARGET_CORRELATION,
                        score=round(score, 6),
                        threshold=options.correlation_threshold,
                        blocking=True,
                        detail=(
                            f"{name!r} correlates with target {target_column!r} at "
                            f"{score:.4f}. A feature this close to the target is almost "
                            "always a transformation of it, not a predictor."
                        ),
                        suggested_action=f"Drop {name!r} from the feature set.",
                    )
                )
                continue

        # Mutual information catches categorical and nonlinear determination that
        # correlation misses entirely.
        if profile is not None and profile.semantic_type is not SemanticType.TEXT:
            score = normalised_mutual_information(series, target, options.max_bins)
            if score >= options.mutual_information_threshold:
                findings.append(
                    LeakageFinding(
                        column=name,
                        kind=LeakageKind.TARGET_MUTUAL_INFORMATION,
                        score=round(score, 6),
                        threshold=options.mutual_information_threshold,
                        blocking=True,
                        detail=(
                            f"{name!r} determines target {target_column!r} at normalised "
                            f"mutual information {score:.4f}. Knowing this feature is "
                            "nearly equivalent to knowing the answer."
                        ),
                        suggested_action=f"Drop {name!r} from the feature set.",
                    )
                )

    return findings


def ordered_separation_information(
    feature: pd.Series,
    target: pd.Series,
    *,
    min_bin_support: int = SEPARATION_MIN_BIN_SUPPORT,
    max_bins: int = SEPARATION_MAX_BINS,
) -> float | None:
    """How completely a binned ordered feature determines a class target.

    Quantile resolution adapts to target complexity and rarity as
    ``max(20, 2 * n_classes, ceil(1 / minority_rate))``. It is capped both by
    ``min_bin_support`` and by ``max_bins``. Every feature is binned, including
    unique numeric ids and timestamps: raw unique values score approximately
    1.0 with ``average_method="min"`` because each singleton trivially identifies
    its row's target.

    ``average_method="min"`` normalises by target entropy when the feature
    partition is finer. The result therefore asks the directional question
    needed here — whether the feature determines the target — while adjusted MI
    subtracts agreement expected by chance. Returns ``None`` when the feature is
    unorderable or the data cannot form two supported bins/classes.
    """
    if min_bin_support < 2:
        raise ValueError("min_bin_support must be at least 2.")
    if max_bins < 2:
        raise ValueError("max_bins must be at least 2.")

    ordered_all = _as_ordered(feature)
    if ordered_all is None:
        return None

    # Align after coercion, so unparseable values drop jointly with their target
    # rows rather than desynchronising the two series.
    frame = pd.DataFrame({"f": ordered_all, "t": target})
    frame["f"] = frame["f"].replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna()
    if len(frame) < 2 * min_bin_support or frame["f"].nunique() < 2:
        return None

    target_labels = frame["t"].astype(str)
    class_counts = target_labels.value_counts()
    if len(class_counts) < 2:
        return None

    minority_rate = float(class_counts.min() / len(frame))
    requested_bins = max(
        20,
        2 * len(class_counts),
        math.ceil(1.0 / minority_rate),
    )
    bin_ceiling = min(
        requested_bins,
        len(frame) // min_bin_support,
        max_bins,
        int(frame["f"].nunique()),
    )
    if bin_ceiling < 2:
        return None

    # Ties can make qcut bins uneven, so the arithmetic support cap alone is not
    # sufficient. Lower q until every observed bin meets the support floor.
    binned: pd.Series | None = None
    candidate_bins = bin_ceiling
    while candidate_bins >= 2:
        try:
            candidate = pd.qcut(
                frame["f"], q=candidate_bins, duplicates="drop"
            )
        except (TypeError, ValueError):
            return None
        counts = candidate.value_counts()
        observed_counts = counts[counts > 0]
        if len(observed_counts) >= 2 and int(observed_counts.min()) >= min_bin_support:
            binned = candidate
            break
        reduced = int(candidate.nunique()) - 1
        candidate_bins = min(candidate_bins - 1, reduced)

    if binned is None:
        return None
    score = adjusted_mutual_info_score(
        target_labels.to_numpy(),
        binned.astype(str).to_numpy(),
        average_method="min",
    )
    return float(np.clip(score, 0.0, 1.0))


def _as_ordered(series: pd.Series) -> pd.Series | None:
    """Coerce a column to a rankable float series, or None if unorderable.

    Unparseable and missing values become NaN rather than the int64 NaT sentinel
    (-9223372036854775808), which would otherwise rank as an extreme value and
    fabricate separation. The caller drops NaN jointly with the target.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    if pd.api.types.is_datetime64_any_dtype(series):
        return _datetime_to_float(series)
    parsed = _try_parse_datetime_column(series)
    if parsed is not None:
        return _datetime_to_float(parsed)
    return None


def _datetime_to_float(series: pd.Series) -> pd.Series:
    """Datetimes as floats, with NaT preserved as NaN instead of a huge int."""
    values = series
    if getattr(values.dtype, "tz", None) is not None:
        values = values.dt.tz_convert("UTC").dt.tz_localize(None)
    numeric = values.astype("int64").astype(float)
    return numeric.mask(values.isna())


def _audit_separation(
    card: DataCard,
    frame: pd.DataFrame,
    features: list[str],
    target_column: str,
    options: LeakageOptions,
) -> list[LeakageFinding]:
    """Directional check: does one ordered feature determine any class target?"""
    findings: list[LeakageFinding] = []
    target = frame[target_column]

    for name in features:
        if name not in frame.columns:
            continue
        score = ordered_separation_information(
            frame[name],
            target,
            min_bin_support=options.separation_min_bin_support,
            max_bins=options.separation_max_bins,
        )
        if score is None or score < options.separation_information_threshold:
            continue
        findings.append(
            LeakageFinding(
                column=name,
                kind=LeakageKind.PERFECT_SEPARATOR,
                score=round(score, 6),
                threshold=options.separation_information_threshold,
                # Not blocking: statistical strength cannot establish temporal
                # provenance. This escalates for human confirmation instead.
                blocking=False,
                detail=(
                    f"Binned {name!r} determines target {target_column!r} at directional "
                    f"adjusted mutual information {score:.4f}. A single ordered feature "
                    "that separates classes this cleanly may be recorded after the "
                    "outcome rather than available at prediction time."
                ),
                suggested_action=(
                    f"Confirm {name!r} is knowable before the outcome; otherwise drop it."
                ),
            )
        )
    return findings


def _audit_missingness(
    frame: pd.DataFrame,
    features: list[str],
    target_column: str,
    options: LeakageOptions,
) -> list[LeakageFinding]:
    """Score *whether a value exists* against the target, before dropping nulls.

    Codex FINDING 8: every other scorer drops feature-null rows first, so when
    presence itself is post-outcome information the audit erases the entire
    leaking signal — a column populated for positives and empty for negatives
    measured as "no observable relationship".

    Missingness survives into the model: the preprocessing path encodes absent
    categoricals as an explicit sentinel and imputed numerics form a distinct
    region, so a model genuinely can exploit it.
    """
    target = frame[target_column]
    findings: list[LeakageFinding] = []

    for name in features:
        if name not in frame.columns:
            continue
        indicator = frame[name].isna()
        null_rate = float(indicator.mean())
        # Only fully-present and fully-absent columns are structurally
        # uninformative. An earlier 1% floor excluded this project's own
        # motivating case: a column null for exactly the 39 fraud positives in
        # 15,000 rows is 99.74% populated and was skipped before being scored.
        # Rare indicators are governed by chance adjustment in the score itself,
        # which is what AMI is for -- not by a rate floor.
        if not 0.0 < null_rate < 1.0:
            continue

        score = normalised_mutual_information(
            indicator.astype(int), target, options.max_bins
        )
        if score < options.mutual_information_threshold:
            continue

        findings.append(
            LeakageFinding(
                column=name,
                kind=LeakageKind.MISSINGNESS_SEPARATOR,
                score=round(score, 6),
                threshold=options.mutual_information_threshold,
                blocking=True,
                detail=(
                    f"Whether {name!r} is populated at all predicts target "
                    f"{target_column!r} at {score:.4f} ({null_rate:.1%} null). The "
                    "presence of the value is itself post-outcome information; the "
                    "values do not need to be read for the model to exploit it."
                ),
                suggested_action=(
                    f"Drop {name!r}, or confirm the value is recorded before the "
                    "outcome is known."
                ),
            )
        )
    return findings


def _audit_identifier_proxies(
    card: DataCard, features: list[str], options: LeakageOptions
) -> list[LeakageFinding]:
    findings: list[LeakageFinding] = []
    for name in features:
        profile = card.column(name)
        if profile is None:
            continue
        if profile.semantic_type is SemanticType.IDENTIFIER or (
            profile.unique_rate >= options.identifier_unique_rate
            and profile.semantic_type
            in (SemanticType.CATEGORICAL, SemanticType.TEXT)
        ):
            findings.append(
                LeakageFinding(
                    column=name,
                    kind=LeakageKind.IDENTIFIER_PROXY,
                    score=round(profile.unique_rate, 6),
                    threshold=options.identifier_unique_rate,
                    blocking=True,
                    detail=(
                        f"{name!r} is near-unique ({profile.unique_rate:.1%} distinct). A "
                        "model can memorise rows through it instead of learning a pattern."
                    ),
                    suggested_action=f"Exclude {name!r} from features; keep it as a key only.",
                )
            )
    return findings


def _audit_temporal(
    card: DataCard,
    frame: pd.DataFrame,
    features: list[str],
    validation_strategy: ValidationStrategy | None,
    integration_plan: IntegrationPlanProposal | None,
) -> list[LeakageFinding]:
    """Detect leakage that correlation analysis structurally cannot see.

    Under a temporal split, a feature aggregated over the *entire* history
    contains information from the holdout period. Its correlation with the target
    may be modest, so only provenance reveals it — which is why the
    IntegrationPlan is threaded in here.
    """
    if validation_strategy is None:
        return []
    temporal = validation_strategy.strategy in (
        SplitStrategy.TEMPORAL,
        SplitStrategy.GROUPED_TEMPORAL,
    )
    if not temporal:
        return []

    findings: list[LeakageFinding] = []
    feature_set = set(features)

    if integration_plan is not None:
        for aggregation in integration_plan.aggregations:
            for column in aggregation.aggregations:
                if column not in feature_set:
                    continue
                findings.append(
                    LeakageFinding(
                        column=column,
                        kind=LeakageKind.UNWINDOWED_AGGREGATE,
                        score=1.0,
                        threshold=1.0,
                        blocking=True,
                        detail=(
                            f"{column!r} aggregates {aggregation.source_table!r} over its "
                            "full history, but the split is temporal. It therefore "
                            "includes data from the holdout period. Correlation analysis "
                            "cannot detect this; only provenance can."
                        ),
                        suggested_action=(
                            f"Recompute {column!r} windowed to before the holdout cutoff."
                        ),
                    )
                )

    cutoff = validation_strategy.holdout_cutoff
    if cutoff:
        boundary = pd.to_datetime(cutoff, errors="coerce")
        if pd.notna(boundary):
            for name in features:
                profile = card.column(name)
                if profile is None or profile.semantic_type is not SemanticType.DATETIME:
                    continue
                if name == validation_strategy.time_column or name not in frame.columns:
                    continue
                values = pd.to_datetime(frame[name], errors="coerce").dropna()
                if not values.empty and values.max() >= boundary:
                    findings.append(
                        LeakageFinding(
                            column=name,
                            kind=LeakageKind.POST_CUTOFF_DATETIME,
                            score=1.0,
                            threshold=1.0,
                            blocking=False,
                            detail=(
                                f"{name!r} contains dates at or after the holdout cutoff "
                                f"{cutoff}. Verify this is knowable at prediction time."
                            ),
                            suggested_action=(
                                f"Confirm {name!r} is available before the cutoff, or drop it."
                            ),
                        )
                    )
    return findings


def leakage_digest(report: LeakageReport) -> str:
    """Render the report for a human at the gate."""
    if report.is_clean and not report.findings:
        return (
            f"LEAKAGE AUDIT: clean. {report.n_features_checked} features checked against "
            f"target {report.target_column!r}."
        )

    lines = [
        f"LEAKAGE AUDIT: {len(report.blocking_findings)} blocking finding(s) across "
        f"{report.n_features_checked} features (target={report.target_column!r})."
    ]
    for finding in report.findings:
        mark = "BLOCK" if finding.blocking else "warn "
        lines.append(f"  [{mark}] {finding.column}  {finding.kind.value}={finding.score:.4f}")
        lines.append(f"          {finding.detail}")
    return "\n".join(lines)


__all__ = [
    "IDENTIFIER_PROXY_UNIQUE_RATE",
    "LEAKAGE_CORRELATION",
    "LEAKAGE_MUTUAL_INFORMATION",
    "LEAKAGE_SEPARATION_INFORMATION",
    "SEPARATION_MAX_BINS",
    "SEPARATION_MIN_BIN_SUPPORT",
    "LeakageOptions",
    "audit_leakage",
    "leakage_digest",
    "normalised_mutual_information",
    "ordered_separation_information",
]
