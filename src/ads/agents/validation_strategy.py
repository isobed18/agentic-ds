"""ValidationStrategyAgent - choose an honest evaluation split.

Python measures the constraints and has veto power over unsafe proposals. The
agent contributes the judgment that remains: which measured entity/date columns
to use, a cutoff, fold count, holdout size and a business-readable rationale.
Raw ABT rows never enter its context.
"""

from __future__ import annotations

import pandas as pd

from ads.agents.base import AgentContext, AgentSpec, ValidationFailure, suggest_name
from ads.contracts.datacard import DataCard
from ads.contracts.gates import PermissionTier
from ads.contracts.validation import (
    SplitStrategy,
    ValidationSignals,
    ValidationStrategyProposal,
)
from ads.discovery.validation_signals import (
    MIN_STRATIFIABLE_CLASS_COUNT,
    MIN_TEMPORAL_SPAN_DAYS,
    full_coverage_group_columns,
    recommend_strategy,
    validation_signals_digest,
)
from ads.intake.profiler import datacard_digest
from ads.llm.client import LARGE
from ads.skills import render_skills, select_skills
from ads.turkish_style import TURKISH_PROSE_INSTRUCTION

SYSTEM_PROMPT = """\
You are a senior data scientist designing an honest evaluation split for an \
enterprise analytical base table (ABT).

You are given the ABT profile and deterministic measurements of entity \
repetition, time coverage, class balance and per-fold support. Choose a split \
strategy and explain the operational trade-off.

Rules:
- Strategies are not ranked. Each prevents a different leak, and you must choose one \
that prevents every leak the measurements show:
    * grouped           prevents entity leakage (the same entity in train and test)
    * temporal          prevents future leakage (training on data later than test)
    * grouped_temporal  prevents both
    * stratified        preserves class balance only; it prevents neither
    * random            prevents nothing
  `grouped` is NOT a safer version of `temporal`. It provides no temporal protection at \
all, and vice versa.
- Repeating identifier domains plus a multi-period datetime span require grouped_temporal.
- Repeating identifier domains require grouped, so the same candidate entity cannot enter \
train and test.
- A multi-period datetime span requires temporal, holding out the most recent period.
- Imbalanced classification without entity/time constraints requires stratified, but only \
when the measurements say stratification is available. A continuous target, or a class with a \
single row, cannot be stratified by any splitter: choose the strategy the measurements do \
allow, and say so in the rationale.
- Use exact ABT column names for group_column and time_column.
- Group only on a FULL-COVERAGE GROUP CANDIDATE: a measured repeating identifier \
that contains every other repeating identifier domain. Never use a unique row key or a \
repeating identifier that leaves another domain spanning groups.
- For a temporal strategy, choose a holdout_cutoff inside the measured date range.
- State the consequence in business terms: what future population the holdout simulates \
and what optimistic bias the rejected weaker split would introduce.
- Put that rationale in canonical English in `rationale` and provide its faithful
Turkish rendering in `rationale_tr` in the same response.
- Do not reproduce or invent detected_signals. The system attaches those measurements.

The deterministic recommendation is a safety floor. Any strategy missing a protection it \
provides will be rejected.

Known limitation, state it in your rationale if it applies: the available strategies \
cannot combine class stratification with entity or temporal protection. If the data is \
imbalanced AND has repeated entities or a multi-period span, you must choose entity/time \
protection and say explicitly that class balance across folds is not guaranteed.\
"""

# #406: a reader flagged "belge külliyatı" -- the models' literal rendering
# of "document corpus", and not a phrase Turkish speakers use. Every prompt that
# asks for `_tr` prose carries the same correction; `house_turkish` cleans up the
# runs that were written before it.
SYSTEM_PROMPT += f"\n{TURKISH_PROSE_INSTRUCTION}\n"


# Each strategy is described by *which leaks it prevents*, not by a rank.
# Safety is a subset test over these sets, which is a partial order: `grouped`
# and `temporal` are incomparable because they defend against different things.
_ENTITY_ISOLATION = "entity_isolation"
_TEMPORAL_ORDERING = "temporal_ordering"
_CLASS_BALANCE = "class_balance"

