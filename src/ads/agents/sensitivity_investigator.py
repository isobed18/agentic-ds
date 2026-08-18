"""Ask a local agent which columns are personal, and let measurement overrule it.

The deterministic classifier decides which columns are dropped from the feature
pool. It is a name-matcher plus a set of checksums, and it fails in both
directions: it misses personal columns whose names it does not recognise, and it
flags derived counters that merely mention a personal word.

The half it cannot do is semantics. Nothing in a phrase list reads
``basvuru_sahibi`` and concludes "applicant, therefore a person" — and no
off-the-shelf PII model does either, because they all classify spans of prose
rather than columns. A local model does read it that way, which is why this
exists.

Two rules keep it honest, and both are enforced rather than requested:

* **A checksum cannot be overruled.** If a column's values pass the TCKN, IBAN
  or Luhn check, it is personal whatever the agent says. Arithmetic beats
  opinion; the agent may only add.
* **The agent never sees a value.** It receives the column name, semantic type,
  null and unique rates — the same profile a person would read. Giving it rows
  to judge would make it better at this one task and would put personal data in
  a model context to decide whether that data is personal, which is the shape of
  the circularity worth avoiding.

The agent proposes; the deterministic layer decides. Its judgement is recorded
as its own evidence code so an audit can always separate what was measured from
what was inferred.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ads.agents.base import AgentContext, AgentSpec, run_agent
from ads.contracts.datacard import (
    DataCard,
    Sensitivity,
    SensitivityEvidence,
    SensitivityEvidenceCode,
)
from ads.llm import LARGE, StructuredLLM

#: Codes produced by arithmetic rather than by pattern or opinion. A column
#: carrying any of these is personal and the agent cannot clear it.
CHECKSUM_CODES = frozenset(
    {
        SensitivityEvidenceCode.TURKISH_ID_CHECKSUM,
        SensitivityEvidenceCode.IBAN_CHECKSUM,
        SensitivityEvidenceCode.PAYMENT_CARD_CHECKSUM,
    }
)

SYSTEM_PROMPT = """\
You judge whether a database column holds personal data about an identifiable
person. You are given each column's name and profile statistics. You are not
given any values, and you must not ask for them.

Personal: names, contact details, national identifiers, account and card
numbers, addresses, dates of birth, anything that identifies one human being.

Not personal: counts, rates, scores, lengths and other statistics *derived from*
personal data, which describe a population rather than a person. A column named
`telefon_dogrulama_orani` is a rate, not a phone number.

Column names may be in any language. Judge what the name means, not whether it
matches a list. Return only the columns you believe are personal, each with a
short reason. Returning nothing is a valid answer.
"""


class SensitivityJudgement(BaseModel):
    """One column the agent believes holds personal data."""

    column: str
    rationale: str = Field(min_length=3, max_length=400)


class SensitivityProposal(BaseModel):
    """The agent's opinion. It carries no measurement and decides nothing."""

    personal_columns: list[SensitivityJudgement] = Field(default_factory=list)


def build_spec() -> AgentSpec[SensitivityProposal]:
    return AgentSpec(
        id="sensitivity_investigator",
        system_prompt=SYSTEM_PROMPT,
        output_contract=SensitivityProposal,
        profile=LARGE,
        max_attempts=2,
    )


def build_context(card: DataCard) -> AgentContext:
    """Project one table's column profiles, without a single value."""
    lines = [
        f"- {column.name}: type={column.semantic_type.value}, "
        f"null_rate={column.null_rate:.2f}, unique_rate={column.unique_rate:.2f}, "
        f"machine_says={column.sensitivity.value}"
        for column in card.columns
    ]
    return AgentContext(
        sections={
            "Table": f"{card.table_name} ({card.n_rows} rows)",
            "Columns": "\n".join(lines),
            "Task": (
                "List the columns that hold personal data about an identifiable "
                "person. The machine's own guess is shown; correct it where it is "
                "wrong in either direction."
            ),
        },
        facts={"known_columns": [column.name for column in card.columns]},
    )


def investigate_sensitivity(
    card: DataCard, llm: StructuredLLM
) -> tuple[DataCard, list[str]]:
    """Return the card with agent-proposed sensitivity applied, and what changed.

    Failure is not fatal: if the agent errors or names a column that does not
    exist, the deterministic classification stands. This is an improvement layer
    over a working classifier, not a replacement for one.
    """
    try:
        result = run_agent(build_spec(), build_context(card), llm)
    except Exception:  # noqa: BLE001 - an unreachable local model must not stop intake
        # The claim in this docstring was false before this line existed:
        # run_agent propagates transport errors, so an unavailable Ollama took
        # the whole run down rather than degrading to the classifier.
        return card, []
    if not result.succeeded or result.output is None:
        return card, []

    known = {column.name for column in card.columns}
    proposed = {
        judgement.column: judgement.rationale
        for judgement in result.output.personal_columns
        if judgement.column in known
    }
    if not proposed:
        return card, []

    changed: list[str] = []
    columns = []
    for column in card.columns:
        rationale = proposed.get(column.name)
        already = column.sensitivity is Sensitivity.PII
        if rationale is None or already:
            columns.append(column)
            continue
        columns.append(
            column.model_copy(
                update={
                    "sensitivity": Sensitivity.PII,
                    "sensitivity_evidence": [
                        *column.sensitivity_evidence,
                        SensitivityEvidence(
                            code=SensitivityEvidenceCode.AGENT_JUDGEMENT,
                            source="agent",
                            rationale=rationale,
                        ),
                    ],
                }
            )
        )
        changed.append(column.name)

    return card.model_copy(update={"columns": columns}), changed


def checksum_backed(column) -> bool:
    """Whether arithmetic, not inference, established this column as personal."""
    return any(evidence.code in CHECKSUM_CODES for evidence in column.sensitivity_evidence)


__all__ = [
    "CHECKSUM_CODES",
    "SensitivityJudgement",
    "SensitivityProposal",
    "build_context",
    "build_spec",
    "checksum_backed",
    "investigate_sensitivity",
]
