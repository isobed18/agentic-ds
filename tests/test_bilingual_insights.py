"""Agent explanations are written in both languages and chosen at the edge.

Two people can read the same run in different languages, so a translation cannot
happen when the run executes. Both halves are written in the same pass and the
request picks one.
"""

from __future__ import annotations

import pytest

from ads.api import i18n
from ads.api.service import ControlPlane
from ads.contracts.comprehension import (
    InterpretationItem,
    InterpretationKind,
    InterpretationProposal,
    ModelConfidence,
)
from ads.contracts.evidence import MeasurementKind, MeasurementRecord, SubjectRef
from ads.store import ArtifactStore


def _measurement() -> MeasurementRecord:
    # Content-addressed: the id is derived, not chosen, so the record is built
    # through its own constructor rather than assembled by hand.
    return MeasurementRecord.create(
        source_artifact_id="a" * 64,
        field_path="/columns/physician_id",
        kind=MeasurementKind.KEY_CARDINALITY,
        subjects=[SubjectRef(table="physicians", column="physician_id")],
        value={"n_distinct": 800},
    )


def _proposal(**overrides) -> InterpretationProposal:
    base = {
        "kind": InterpretationKind.ROW_GRAIN,
        "subjects": [SubjectRef(table="physicians", column="physician_id")],
        "measurement_ids": [_measurement().measurement_id],
        "interpretation": "One row per physician.",
        "why_it_matters": "It sets the grain every later join must preserve.",
        "verification_question": "Is a physician ever recorded twice here?",
        "confidence": ModelConfidence.MEDIUM,
    }
    return InterpretationProposal(**{**base, **overrides})


class TestBothLanguagesArePersisted:
    def test_turkish_is_carried_onto_the_stored_item(self) -> None:
        item = InterpretationItem.from_proposal(
            _proposal(
                interpretation_tr="Her satır bir hekimi temsil ediyor.",
                why_it_matters_tr="Sonraki her birleştirmenin koruması gereken granülerlik bu.",
                verification_question_tr="Bir hekim burada iki kez kayıtlı olabilir mi?",
            ),
            {_measurement().measurement_id: _measurement()},
        )
        assert item.interpretation_tr == "Her satır bir hekimi temsil ediyor."
        assert item.verification_question_tr

    def test_the_translation_does_not_change_the_identity(self) -> None:
        """A translation of the same finding is the same finding.

        If Turkish entered the canonical payload, one interpretation would get
        two different content-addressed ids depending on whether the model
        managed the second language.
        """
        measurements = {_measurement().measurement_id: _measurement()}
        without = InterpretationItem.from_proposal(_proposal(), measurements)
        with_tr = InterpretationItem.from_proposal(
            _proposal(interpretation_tr="Her satır bir hekim."), measurements
        )
        assert without.interpretation_id == with_tr.interpretation_id

    def test_a_model_that_writes_only_english_still_validates(self) -> None:
        """Failing the whole item would leave the reader with nothing, which is
        worse than leaving them with the English."""
        item = InterpretationItem.from_proposal(
            _proposal(), {_measurement().measurement_id: _measurement()}
        )
        assert item.interpretation
        assert item.interpretation_tr is None


class TestTheEdgePicksOne:
    @pytest.fixture
    def plane(self, tmp_path) -> ControlPlane:
        return ControlPlane(store=ArtifactStore(tmp_path / "artifacts"))

    def _story(self, plane: ControlPlane, payload: dict) -> dict:
        artifact = {
            "artifact_id": "x",
            "type": "comprehension_brief",
            "presentation": {"title": "t", "description": "d", "facts": []},
        }
        plane.artifact_payload = lambda _id: payload  # type: ignore[method-assign]
        return plane._artifact_story(artifact)

    def _payload(self) -> dict:
        return {
            "items": [
                {
                    "kind": "row_grain",
                    "interpretation": "One row per physician.",
                    "interpretation_tr": "Her satır bir hekim.",
                    "why_it_matters": "It sets the grain.",
                    "why_it_matters_tr": "Granülerliği bu belirliyor.",
                    "verification": {"question": "Ever recorded twice?"},
                    "verification_question_tr": "İki kez kayıtlı olabilir mi?",
                    "subjects": [{"table": "physicians", "column": "physician_id"}],
                }
            ]
        }

    def test_turkish_reader_gets_turkish(self, plane: ControlPlane) -> None:
        with i18n.using("tr"):
            story = self._story(plane, self._payload())
        insight = story["insights"][0]
        assert insight["interpretation"] == "Her satır bir hekim."
        assert insight["verification_question"] == "İki kez kayıtlı olabilir mi?"

    def test_english_reader_gets_english(self, plane: ControlPlane) -> None:
        with i18n.using("en"):
            story = self._story(plane, self._payload())
        insight = story["insights"][0]
        assert insight["interpretation"] == "One row per physician."
        assert insight["verification_question"] == "Ever recorded twice?"

    def test_a_turkish_reader_falls_back_rather_than_seeing_a_blank(
        self, plane: ControlPlane
    ) -> None:
        payload = self._payload()
        payload["items"][0].pop("interpretation_tr")
        payload["items"][0].pop("verification_question_tr")
        with i18n.using("tr"):
            story = self._story(plane, payload)
        insight = story["insights"][0]
        assert insight["interpretation"] == "One row per physician."
        assert insight["verification_question"] == "Ever recorded twice?"


def test_the_prompt_actually_asks_for_both() -> None:
    """The contract having the fields is not the same as the model filling them."""
    from ads.agents.interpretation import SYSTEM_PROMPT

    assert "_tr" in SYSTEM_PROMPT
    assert "Turkish" in SYSTEM_PROMPT
