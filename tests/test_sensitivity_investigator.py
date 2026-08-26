"""The agent-assisted PII judgement, and the two limits that make it safe.

The agent exists to catch what a name-matcher cannot: a personal column whose
name is not on any list. It must not be able to do the opposite — clear a column
that arithmetic proved personal — and it must not be handed values in order to
decide whether those values are personal.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ads.agents.sensitivity_investigator import (
    SensitivityJudgement,
    SensitivityProposal,
    build_context,
    checksum_backed,
    investigate_sensitivity,
)
from ads.contracts.datacard import Sensitivity, SensitivityEvidenceCode
from ads.intake import LoadedTable, profile_table
from ads.llm import LLMResponse


class _Agent:
    """A local model stub that answers with a fixed proposal."""

    def __init__(self, proposal: SensitivityProposal | None, *, fail: bool = False) -> None:
        self.proposal = proposal
        self.fail = fail
        self.prompts: list[str] = []

    def generate_structured(self, *, system, prompt, json_schema, profile):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("the local model is unavailable")
        payload = (self.proposal or SensitivityProposal()).model_dump()
        return LLMResponse(
            text=(self.proposal or SensitivityProposal()).model_dump_json(),
            model="stub",
            latency_s=0.0,
            parsed=payload,
        )


def make_tckn(prefix: str) -> str:
    """Build a checksum-valid Turkish national ID from a nine-digit prefix.

    Written rather than hard-coded because hard-coded ones are easy to get
    wrong: `tests/fixtures/pii_sensitivity_cases.json` contains three and only
    one of them passes the check digit, which is why the checksum detector never
    fires on that fixture and the case is actually caught by column name.
    """
    digits = [int(character) for character in prefix]
    odd = digits[0] + digits[2] + digits[4] + digits[6] + digits[8]
    even = digits[1] + digits[3] + digits[5] + digits[7]
    tenth = (odd * 7 - even) % 10
    eleventh = (sum(digits) + tenth) % 10
    return f"{prefix}{tenth}{eleventh}"


def _card(frame: pd.DataFrame):
    return profile_table(LoadedTable(name="t", frame=frame, source_uri="x", source_format="csv"))


class TestTheAgentAdds:
    def test_it_catches_a_personal_column_the_matcher_missed(self) -> None:
        card = _card(pd.DataFrame({"basvuru_sahibi": ["Zeynep Acar", "Burak Sen"]}))
        assert card.columns[0].sensitivity is Sensitivity.INTERNAL, "fixture must start missed"

        agent = _Agent(
            SensitivityProposal(
                personal_columns=[
                    SensitivityJudgement(column="basvuru_sahibi", rationale="applicant name")
                ]
            )
        )
        updated, changed = investigate_sensitivity(card, agent)

        assert changed == ["basvuru_sahibi"]
        assert updated.columns[0].sensitivity is Sensitivity.PII

    def test_its_judgement_is_recorded_as_inference_not_measurement(self) -> None:
        """An audit has to be able to tell the two apart afterwards."""
        card = _card(pd.DataFrame({"basvuru_sahibi": ["Zeynep Acar", "Burak Sen"]}))
        agent = _Agent(
            SensitivityProposal(
                personal_columns=[
                    SensitivityJudgement(column="basvuru_sahibi", rationale="applicant name")
                ]
            )
        )
        updated, _ = investigate_sensitivity(card, agent)

        evidence = updated.columns[0].sensitivity_evidence[-1]
        assert evidence.code is SensitivityEvidenceCode.AGENT_JUDGEMENT
        assert evidence.source == "agent"
        assert evidence.rationale == "applicant name"
        assert evidence.match_rate is None, "an opinion carries no measurement"


class TestTheAgentCannotSubtract:
    def test_it_cannot_clear_a_column_a_checksum_established(self) -> None:
        """Valid national ID numbers under an innocuous column name.

        Arithmetic proved these are identifiers. The agent naming the column as
        not-personal must not be able to undo that, because the whole reason the
        checksums are worth keeping is that they do not depend on anyone's
        opinion.
        """
        card = _card(pd.DataFrame({"hasta_no": [make_tckn("123456789"), make_tckn("234567891")]}))
        assert checksum_backed(card.columns[0]), (
            "arithmetic, not the column name, must establish this"
        )
        assert card.columns[0].sensitivity is Sensitivity.PII

        # The proposal shape has no field for "not personal" at all — the only
        # thing an agent can express is an addition.
        assert not hasattr(SensitivityProposal(), "not_personal_columns")

        agent = _Agent(SensitivityProposal(personal_columns=[]))
        updated, changed = investigate_sensitivity(card, agent)

        assert changed == []
        assert updated.columns[0].sensitivity is card.columns[0].sensitivity

    def test_an_unknown_column_name_is_ignored(self) -> None:
        card = _card(pd.DataFrame({"tutar": [1, 2]}))
        agent = _Agent(
            SensitivityProposal(
                personal_columns=[SensitivityJudgement(column="hayali_sutun", rationale="invented")]
            )
        )
        updated, changed = investigate_sensitivity(card, agent)
        assert changed == []
        assert updated == card


class TestTheAgentNeverSeesValues:
    def test_no_cell_value_reaches_the_prompt(self) -> None:
        """Handing it rows would make it better at this and would put personal
        data into a model context in order to decide whether that data is
        personal. That is the circularity worth refusing."""
        frame = pd.DataFrame(
            {
                "musteri_adi": ["Ayse Yilmaz", "Mehmet Demir"],
                "iban": ["TR330006100519786457841326", "TR320010009999901234567890"],
            }
        )
        card = _card(frame)
        agent = _Agent(SensitivityProposal())
        investigate_sensitivity(card, agent)

        assert agent.prompts, "the agent must actually have been asked"
        rendered = "\n".join(agent.prompts)
        for value in ["Ayse Yilmaz", "Mehmet Demir", "TR330006100519786457841326"]:
            assert value not in rendered, f"{value!r} leaked into the prompt"

    def test_the_context_carries_the_profile_a_person_would_read(self) -> None:
        card = _card(pd.DataFrame({"musteri_adi": ["Ayse Yilmaz", "Mehmet Demir"]}))
        rendered = build_context(card).render()
        assert "musteri_adi" in rendered
        assert "null_rate" in rendered and "unique_rate" in rendered


class TestFailureIsNotFatal:
    def test_an_unavailable_model_leaves_the_classification_standing(self) -> None:
        """This is an improvement layer over a working classifier, not a
        replacement for one, so it has to degrade to the classifier."""
        card = _card(pd.DataFrame({"musteri_adi": ["Ayse Yilmaz", "Mehmet Demir"]}))
        updated, changed = investigate_sensitivity(card, _Agent(None, fail=True))
        assert changed == []
        assert updated.columns[0].sensitivity is card.columns[0].sensitivity


def test_evidence_cannot_pretend_an_opinion_was_measured() -> None:
    from ads.contracts.datacard import SensitivityEvidence

    with pytest.raises(ValueError, match="cannot carry row measurements"):
        SensitivityEvidence(
            code=SensitivityEvidenceCode.AGENT_JUDGEMENT,
            source="agent",
            rationale="looks personal",
            match_count=9,
            measured_count=10,
            match_rate=0.9,
        )
