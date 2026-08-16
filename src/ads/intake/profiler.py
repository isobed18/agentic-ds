"""Deterministic DataCard profiler.

Produces the compact table description that agents actually see. Two properties
matter more than completeness:

* **Deterministic.** Same input always yields the same DataCard, so runs are
  reproducible and agents are unit-testable against fixtures.
* **Redaction-aware.** Sample values are the only place raw data can leak into
  LLM context, so columns classified as PII never contribute samples or
  top-values regardless of caller settings.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ads.contracts.datacard import (
    ColumnProfile,
    DataCard,
    DatetimeStats,
    NumericStats,
    SemanticType,
    Sensitivity,
    TextScript,
    TextStats,
    ValueCount,
)
from ads.intake.loaders import LoadedTable

# Column-name signals for sensitivity classification. Deliberately broad: a
# false PII positive costs a few sample values, a false negative leaks data.
_PII_NAME_PATTERNS = (
    "name", "surname", "firstname", "lastname", "fullname",
    "email", "mail", "phone", "mobile", "tel", "fax",
    "ssn", "sin", "nin", "tckn", "tc_kimlik", "national_id", "passport",
    "address", "street", "postcode", "zip", "city_of_birth",
    "dob", "birth", "iban", "account_no", "account_number", "card",
    "credit_card", "cvv", "license", "tax_id",
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
_LONG_DIGIT_RE = re.compile(r"^[0-9]{9,}$")
_URL_RE = re.compile(r"^(?:https?://|www[.])[^ ]+$", re.IGNORECASE)
_STRUCTURED_ID_RE = re.compile(
    r"^(?=.{4,}$)(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._:/-]*$"
)

_ID_NAME_PATTERNS = ("_id", "id_", "^id$", "_no$", "_key$", "code$", "_ref$", "uuid", "guid")
# Cardinality above which an id-shaped name is treated as a key even when the
# column repeats heavily — i.e. a foreign key rather than a primary key.
_ID_MIN_CARDINALITY = 50

_DEFAULT_MAX_PROFILE_ROWS = 200_000
_TOP_VALUES = 5
_SAMPLE_VALUES = 3
_MAX_CATEGORICAL_CARDINALITY = 50
_DATETIME_INFERENCE_MIN_RATE = 0.70
_DATETIME_SAMPLE_SIZE = 500


@dataclass(frozen=True)
class ProfileOptions:
    """Knobs for profiling. Defaults are the privacy-preserving choice."""

    max_profile_rows: int = _DEFAULT_MAX_PROFILE_ROWS
    include_samples: bool = True
    """Include sample values for non-PII columns. PII is redacted regardless."""
    max_categorical_cardinality: int = _MAX_CATEGORICAL_CARDINALITY
    random_state: int = 17


def _looks_like_identifier_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        re.search(pattern, lowered) if pattern.startswith("^") or pattern.endswith("$")
        else pattern in lowered
        for pattern in _ID_NAME_PATTERNS
    )


def classify_sensitivity(name: str, series: pd.Series) -> Sensitivity:
    """Classify a column's redaction class from its name and a value sample."""
    lowered = name.lower()
    if any(pattern in lowered for pattern in _PII_NAME_PATTERNS):
        return Sensitivity.PII

    non_null = series.dropna()
    if non_null.empty:
        return Sensitivity.INTERNAL

    sample = non_null.head(200).astype(str)
    if sample.str.match(_EMAIL_RE).mean() > 0.5:
        return Sensitivity.PII
    if sample.str.match(_LONG_DIGIT_RE).mean() > 0.8 and series.nunique() > len(sample) * 0.8:
        # Long, near-unique digit strings are account/ID numbers far more often
        # than they are measurements.
        return Sensitivity.PII
    return Sensitivity.INTERNAL


