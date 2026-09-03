"""DataCard — the compact, deterministic description of a table.

Planner agents use this compact view instead of spending context on rows. A
4M-row ledger becomes ~2KB of schema and statistics, which improves token
economics and arithmetic accuracy. Local exploratory agents may additionally
read a source-preserving copy through the isolated execution boundary; that
does not weaken the DataCard's role as the shared planning contract.

Everything here is computed deterministically by ``ads.intake.profiler``. No LLM
is involved in producing a DataCard.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from ads.contracts.base import Artifact, ArtifactType, FrozenModel

#: A classification target with more levels than this is really something
#: else. Defined here rather than in `ads.discovery.support` because
#: `is_usable_target` has to apply the same limit the measurement does, and
#: contracts cannot import from discovery; `support.MAX_CLASSES` aliases it so
#: there is one number (#427).
MAX_TARGET_CLASSES = 50


class SemanticType(StrEnum):
    """Inferred meaning of a column, beyond its storage dtype.

    Storage dtype is insufficient for planning: an ``int64`` may be an
    identifier, a count, or a categorical code, and each implies different
    downstream handling. This is inferred by deterministic heuristics.
    """

    IDENTIFIER = "identifier"
    BOOLEAN = "boolean"
    CATEGORICAL = "categorical"
    NUMERIC_CONTINUOUS = "numeric_continuous"
    NUMERIC_DISCRETE = "numeric_discrete"
    DATETIME = "datetime"
    TEXT = "text"
    CONSTANT = "constant"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class Sensitivity(StrEnum):
    """Redaction class. Drives whether sample values may enter LLM context."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PII = "pii"


class SensitivityEvidenceCode(StrEnum):
    """Stable reasons emitted by deterministic sensitivity classification."""

    COLUMN_NAME = "pii_column_name"
    EMAIL_SHAPE = "pii_email_shape"
    TURKISH_ID_CHECKSUM = "pii_turkish_id_checksum"
    IBAN_CHECKSUM = "pii_iban_checksum"
    PAYMENT_CARD_CHECKSUM = "pii_payment_card_checksum"
    FORMATTED_PHONE_SHAPE = "pii_formatted_phone_shape"
    #: A local agent read the column's name and profile and judged it personal.
    #: Kept as its own code so an audit can always separate what was measured
    #: from what was inferred, and so the two can be weighed differently.
    AGENT_JUDGEMENT = "pii_agent_judgement"


class SensitivityEvidence(FrozenModel):
    """Row-free evidence for a sensitivity decision.

    Name evidence has no row population, so its counts are null. Value-shape
    evidence always reports the bounded sample denominator and match rate.
    """

    code: SensitivityEvidenceCode
    source: Literal["column_name", "value_shape", "agent"]
    #: Present only for agent evidence: why it judged the column personal.
    rationale: str | None = Field(default=None, max_length=400)
    match_count: int | None = Field(default=None, ge=0)
    measured_count: int | None = Field(default=None, ge=0)
    match_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def evidence_population_matches_source(self) -> SensitivityEvidence:
        measured = (self.match_count, self.measured_count, self.match_rate)
        if self.source in {"column_name", "agent"} and any(value is not None for value in measured):
            raise ValueError("name and agent evidence cannot carry row measurements")
        if self.source == "agent" and not self.rationale:
            raise ValueError("agent evidence must say why")
        if self.source != "agent" and self.rationale is not None:
            # A measured detector has no opinion to record; if it grew one, the
            # audit could no longer tell inference from measurement.
            raise ValueError("only agent evidence carries a rationale")
        if self.source == "value_shape":
            if any(value is None for value in measured):
                raise ValueError("value-shape evidence requires row measurements")
            if self.measured_count == 0 or self.match_count > self.measured_count:
                raise ValueError("invalid value-shape evidence population")
        return self


class TextScript(StrEnum):
    """Dominant Unicode script in row-free text-shape measurements."""

    LATIN = "latin"
    CYRILLIC = "cyrillic"
    GREEK = "greek"
    ARABIC = "arabic"
    HEBREW = "hebrew"
    HAN = "han"
    HANGUL = "hangul"
    DEVANAGARI = "devanagari"
    THAI = "thai"
    MIXED = "mixed"
    OTHER = "other"
    NONE = "none"


class NumericStats(FrozenModel):
    min: float
    max: float
    mean: float
    std: float
    p25: float
    p50: float
    p75: float
    zero_rate: float = Field(ge=0.0, le=1.0)
    negative_rate: float = Field(ge=0.0, le=1.0)


