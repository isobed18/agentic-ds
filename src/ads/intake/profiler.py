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
    SensitivityEvidence,
    SensitivityEvidenceCode,
    TextScript,
    TextStats,
    ValueCount,
)
from ads.intake.keys import detect_primary_keys
from ads.intake.loaders import LoadedTable

# Exact token phrases avoid substring failures such as ``license_plate_count``.
# Turkish spellings are normalized to ASCII before matching so both dotted and
# dotless I, and diacritics, have one deterministic representation.
_PII_NAME_PHRASES = {
    ("full", "name"),
    ("fullname",),
    ("first", "name"),
    ("firstname",),
    ("last", "name"),
    ("lastname",),
    ("surname",),
    ("email",),
    ("mail",),
    ("phone",),
    ("mobile",),
    ("tel",),
    ("fax",),
    ("ssn",),
    ("sin",),
    ("nin",),
    ("tckn",),
    ("tc", "kimlik"),
    ("national", "id"),
    ("passport",),
    ("address",),
    ("street",),
    ("postcode",),
    ("zip",),
    ("dob",),
    ("birth", "date"),
    ("iban",),
    ("account", "no"),
    ("account", "number"),
    ("credit", "card"),
    ("cvv",),
    ("driver", "license"),
    ("drivers", "license"),
    ("license", "no"),
    ("tax", "id"),
    ("musteri", "adi"),
    ("ad", "soyad"),
    ("isim", "soyisim"),
    ("dogum", "tarihi"),
    ("vergi", "no"),
    ("vergi", "numarasi"),
    ("e", "posta"),
    ("eposta",),
    ("cep", "telefonu"),
    ("telefon",),
    ("ev", "adresi"),
    ("adres",),
    ("pasaport", "no"),
    ("ehliyet", "no"),
    ("kredi", "karti"),
}
_DERIVED_FIELD_SUFFIXES = {
    "count",
    "rate",
    "ratio",
    "score",
    "length",
    "distribution",
    "type",
    "flag",
    "indicator",
    "average",
    "avg",
    "min",
    "max",
    "total",
    "sayisi",
    "orani",
    "puani",
    "uzunlugu",
    "turu",
    "tipi",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
_LONG_DIGIT_RE = re.compile(r"^[0-9]{9,}$")
_FORMATTED_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{7,18}[0-9]$")
_URL_RE = re.compile(r"^(?:https?://|www[.])[^ ]+$", re.IGNORECASE)
_STRUCTURED_ID_RE = re.compile(r"^(?=.{4,}$)(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._:/-]*$")

#: A trailing token that names a key rather than a measurement: `customer_no`,
#: `postal_code`, `sort_key`, `source_ref`. Only checked at the end, because
#: `code_review` is not an identifier while `product_code` is.
_ID_TRAILING_TOKENS = frozenset({"no", "key", "code", "ref"})
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
    detect_composite_keys: bool = True
    """Search for a composite primary key when no single column is unique (#444).

    On by default because a table recorded as keyless is not a missing detail:
    it is read as evidence, by the agent that picks the base table and by
    everything downstream of that choice. The search only runs when no
    single-column key was found, and `ads.intake.keys` bounds it to pairs drawn
    from at most twelve eligible columns -- but it is real work on a wide,
    keyless, fully-sampled table, so a caller profiling a derived frame whose
    key nobody consumes can turn it off.
    """


def _looks_like_identifier_name(name: str) -> bool:
    """Decide whether a column name claims to be a key.

    Matched on name *tokens*, not on substrings of the lowercased name. The
    substring form missed every camelCase key -- `movieId` lowercases to
    `movieid`, which contains none of `_id`, `id_` or `^id$` -- so an integer
    `movieId` was classified `numeric_continuous`, excluded from `_JOINABLE`,
    and relationship detection returned nothing at all for a four-table dataset
    whose joins were 100% overlapping. camelCase keys are the norm in anything
    exported from a JS, Java or Mongo schema, so this was not an edge case.

    Tokens also remove a false positive the substring form had: `paid_amount`
    contains `id_` and was read as a key.
    """
    tokens = _normalise_name_tokens(name)
    if not tokens:
        return False
    if "id" in tokens or "uuid" in tokens or "guid" in tokens:
        return True
    return tokens[-1] in _ID_TRAILING_TOKENS


@dataclass(frozen=True)
class SensitivityAssessment:
    sensitivity: Sensitivity
    evidence: tuple[SensitivityEvidence, ...] = ()


def _normalise_name_tokens(name: str) -> tuple[str, ...]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    folded = unicodedata.normalize("NFKD", expanded.casefold())
    ascii_like = "".join(char for char in folded if not unicodedata.combining(char))
    ascii_like = ascii_like.translate(str.maketrans({"ı": "i", "ş": "s", "ğ": "g"}))
    return tuple(token for token in re.split(r"[^a-z0-9]+", ascii_like) if token)


def _contains_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    return any(tokens[index : index + width] == phrase for index in range(len(tokens) - width + 1))


def _luhn_valid(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _iban_valid(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    return int(numeric) % 97 == 1


def _tckn_valid(value: str) -> bool:
    digits = re.sub(r"\s", "", value)
    if not re.fullmatch(r"[1-9][0-9]{10}", digits):
        return False
    values = [int(character) for character in digits]
    tenth = ((sum(values[0:9:2]) * 7) - sum(values[1:8:2])) % 10
    eleventh = sum(values[:10]) % 10
    return values[9] == tenth and values[10] == eleventh


def _value_evidence(
    code: SensitivityEvidenceCode,
    matches: pd.Series,
) -> SensitivityEvidence:
    count = int(matches.sum())
    measured = int(len(matches))
    return SensitivityEvidence(
        code=code,
        source="value_shape",
        match_count=count,
        measured_count=measured,
        match_rate=round(count / max(measured, 1), 6),
    )


def assess_sensitivity(name: str, series: pd.Series) -> SensitivityAssessment:
    """Classify sensitivity from bounded, row-free name and value-shape evidence."""
    evidence: list[SensitivityEvidence] = []
    tokens = _normalise_name_tokens(name)
    is_derived = bool(tokens and tokens[-1] in _DERIVED_FIELD_SUFFIXES)
    if not is_derived and any(_contains_phrase(tokens, phrase) for phrase in _PII_NAME_PHRASES):
        evidence.append(
            SensitivityEvidence(
                code=SensitivityEvidenceCode.COLUMN_NAME,
                source="column_name",
            )
        )

    non_null = series.dropna()
    if non_null.empty:
        sensitivity = Sensitivity.PII if evidence else Sensitivity.INTERNAL
        return SensitivityAssessment(sensitivity=sensitivity, evidence=tuple(evidence))

    sample = non_null.head(200).astype(str)
    detectors = (
        (SensitivityEvidenceCode.EMAIL_SHAPE, sample.str.fullmatch(_EMAIL_RE)),
        (SensitivityEvidenceCode.TURKISH_ID_CHECKSUM, sample.map(_tckn_valid)),
        (SensitivityEvidenceCode.IBAN_CHECKSUM, sample.map(_iban_valid)),
        (SensitivityEvidenceCode.PAYMENT_CARD_CHECKSUM, sample.map(_luhn_valid)),
        (
            SensitivityEvidenceCode.FORMATTED_PHONE_SHAPE,
            sample.str.fullmatch(_FORMATTED_PHONE_RE) & sample.str.contains(r"[^0-9]", regex=True),
        ),
    )
    for code, matches in detectors:
        measured = _value_evidence(code, matches)
        if measured.match_rate is not None and measured.match_rate > 0.8:
            evidence.append(measured)
    sensitivity = Sensitivity.PII if evidence else Sensitivity.INTERNAL
    return SensitivityAssessment(sensitivity=sensitivity, evidence=tuple(evidence))


def classify_sensitivity(name: str, series: pd.Series) -> Sensitivity:
    """Compatibility projection of :func:`assess_sensitivity`."""
    return assess_sensitivity(name, series).sensitivity


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
        is_integral = (
            pd.api.types.is_integer_dtype(series) or (non_null.astype(float) % 1 == 0).all()
        )
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
        positions = np.linspace(0, len(as_str) - 1, num=_DATETIME_SAMPLE_SIZE, dtype=int)
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
        (values.str.fullmatch(_LONG_DIGIT_RE) | values.str.fullmatch(_STRUCTURED_ID_RE))
        & ~email_matches
        & ~url_matches
    )
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

    sensitivity_assessment = assess_sensitivity(name, series)
    sensitivity = sensitivity_assessment.sensitivity
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
        sensitivity_evidence=list(sensitivity_assessment.evidence),
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

    columns = [profile_column(str(col), working[col], n_rows, options) for col in working.columns]

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

    card = DataCard(
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
    if not options.detect_composite_keys:
        return card

    # #444: `detect_primary_keys` finds the composite keys the loop above
    # cannot -- `(user_id, movie_id)` on a ratings fact table -- and had no
    # caller anywhere in the profiling path. Its two callers both sit past
    # intake and neither writes to a card, so a table whose key is composite
    # was recorded as keyless permanently.
    #
    # That is not merely a wrong count in the UI. `datacard_digest` omits the
    # "candidate keys" line entirely when the list is empty, so the agent
    # choosing `base_table` for a multi-table source was told a 100k-row fact
    # table had no key while a three-column id crosswalk had two -- a direct
    # steer to the wrong base, and from there to a target picker offering
    # nothing but identifiers, because every column of that crosswalk is one.
    #
    # The single-column list above is kept as the floor rather than replaced.
    # The two rules agree today; a union cannot regress a card that already had
    # keys if they ever drift.
    detected = [list(candidate.columns) for candidate in detect_primary_keys(card, working)]
    merged = [*candidate_keys, *(key for key in detected if key not in candidate_keys)]
    if merged == candidate_keys:
        return card
    return card.model_copy(update={"candidate_primary_keys": merged})


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
                f"range=[{col.numeric.min:.4g}..{col.numeric.max:.4g}] med={col.numeric.p50:.4g}"
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
            visible_patterns = [f"{name}={rate:.1%}" for name, rate in patterns.items() if rate > 0]
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