def infer_semantic_type(name: str, series: pd.Series, n_rows: int) -> SemanticType:
    """Infer what a column *means*, beyond its storage dtype."""
    non_null = series.dropna()
    if non_null.empty:
        return SemanticType.EMPTY

    n_unique = non_null.nunique()
    if n_unique == 1:
        return SemanticType.CONSTANT

    unique_rate = n_unique / max(len(non_null), 1)

    if pd.api.types.is_bool_dtype(series):
        return SemanticType.BOOLEAN
    if pd.api.types.is_datetime64_any_dtype(series):
        return SemanticType.DATETIME

    if n_unique == 2:
        values = {str(v).strip().lower() for v in non_null.unique()}
        if values <= {"0", "1", "true", "false", "yes", "no", "y", "n", "t", "f"}:
            return SemanticType.BOOLEAN

    # An id-shaped name identifies both primary keys (near-unique) and foreign
    # keys (highly repeated). Gating on uniqueness alone would misclassify every
    # FK as a measurement and hide it from relationship detection.
    id_by_name = _looks_like_identifier_name(name) and (
        unique_rate > 0.98 or n_unique >= _ID_MIN_CARDINALITY
    )

    if pd.api.types.is_numeric_dtype(series):
        is_integral = pd.api.types.is_integer_dtype(series) or (
            non_null.astype(float) % 1 == 0
        ).all()
        if is_integral and id_by_name:
            return SemanticType.IDENTIFIER
        if is_integral and n_unique <= _MAX_CATEGORICAL_CARDINALITY:
            return SemanticType.NUMERIC_DISCRETE
        return SemanticType.NUMERIC_CONTINUOUS

    # Object/string columns.
    as_str = non_null.astype(str)
    if _try_parse_datetime(as_str) is not None:
        return SemanticType.DATETIME
    if id_by_name:
        return SemanticType.IDENTIFIER
    if n_unique <= _MAX_CATEGORICAL_CARDINALITY:
        return SemanticType.CATEGORICAL
    # Unique narrative text is not an identifier merely because every review differs.
    # Unlabelled identifiers must also have a repeated machine-readable shape.
    if as_str.str.len().mean() > 40:
        return SemanticType.TEXT
    if unique_rate > 0.98 and n_unique == n_rows:
        identifier_shaped = (
            as_str.str.fullmatch(_LONG_DIGIT_RE).mean()
            + as_str.str.fullmatch(_STRUCTURED_ID_RE).mean()
        )
        if identifier_shaped > 0.8:
            return SemanticType.IDENTIFIER
    return SemanticType.CATEGORICAL if unique_rate < 0.5 else SemanticType.TEXT


def _try_parse_datetime(as_str: pd.Series) -> pd.Series | None:
    """Parse a mostly-datetime string series while retaining measurable dirty values.

    Qualification uses a bounded sample, but the returned series covers every value.
    The tolerant threshold preserves temporal columns containing explicit junk markers;
    DatetimeStats records the parse rate so that dirt remains visible.
    """
    if len(as_str) <= _DATETIME_SAMPLE_SIZE:
        sample = as_str
    else:
        positions = np.linspace(
            0, len(as_str) - 1, num=_DATETIME_SAMPLE_SIZE, dtype=int
        )
        sample = as_str.iloc[positions]
    if sample.empty or sample.str.fullmatch(r"[0-9]+").mean() > 0.8:
        return None
    try:
        parsed_sample = pd.to_datetime(
            sample,
            errors="coerce",
            format="mixed",
            utc=True,
        )
    except (ValueError, TypeError):
        return None
    if float(parsed_sample.notna().mean()) < _DATETIME_INFERENCE_MIN_RATE:
        return None
    return pd.to_datetime(as_str, errors="coerce", format="mixed", utc=True)


def _numeric_stats(series: pd.Series) -> NumericStats | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    values = values.astype(float)
    return NumericStats(
        min=float(values.min()),
        max=float(values.max()),
        mean=float(values.mean()),
        std=float(values.std(ddof=0)) if len(values) > 1 else 0.0,
        p25=float(values.quantile(0.25)),
        p50=float(values.quantile(0.50)),
        p75=float(values.quantile(0.75)),
        zero_rate=float((values == 0).mean()),
        negative_rate=float((values < 0).mean()),
    )