_PROTECTIONS: dict[SplitStrategy, frozenset[str]] = {
    SplitStrategy.RANDOM: frozenset(),
    SplitStrategy.STRATIFIED: frozenset({_CLASS_BALANCE}),
    SplitStrategy.TEMPORAL: frozenset({_TEMPORAL_ORDERING}),
    SplitStrategy.GROUPED: frozenset({_ENTITY_ISOLATION}),
    SplitStrategy.GROUPED_TEMPORAL: frozenset({_ENTITY_ISOLATION, _TEMPORAL_ORDERING}),
}


def build_context(abt_card: DataCard, signals: ValidationSignals) -> AgentContext:
    """Project only the ABT DataCard and measured signals into model context."""
    repeated = {item.column: item for item in signals.repeated_entity_keys}
    safe_groups = full_coverage_group_columns(signals)
    temporal = {item.column: item for item in signals.temporal_spans}
    skills = select_skills("validation_strategy", [abt_card])
    sections = {
        "Analytical base table": datacard_digest(abt_card),
        "Validation signals": validation_signals_digest(signals),
        "Task": "Choose the validation strategy and justify its business consequence.",
    }
    if skills:
        sections["Applicable skills"] = render_skills(skills)
    return AgentContext(
        sections=sections,
        facts={
            "known_columns": abt_card.column_names,
            "semantic_by_column": {
                column.name: column.semantic_type.value for column in abt_card.columns
            },
            "unique_rate_by_column": {
                column.name: column.unique_rate for column in abt_card.columns
            },
            "repeated_entities_by_column": repeated,
            "full_coverage_group_columns": safe_groups,
            "temporal_spans_by_column": temporal,
            # #450: the target's own shape, which no validator could see. The
            # proposal was checked for column presence and for not being weaker
            # than the measured signals, and never for whether the strategy it
            # names can execute against the column it will split on.
            "target_column": signals.target_column,
            "target_class_support": signals.minority_class_count,
            "target_null_rate": (
                next(
                    (
                        column.null_rate
                        for column in abt_card.columns
                        if column.name == signals.target_column
                    ),
                    None,
                )
                if signals.target_column
                else None
            ),
            "recommended_strategy": recommend_strategy(signals),
            "skill_ids": [skill.skill_id for skill in skills],
        },
    )


