"""InterpretationAgent: bounded proposed meaning over stable row-free measurements."""

from __future__ import annotations

from ads.agents.base import AgentContext, AgentSpec, ValidationFailure
from ads.contracts.comprehension import (
    ComprehensionScope,
    InterpretationBatchProposal,
    InterpretationKind,
)
from ads.contracts.evidence import MeasurementKind, MeasurementRecord, SubjectRef
from ads.discovery.measurements import measurement_digest
from ads.llm.client import LARGE
from ads.skills import render_skills, select_skills
from ads.turkish_style import TURKISH_PROSE_INSTRUCTION

SYSTEM_PROMPT = """\
You help a human understand an unfamiliar dataset without seeing source rows.

You receive a catalog of deterministic measurements. Produce at most eight high-value
interpretations. Each item must select one to four measurement ids from the catalog.
Never write an observation or measured value yourself; the executor resolves citations.

Interpretation is world-knowledge work and is never fact. Every item is proposed and
must include a concrete question a human can answer to verify or reject it. If you cannot
write a useful verification question, omit the item. Confidence is only your uncalibrated
self-assessment; do not use confidence to suppress uncertainty.

Prefer, in order: what one row means, what a table appears to be for, consequential
relationship meaning, notable non-error distribution patterns, ambiguous column meaning,
and questions whose answers change analysis. Do not restate null rates or schemas as prose.
Do not say a distribution is bimodal, heaped, migrated, or backfilled as a fact. Describe
what the measured descriptors suggest and ask how to verify it.

Use exact table and column names in structured subjects. A relationship item must name both
tables and cite a single relationship measurement that contains both tables.

Write every item twice: in English, and in Turkish in the `_tr` fields. This
deployment has readers in both languages and they may be looking at the same run
at the same time, so both are stored and the interface picks one. Write the
Turkish as Turkish rather than as a word-for-word rendering of the English --
same finding, same number, natural sentence. Keep table and column names exactly
as they appear; they are identifiers, not words to translate.
"""

# #406: a reader flagged "belge külliyatı" -- the models' literal rendering
# of "document corpus", and not a phrase Turkish speakers use. Every prompt that
# asks for `_tr` prose carries the same correction; `house_turkish` cleans up the
# runs that were written before it.
SYSTEM_PROMPT += f"\n{TURKISH_PROSE_INSTRUCTION}\n"



_COMPATIBLE_KINDS = {
    InterpretationKind.ROW_GRAIN: frozenset(
        {
            MeasurementKind.KEY_CARDINALITY,
            MeasurementKind.RELATIONSHIP_CARDINALITY,
            MeasurementKind.ROWS_PER_PARENT,
        }
    ),
    InterpretationKind.TABLE_PURPOSE: frozenset(
        {
            MeasurementKind.TABLE_PROFILE,
            MeasurementKind.KEY_CARDINALITY,
            MeasurementKind.RELATIONSHIP_CARDINALITY,
            MeasurementKind.ROWS_PER_PARENT,
            MeasurementKind.PERIOD_DISTRIBUTION,
            MeasurementKind.TEXT_SHAPE,
        }
    ),
    InterpretationKind.RELATIONSHIP: frozenset(
        {
            MeasurementKind.RELATIONSHIP_CARDINALITY,
            MeasurementKind.ROWS_PER_PARENT,
        }
    ),
    InterpretationKind.DISTRIBUTION_PATTERN: frozenset(
        {
            MeasurementKind.DISTRIBUTION_SHAPE,
            MeasurementKind.PERIOD_DISTRIBUTION,
            MeasurementKind.TEXT_SHAPE,
            MeasurementKind.NUMERIC_PROFILE,
            MeasurementKind.EXPLORATORY_ANALYSIS,
        }
    ),
    InterpretationKind.COLUMN_SEMANTICS: frozenset(
        {
            MeasurementKind.COLUMN_PROFILE,
            MeasurementKind.TEXT_SHAPE,
            MeasurementKind.DATETIME_PROFILE,
            MeasurementKind.NUMERIC_PROFILE,
            MeasurementKind.PERIOD_DISTRIBUTION,
            MeasurementKind.KEY_CARDINALITY,
            MeasurementKind.EXPLORATORY_ANALYSIS,
        }
    ),
    InterpretationKind.OPEN_QUESTION: frozenset(MeasurementKind),
}


def _citation_rules() -> str:
    """Render the compatibility map the validator will judge against.

    It existed only inside the validator, so the agent was told which
    measurements exist but not which of them each interpretation kind is allowed
    to cite. Measured on a real run: every comprehension attempt was rejected
    with `incompatible_measurement_kind` or `unknown_measurement`, both briefs
    came back degraded with zero items, and the user saw no agent explanation at
    all. A rule an agent is judged by and never shown is not a rule, it is a trap.
    """
    lines = []
    for interpretation_kind, measurement_kinds in sorted(
        _COMPATIBLE_KINDS.items(), key=lambda pair: pair[0].value
    ):
        allowed = ", ".join(sorted(kind.value for kind in measurement_kinds))
        lines.append(f"- {interpretation_kind.value} may cite: {allowed}")
    header = (
        "Cite only measurement ids listed in the catalog above, and only kinds "
        "allowed for the interpretation kind you chose:"
    )
    return "\n".join([header, *lines])