def _datetime_stats(series: pd.Series) -> DatetimeStats | None:
    non_null = series.dropna()
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed_all = pd.to_datetime(non_null, errors="coerce", utc=True)
    else:
        parsed_all = _try_parse_datetime(non_null.astype(str))
        if parsed_all is None:
            return None
    parsed = parsed_all.dropna()
    if parsed.empty:
        return None
    parsed_count = int(len(parsed))
    invalid_count = int(len(non_null) - parsed_count)
    lo, hi = parsed.min(), parsed.max()
    return DatetimeStats(
        min=str(lo),
        max=str(hi),
        span_days=int(max((hi - lo).days, 0)),
        parsed_count=parsed_count,
        invalid_count=invalid_count,
        parse_rate=round(parsed_count / max(len(non_null), 1), 6),
    )


_SCRIPT_PREFIXES = (
    ("LATIN", TextScript.LATIN),
    ("CYRILLIC", TextScript.CYRILLIC),
    ("GREEK", TextScript.GREEK),
    ("ARABIC", TextScript.ARABIC),
    ("HEBREW", TextScript.HEBREW),
    ("CJK", TextScript.HAN),
    ("IDEOGRAPH", TextScript.HAN),
    ("HIRAGANA", TextScript.HAN),
    ("KATAKANA", TextScript.HAN),
    ("HANGUL", TextScript.HANGUL),
    ("DEVANAGARI", TextScript.DEVANAGARI),
    ("THAI", TextScript.THAI),
)


def _text_script(value: str) -> TextScript:
    scripts: set[TextScript] = set()
    for character in value:
        if not character.isalpha():
            continue
        name = unicodedata.name(character, "")
        matched = next(
            (script for prefix, script in _SCRIPT_PREFIXES if prefix in name),
            TextScript.OTHER,
        )
        scripts.add(matched)
    if not scripts:
        return TextScript.NONE
    if len(scripts) > 1:
        return TextScript.MIXED
    return next(iter(scripts))


def _text_stats(series: pd.Series) -> TextStats | None:
    """Measure aggregate text shape without retaining any source value."""
    values = series.dropna().astype(str)
    if values.empty:
        return None
    lengths = values.str.len()
    token_counts = values.map(lambda value: len(value.split()))
    scripts = Counter(_text_script(value) for value in values)
    dominant_script, dominant_count = sorted(
        scripts.items(),
        key=lambda item: (-item[1], item[0].value),
    )[0]
    total_characters = int(lengths.sum())
    punctuation_characters = sum(
        1
        for value in values
        for character in value
        if unicodedata.category(character).startswith("P")
    )
    email_matches = values.str.fullmatch(_EMAIL_RE)
    url_matches = values.str.fullmatch(_URL_RE)
    identifier_matches = (
        values.str.fullmatch(_LONG_DIGIT_RE)
        | values.str.fullmatch(_STRUCTURED_ID_RE)
    ) & ~email_matches & ~url_matches
    return TextStats(
        measured_count=len(values),
        min_length=int(lengths.min()),
        max_length=int(lengths.max()),
        mean_length=round(float(lengths.mean()), 6),
        p50_length=round(float(lengths.quantile(0.50)), 6),
        p90_length=round(float(lengths.quantile(0.90)), 6),
        mean_token_count=round(float(token_counts.mean()), 6),
        p50_token_count=round(float(token_counts.quantile(0.50)), 6),
        p90_token_count=round(float(token_counts.quantile(0.90)), 6),
        blank_string_rate=round(float(values.map(lambda value: not value.strip()).mean()), 6),
        contains_whitespace_rate=round(
            float(values.map(lambda value: any(char.isspace() for char in value)).mean()),
            6,
        ),
        punctuation_character_rate=round(
            punctuation_characters / max(total_characters, 1),
            6,
        ),
        dominant_script=dominant_script,
        dominant_script_rate=round(dominant_count / len(values), 6),
        email_pattern_rate=round(float(email_matches.mean()), 6),
        url_pattern_rate=round(float(url_matches.mean()), 6),
        identifier_pattern_rate=round(float(identifier_matches.mean()), 6),
    )