def validate_columns_exist(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Reject group/time references that are not columns of the ABT."""
    known: list[str] = list(context.facts.get("known_columns", []))
    failures: list[ValidationFailure] = []
    for field_name in ("group_column", "time_column"):
        column = getattr(proposal, field_name)
        if column is not None and column not in known:
            failures.append(
                ValidationFailure(
                    layer="semantic",
                    code="unknown_column",
                    detail=f"{field_name} {column!r} is not a column of the ABT.",
                    field_path=field_name,
                    repair_suggestion=suggest_name(column, known),
                )
            )
    return failures


def _containment_gaps(repeated: dict, columns: list[str] | None = None) -> list[str]:
    """Render row-free reasons a candidate does not protect a repeating domain."""
    selected = columns or sorted(repeated)
    details: list[str] = []
    repeated_columns = sorted(repeated)
    for candidate_column in selected:
        candidate = repeated[candidate_column]
        containment = {item.column: item for item in candidate.containment}
        if not containment:
            details.append(f"grouping by {candidate_column}: containment not measured")
            continue
        for repeated_column in repeated_columns:
            cell = containment.get(repeated_column)
            if cell is None:
                details.append(
                    f"grouping by {candidate_column}: {repeated_column} was not measured"
                )
            elif cell.spanning_value_count > 0 or cell.missing_group_value_count > 0:
                detail = (
                    f"grouping by {candidate_column} leaves "
                    f"{cell.spanning_value_count} of {cell.repeated_value_count} "
                    f"repeated values of {repeated_column} spanning groups"
                )
                if cell.missing_group_value_count:
                    detail += f" and {cell.missing_group_value_count} with a missing group value"
                details.append(detail)
    return details


def validate_group_column_repeats(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Grouped strategies must use a candidate containing every recurrence."""
    if proposal.strategy not in {
        SplitStrategy.GROUPED,
        SplitStrategy.GROUPED_TEMPORAL,
    }:
        return []

    column = proposal.group_column
    known: list[str] = list(context.facts.get("known_columns", []))
    if column is None or column not in known:
        return []  # Contract/column validation reports the primary failure.

    repeated: dict = context.facts.get("repeated_entities_by_column", {})
    safe_groups = sorted(context.facts.get("full_coverage_group_columns", []))
    if column in safe_groups:
        return []

    if column not in repeated:
        unique_rates: dict[str, float] = context.facts.get("unique_rate_by_column", {})
        unique_rate = unique_rates.get(column)
        measured = (
            f" Its measured unique_rate is {unique_rate:.3f}." if unique_rate is not None else ""
        )
        return [
            ValidationFailure(
                layer="consistency",
                code="group_column_does_not_repeat",
                detail=(
                    f"Column {column!r} is not a repeating identifier.{measured} "
                    "Grouping on a unique row key does not keep repeated entities together."
                ),
                field_path="group_column",
                repair_suggestion=safe_groups[0] if safe_groups else None,
            )
        ]

    if not safe_groups:
        matrix = "; ".join(_containment_gaps(repeated))
        return [
            ValidationFailure(
                layer="consistency",
                code="no_full_coverage_group_column",
                detail=("No single column protects every repeating identifier domain. " + matrix),
                field_path="group_column",
            )
        ]

    gaps = "; ".join(_containment_gaps(repeated, [column]))
    return [
        ValidationFailure(
            layer="consistency",
            code="group_column_does_not_contain_recurrences",
            detail=(
                f"Column {column!r} repeats but does not protect every repeating "
                f"identifier domain. {gaps}"
            ),
            field_path="group_column",
            repair_suggestion=safe_groups[0],
        )
    ]


def validate_temporal_column_spans_periods(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Temporal strategies require an observed multi-period datetime column."""
    if proposal.strategy not in {
        SplitStrategy.TEMPORAL,
        SplitStrategy.GROUPED_TEMPORAL,
    }:
        return []

    column = proposal.time_column
    known: list[str] = list(context.facts.get("known_columns", []))
    if column is None or column not in known:
        return []

    spans: dict = context.facts.get("temporal_spans_by_column", {})
    span = spans.get(column)
    if span is not None and span.span_days >= MIN_TEMPORAL_SPAN_DAYS:
        return []

    semantic: dict[str, str] = context.facts.get("semantic_by_column", {})
    description = semantic.get(column, "unknown")
    if span is not None:
        description = f"datetime with only a {span.span_days}-day span"
    alternatives = sorted(
        name for name, item in spans.items() if item.span_days >= MIN_TEMPORAL_SPAN_DAYS
    )
    return [
        ValidationFailure(
            layer="consistency",
            code="time_column_not_multi_period",
            detail=(
                f"Column {column!r} is {description}, not a datetime column spanning "
                "multiple observed periods. A temporal holdout cannot be formed from it."
            ),
            field_path="time_column",
            repair_suggestion=alternatives[0] if alternatives else None,
        )
    ]


def missing_protections(proposal: SplitStrategy, recommended: SplitStrategy) -> frozenset[str]:
    """Protections the recommendation provides that the proposal does not.

    Entity isolation and temporal ordering are *independent* guarantees, so a
    single ranking cannot express "at least as safe". Under a total order,
    ``grouped`` (rank 4) outranks ``temporal`` (rank 3) while providing no
    temporal protection at all — a run with a multi-year span would train on the
    future and test on the past.

    In practice ``validate_group_column_repeats`` currently rejects that case
    first, so the ordering was safe by interaction rather than by design. This
    makes the property explicit instead, so it survives someone editing either
    validator independently.
    """
    return frozenset(_PROTECTIONS[recommended] - _PROTECTIONS[proposal])


def validate_strategy_not_weaker(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Give deterministic measurements veto power over an unsafe split."""
    recommended = context.facts.get("recommended_strategy")
    if not isinstance(recommended, SplitStrategy):
        return []

    missing = missing_protections(proposal.strategy, recommended)
    if not missing:
        return []

    repeated = sorted(context.facts.get("repeated_entities_by_column", {}))
    temporal = sorted(
        name
        for name, span in context.facts.get("temporal_spans_by_column", {}).items()
        if span.span_days >= MIN_TEMPORAL_SPAN_DAYS
    )
    reasons: list[str] = []
    if _ENTITY_ISOLATION in missing and repeated:
        reasons.append(f"repeating identifier domains in {repeated} require entity isolation")
    if _TEMPORAL_ORDERING in missing and temporal:
        reasons.append(f"multi-period dates in {temporal} require a temporal holdout")
    if _CLASS_BALANCE in missing:
        reasons.append("the measured class imbalance requires stratification")
    reason_text = "; ".join(reasons) or "the measured signals"

    return [
        ValidationFailure(
            layer="consistency",
            code="strategy_weaker_than_detected_signals",
            detail=(
                f"Strategy {proposal.strategy.value!r} omits {sorted(missing)} but "
                f"{reason_text}. The deterministic minimum is {recommended.value!r}; "
                "a weaker split leaks entities or future information into training."
            ),
            field_path="strategy",
            repair_suggestion=recommended.value,
        )
    ]


def validate_stratification_is_executable(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """Reject a stratified split the target cannot support.

    #450 (split from #440): the proposal was validated for column presence and
    for not being weaker than the measured signals, and never for whether the
    named strategy can run at all. `STRATIFIED` is the one that needs it and the
    one with no check, so on the MovieLens suite it was proposed, trialled,
    failed, escalated generically, approved by a person shown no reason, and
    crashed five stages later in `splitting_stage`.

    Three preconditions, each measured here from facts the run already has:

    * The target must be a classification target. `rating` is a float over
      0.5..5.0, so `type_of_target` calls it continuous and `StratifiedKFold`
      refuses it. `minority_class_count` is measured only for a classification
      task, so its absence is exactly that answer.
    * Every class needs two members. `genres` has 951 distinct values with
      singletons among them; the outer `train_test_split` needs one row per
      class on each side and the inner folds need more still.
    * The target must have no nulls. `tmdb_id` is 0.1% null, and that alone is
      fatal -- `Input y contains NaN`.

    Rejected with a strategy that *can* run rather than with a description of
    the problem: stratification protects class balance and nothing else, so
    when it cannot execute the honest replacement is whatever the measurements
    require without it.
    """
    if proposal.strategy is not SplitStrategy.STRATIFIED:
        return []
    target = context.facts.get("target_column")
    if not target:
        return []

    recommended = context.facts.get("recommended_strategy")
    replacement = (
        recommended.value
        if isinstance(recommended, SplitStrategy) and recommended is not SplitStrategy.STRATIFIED
        else SplitStrategy.RANDOM.value
    )

    null_rate = context.facts.get("target_null_rate")
    if isinstance(null_rate, float) and null_rate > 0:
        return [
            ValidationFailure(
                layer="consistency",
                code="stratify_target_has_nulls",
                detail=(
                    f"Stratified splitting cannot run on {target!r}: {null_rate:.2%} of its "
                    "rows are null, and a stratified split refuses a target containing NaN."
                ),
                field_path="strategy",
                repair_suggestion=replacement,
            )
        ]

    support = context.facts.get("target_class_support")
    if support is None:
        semantic = dict(context.facts.get("semantic_by_column", {})).get(target, "unknown")
        return [
            ValidationFailure(
                layer="consistency",
                code="stratify_non_classification_target",
                detail=(
                    f"Stratified splitting cannot run on {target!r}: it is measured as "
                    f"{semantic!r} and carries no class support, so the splitter reads it as "
                    "a continuous target. Stratification applies to classes."
                ),
                field_path="strategy",
                repair_suggestion=replacement,
            )
        ]
    if support < MIN_STRATIFIABLE_CLASS_COUNT:
        return [
            ValidationFailure(
                layer="consistency",
                code="stratify_singleton_class",
                detail=(
                    f"Stratified splitting cannot run on {target!r}: its smallest class has "
                    f"{support} row(s), and every class needs at least "
                    f"{MIN_STRATIFIABLE_CLASS_COUNT} so one can go to train and one to "
                    "holdout. A class this rare is a modelling problem, not a split one."
                ),
                field_path="strategy",
                repair_suggestion=replacement,
            )
        ]
    return []


def _timestamp(value: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert(None)
    return parsed


def validate_holdout_cutoff(
    proposal: ValidationStrategyProposal, context: AgentContext
) -> list[ValidationFailure]:
    """A supplied cutoff must be parseable and inside the selected date range."""
    if proposal.holdout_cutoff is None:
        return []
    if proposal.time_column is None:
        return [
            ValidationFailure(
                layer="semantic",
                code="cutoff_without_time_column",
                detail="holdout_cutoff requires a time_column with an observed date range.",
                field_path="holdout_cutoff",
            )
        ]

    spans: dict = context.facts.get("temporal_spans_by_column", {})
    span = spans.get(proposal.time_column)
    if span is None:
        return []  # The temporal-column validator reports the missing measurement.

    try:
        cutoff = _timestamp(proposal.holdout_cutoff)
        minimum = _timestamp(span.min_date)
        maximum = _timestamp(span.max_date)
    except (TypeError, ValueError):
        return [
            ValidationFailure(
                layer="semantic",
                code="invalid_holdout_cutoff",
                detail=(f"holdout_cutoff {proposal.holdout_cutoff!r} is not a valid ISO date."),
                field_path="holdout_cutoff",
            )
        ]

    if minimum <= cutoff <= maximum:
        return []
    return [
        ValidationFailure(
            layer="consistency",
            code="holdout_cutoff_outside_observed_range",
            detail=(
                f"holdout_cutoff {proposal.holdout_cutoff!r} falls outside the observed "
                f"range {span.min_date[:10]}..{span.max_date[:10]} for "
                f"{proposal.time_column!r}."
            ),
            field_path="holdout_cutoff",
            repair_suggestion=span.max_date[:10],
        )
    ]


def build_spec() -> AgentSpec[ValidationStrategyProposal]:
    return AgentSpec(
        id="validation_strategy",
        system_prompt=SYSTEM_PROMPT,
        output_contract=ValidationStrategyProposal,
        profile=LARGE,
        validators=(
            validate_columns_exist,
            validate_group_column_repeats,
            validate_temporal_column_spans_periods,
            validate_strategy_not_weaker,
            validate_stratification_is_executable,
            validate_holdout_cutoff,
        ),
        max_attempts=3,
        rubric="validation_strategy.v1",
        # #166: same shared-context handoff as problem_discovery. The validation
        # scout (validation_investigator.investigate_validation_context) runs on
        # this context first and admits its evidence, including
        # `trial_validation_strategy`. Without declaring it, the panel would
        # reject the shared context as an undeclared tool the moment the scout
        # measured a trial split -- the identical latent defect one stage later.
        allowed_tools=frozenset(
            {
                "cardinality",
                "column_profile",
                "null_rate",
                "trial_validation_strategy",
                "validation_signals",
                "value_counts",
            }
        ),
        max_tool_tier=PermissionTier.READ_DATA,
        column_fields=frozenset({"group_column", "time_column"}),
        # Every other field changes how the split is actually built.
        narration_fields=frozenset({"rationale", "rationale_tr"}),
    )


__all__ = [
    "SYSTEM_PROMPT",
    "build_context",
    "build_spec",
    "missing_protections",
    "validate_columns_exist",
    "validate_group_column_repeats",
    "validate_holdout_cutoff",
    "validate_stratification_is_executable",
    "validate_strategy_not_weaker",
    "validate_temporal_column_spans_periods",
]