def build_context(
    bundle,
    scope: ComprehensionScope,
    cards: list | None = None,
) -> AgentContext:
    rendered, visible = measurement_digest(bundle)
    # Procedural guidance for the stage where the reader knows least about their
    # own data: what to say about an unfamiliar dataset, and how to read a schema
    # that declares nothing. Loaded through the skill registry so it is auditable
    # and versioned rather than buried in a prompt string.
    skills = select_skills("source_comprehension", cards or [])
    sections = {
        "Scope": scope.value,
        "Measured catalog": rendered,
        "Citation rules": _citation_rules(),
        "Task": (
            "Select only consequential interpretations that save a human time. "
            "Return no item that lacks measured support and a verification question."
        ),
    }
    if skills:
        sections["How to describe this"] = render_skills(skills)
    return AgentContext(
        sections=sections,
        facts={
            "measurement_by_id": visible,
            "known_columns": sorted(
                {
                    subject.column
                    for record in visible.values()
                    for subject in record.subjects
                    if subject.column is not None
                }
            ),
            "known_tables": sorted(
                {subject.table for record in visible.values() for subject in record.subjects}
            ),
        },
    )


def _covers(measured: SubjectRef, claimed: SubjectRef) -> bool:
    return measured.table == claimed.table and (
        claimed.column is None or measured.column == claimed.column
    )


def validate_measurement_binding(
    output: InterpretationBatchProposal,
    context: AgentContext,
) -> list[ValidationFailure]:
    """Check aboutness and kind compatibility, explicitly not entailment."""
    catalog: dict[str, MeasurementRecord] = context.facts.get("measurement_by_id", {})
    failures: list[ValidationFailure] = []
    for index, item in enumerate(output.items):
        missing = [reference for reference in item.measurement_ids if reference not in catalog]
        if missing:
            failures.append(
                ValidationFailure(
                    layer="evidence",
                    code="unknown_measurement",
                    detail=f"Unknown measurement ids: {missing}.",
                    field_path=f"items[{index}].measurement_ids",
                )
            )
            continue
        citations = [catalog[reference] for reference in item.measurement_ids]
        incompatible = [
            citation.kind.value
            for citation in citations
            if citation.kind not in _COMPATIBLE_KINDS[item.kind]
        ]
        if incompatible:
            failures.append(
                ValidationFailure(
                    layer="evidence",
                    code="incompatible_measurement_kind",
                    detail=(f"{item.kind.value} cannot cite measurement kinds {incompatible}."),
                    field_path=f"items[{index}].measurement_ids",
                )
            )
        measured_subjects = [subject for record in citations for subject in record.subjects]
        uncovered = [
            subject
            for subject in item.subjects
            if not any(_covers(measured, subject) for measured in measured_subjects)
        ]
        if uncovered:
            failures.append(
                ValidationFailure(
                    layer="evidence",
                    code="unbound_subject",
                    detail=f"Subjects are not present in cited measurements: {uncovered}.",
                    field_path=f"items[{index}].subjects",
                )
            )
        if item.kind is InterpretationKind.RELATIONSHIP:
            claimed_tables = {subject.table for subject in item.subjects}
            has_joint_citation = any(
                claimed_tables <= {subject.table for subject in record.subjects}
                and len({subject.table for subject in record.subjects}) >= 2
                for record in citations
            )
            if len(claimed_tables) < 2 or not has_joint_citation:
                failures.append(
                    ValidationFailure(
                        layer="evidence",
                        code="relationship_requires_joint_measurement",
                        detail=(
                            "A relationship interpretation must name both tables and cite "
                            "one measurement whose subjects include both."
                        ),
                        field_path=f"items[{index}].measurement_ids",
                    )
                )
    return failures


def build_spec() -> AgentSpec[InterpretationBatchProposal]:
    return AgentSpec(
        id="interpretation",
        system_prompt=SYSTEM_PROMPT,
        output_contract=InterpretationBatchProposal,
        profile=LARGE,
        validators=(validate_measurement_binding,),
        max_attempts=1,
        column_fields=frozenset({"column"}),
        narration_fields=frozenset(
            {
                "interpretation",
                "why_it_matters",
                "verification_question",
                "confidence",
            }
        ),
    )


__all__ = [
    "SYSTEM_PROMPT",
    "build_context",
    "build_spec",
    "validate_measurement_binding",
]