def profile_column(
    name: str, series: pd.Series, n_rows: int, options: ProfileOptions
) -> ColumnProfile:
    """Build the deterministic profile of a single column."""
    null_count = int(series.isna().sum())
    non_null = series.dropna()
    n_unique = int(non_null.nunique())
    total = max(len(series), 1)

    sensitivity = classify_sensitivity(name, series)
    semantic_type = infer_semantic_type(name, series, n_rows)

    numeric = None
    datetime_stats = None
    text_stats = None
    if semantic_type in (SemanticType.NUMERIC_CONTINUOUS, SemanticType.NUMERIC_DISCRETE):
        numeric = _numeric_stats(series)
    elif semantic_type == SemanticType.DATETIME:
        datetime_stats = _datetime_stats(series)
    elif semantic_type == SemanticType.TEXT:
        text_stats = _text_stats(series)

    top_values: list[ValueCount] = []
    sample_values: list[str] = []
    redacted = sensitivity == Sensitivity.PII

    if not redacted and semantic_type in (
        SemanticType.CATEGORICAL,
        SemanticType.BOOLEAN,
        SemanticType.NUMERIC_DISCRETE,
        SemanticType.CONSTANT,
    ):
        counts = non_null.astype(str).value_counts().head(_TOP_VALUES)
        top_values = [ValueCount(value=str(v), count=int(c)) for v, c in counts.items()]

    if options.include_samples and not redacted and not non_null.empty:
        sample_values = [str(v) for v in non_null.head(_SAMPLE_VALUES).tolist()]

    notes: list[str] = []
    if redacted:
        notes.append("Values redacted: column classified as PII.")
    if null_count == len(series) and len(series) > 0:
        notes.append("Column is entirely null.")
    if semantic_type == SemanticType.CATEGORICAL and n_unique > options.max_categorical_cardinality:
        notes.append(f"High-cardinality categorical ({n_unique} levels).")
    if datetime_stats is not None and datetime_stats.invalid_count:
        notes.append(
            f"Datetime parsing retained {datetime_stats.parsed_count:,} of "
            f"{datetime_stats.parsed_count + datetime_stats.invalid_count:,} non-null values "
            f"({datetime_stats.parse_rate:.1%}); unparsed values remain in source."
        )

    return ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        semantic_type=semantic_type,
        sensitivity=sensitivity,
        null_count=null_count,
        null_rate=round(null_count / total, 6),
        n_unique=n_unique,
        unique_rate=round(n_unique / max(len(non_null), 1), 6) if len(non_null) else 0.0,
        is_unique=bool(n_unique == len(non_null) and null_count == 0 and n_unique > 0),
        numeric=numeric,
        datetime=datetime_stats,
        text=text_stats,
        top_values=top_values,
        sample_values=sample_values,
        notes=notes,
    )


def profile_table(table: LoadedTable, options: ProfileOptions | None = None) -> DataCard:
    """Build a DataCard for one loaded table."""
    options = options or ProfileOptions()
    frame = table.frame
    n_rows = int(len(frame))

    sampled = n_rows > options.max_profile_rows
    working = (
        frame.sample(options.max_profile_rows, random_state=options.random_state)
        if sampled
        else frame
    )

    issues = list(table.issues)
    if sampled:
        issues.append(
            _make_issue(
                "info",
                "profiled_on_sample",
                f"Profiled a {options.max_profile_rows:,}-row sample of {n_rows:,} rows. "
                "Uniqueness and key detection are approximate.",
            )
        )

    columns = [
        profile_column(str(col), working[col], n_rows, options) for col in working.columns
    ]

    # Single-column candidate keys. Composite keys are handled in ads.intake.keys.
    # Continuous measurements are excluded even when they happen to be unique:
    # a distinct float is a coincidence of the sample, not an identifier.
    candidate_keys = [
        [c.name]
        for c in columns
        if c.is_unique and c.n_unique > 1 and c.semantic_type is not SemanticType.NUMERIC_CONTINUOUS
    ]

    if not columns:
        issues.append(_make_issue("error", "no_columns", "Table has no usable columns."))
    if n_rows == 0:
        issues.append(_make_issue("error", "no_rows", "Table has no rows."))

    return DataCard(
        table_name=table.name,
        source_uri=table.source_uri,
        source_format=table.source_format,
        sheet_name=table.sheet_name,
        n_rows=n_rows,
        n_columns=len(columns),
        columns=columns,
        candidate_primary_keys=candidate_keys,
        issues=issues,
        profiled_rows=int(len(working)),
        sampled=sampled,
    )