class DatetimeStats(FrozenModel):
    min: str
    max: str
    span_days: int = Field(ge=0)
    parsed_count: int | None = Field(default=None, ge=0)
    invalid_count: int | None = Field(default=None, ge=0)
    parse_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class TextStats(FrozenModel):
    """Aggregate text shape. No source value or recoverable substring is retained."""

    measured_count: int = Field(ge=0)
    min_length: int = Field(ge=0)
    max_length: int = Field(ge=0)
    mean_length: float = Field(ge=0.0)
    p50_length: float = Field(ge=0.0)
    p90_length: float = Field(ge=0.0)
    mean_token_count: float = Field(ge=0.0)
    p50_token_count: float = Field(ge=0.0)
    p90_token_count: float = Field(ge=0.0)
    blank_string_rate: float = Field(ge=0.0, le=1.0)
    contains_whitespace_rate: float = Field(ge=0.0, le=1.0)
    punctuation_character_rate: float = Field(ge=0.0, le=1.0)
    dominant_script: TextScript
    dominant_script_rate: float = Field(ge=0.0, le=1.0)
    email_pattern_rate: float = Field(ge=0.0, le=1.0)
    url_pattern_rate: float = Field(ge=0.0, le=1.0)
    identifier_pattern_rate: float = Field(ge=0.0, le=1.0)


class ValueCount(FrozenModel):
    value: str
    count: int


class ColumnProfile(FrozenModel):
    """Deterministic profile of one column."""

    name: str
    dtype: str
    semantic_type: SemanticType
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    sensitivity_evidence: list[SensitivityEvidence] = Field(default_factory=list)

    null_count: int = Field(ge=0)
    null_rate: float = Field(ge=0.0, le=1.0)
    n_unique: int = Field(ge=0)
    unique_rate: float = Field(ge=0.0, le=1.0)
    is_unique: bool = False

    numeric: NumericStats | None = None
    datetime: DatetimeStats | None = None
    text: TextStats | None = None
    top_values: list[ValueCount] = Field(default_factory=list)
    sample_values: list[str] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)

    @property
    def is_usable_target(self) -> bool:
        """Whether this column could plausibly serve as a supervised target.

        Deliberately conservative and deterministic; the ProblemDiscoveryAgent
        proposes targets, but this pre-filter keeps it from proposing something
        structurally impossible (all-null, constant, or an identifier).

        #427: it used to stop there, so the "Candidate targets" digest handed to
        the agent offered framings `compute_support` was then guaranteed to
        block -- a free-text claim description with 97,219 distinct values in
        100,000 rows, and a raw timestamp, both listed as targets. The agent
        proposed one, the measurement rejected it, and the stage failed with
        nothing viable. The pre-filter and the measurement have to agree, so
        this excludes exactly what `_check_task_target_compatibility` cannot
        admit: a supervised target is numeric (regression) or has between 2 and
        `MAX_TARGET_CLASSES` levels (classification), which leaves out text,
        datetime, and any near-unique non-numeric column.
        """
        if self.sensitivity is Sensitivity.PII:
            return False
        if self.semantic_type in _UNUSABLE_TARGET_TYPES:
            return False
        if self.null_rate >= 0.5:
            return False
        # Numeric targets are regression candidates whatever their cardinality;
        # a 100,000-level numeric column is a perfectly good one. Everything
        # else can only be a classification target, and beyond the class limit
        # `too_many_classes` blocks it outright.
        if self.semantic_type in (
            SemanticType.NUMERIC_CONTINUOUS,
            SemanticType.NUMERIC_DISCRETE,
        ):
            return True
        return 2 <= self.n_unique <= MAX_TARGET_CLASSES


#: Semantic types no supervised target can have. Identifier, constant and
#: empty are structurally impossible; unknown means the profiler could not say
#: what the column is. Text and datetime are here because
#: `_check_task_target_compatibility` blocks both -- regression needs a numeric
#: target and classification needs countable classes (#427).
_UNUSABLE_TARGET_TYPES = frozenset(
    {
        SemanticType.IDENTIFIER,
        SemanticType.CONSTANT,
        SemanticType.EMPTY,
        SemanticType.UNKNOWN,
        SemanticType.TEXT,
        SemanticType.DATETIME,
    }
)


class LoadIssue(FrozenModel):
    """A problem encountered while reading the source file.

    Surfaced rather than silently repaired: per risk R6, messy enterprise files
    should fail loudly to a human gate instead of being guessed at.
    """

    severity: str = Field(pattern="^(info|warn|error)$")
    code: str
    detail: str


class DataCard(Artifact):
    """Compact description of one source table."""

    artifact_type: ClassVar[ArtifactType] = ArtifactType.DATA_CARD
    schema_version: ClassVar[str] = "2"

    table_name: str
    source_uri: str
    source_format: str
    sheet_name: str | None = None

    n_rows: int = Field(ge=0)
    n_columns: int = Field(ge=0)
    columns: list[ColumnProfile]

    candidate_primary_keys: list[list[str]] = Field(default_factory=list)
    issues: list[LoadIssue] = Field(default_factory=list)

    profiled_rows: int = Field(ge=0, description="Rows actually scanned (may be a sample).")
    sampled: bool = False

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def candidate_targets(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.is_usable_target]

    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def summary(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "n_rows": self.n_rows,
            "n_columns": self.n_columns,
            "has_errors": self.has_errors(),
            "n_candidate_keys": len(self.candidate_primary_keys),
        }