def _make_issue(severity: str, code: str, detail: str):
    from ads.contracts.datacard import LoadIssue

    return LoadIssue(severity=severity, code=code, detail=detail)


def profile_tables(
    tables: list[LoadedTable], options: ProfileOptions | None = None
) -> list[DataCard]:
    return [profile_table(t, options) for t in tables]


def datacard_digest(card: DataCard) -> str:
    """Render a DataCard as compact text for LLM context.

    This is the actual projection an agent sees. Keeping it terse is what makes
    a 27B local model viable: a 40-column table costs roughly 600 tokens here
    versus tens of thousands for raw rows.
    """
    lines = [
        f"TABLE {card.table_name}  ({card.n_rows:,} rows x {card.n_columns} cols, "
        f"format={card.source_format}"
        + (f", sheet={card.sheet_name}" if card.sheet_name else "")
        + ")"
    ]
    if card.sampled:
        lines.append(f"  [profiled on {card.profiled_rows:,}-row sample]")

    for col in card.columns:
        parts = [f"  - {col.name}: {col.semantic_type.value}"]
        if col.null_rate > 0:
            parts.append(f"null={col.null_rate:.1%}")
        parts.append(f"uniq={col.n_unique:,}")
        if col.is_unique:
            parts.append("UNIQUE")
        if col.sensitivity == Sensitivity.PII:
            parts.append("PII/redacted")
        if col.numeric:
            parts.append(
                f"range=[{col.numeric.min:.4g}..{col.numeric.max:.4g}] "
                f"med={col.numeric.p50:.4g}"
            )
        if col.datetime:
            parts.append(f"span={col.datetime.min[:10]}..{col.datetime.max[:10]}")
            if col.datetime.parse_rate is not None and col.datetime.parse_rate < 1.0:
                parts.append(f"parsed={col.datetime.parse_rate:.1%}")
        if col.text:
            parts.append(
                f"text_len_p50={col.text.p50_length:.3g} "
                f"p90={col.text.p90_length:.3g} "
                f"tokens_p50={col.text.p50_token_count:.3g} "
                f"script={col.text.dominant_script.value}"
            )
            patterns = {
                "email": col.text.email_pattern_rate,
                "url": col.text.url_pattern_rate,
                "identifier": col.text.identifier_pattern_rate,
            }
            visible_patterns = [
                f"{name}={rate:.1%}" for name, rate in patterns.items() if rate > 0
            ]
            if visible_patterns:
                parts.append("patterns=[" + ",".join(visible_patterns) + "]")
        if col.top_values:
            top = ", ".join(f"{v.value}({v.count})" for v in col.top_values[:3])
            parts.append(f"top=[{top}]")
        lines.append(" ".join(parts))

    if card.candidate_primary_keys:
        keys = "; ".join("+".join(k) for k in card.candidate_primary_keys)
        lines.append(f"  candidate keys: {keys}")

    errors = [i for i in card.issues if i.severity in ("warn", "error")]
    if errors:
        lines.append("  issues:")
        lines.extend(f"    [{i.severity}] {i.code}: {i.detail}" for i in errors)

    return "\n".join(lines)


__all__ = [
    "ProfileOptions",
    "classify_sensitivity",
    "datacard_digest",
    "infer_semantic_type",
    "profile_column",
    "profile_table",
    "profile_tables",
]
