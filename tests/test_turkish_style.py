"""House Turkish for the prose the agents write themselves (#406).

A reader flagged "belge külliyatı" as not-Turkish. It is nowhere in either
translation catalogue, because it is not a translation: the agents write the
`_tr` half of every bilingual field, and asked for "document corpus" a model
reaches for the collected-works word. The prompts now say not to, and the
contract boundary corrects the runs where they did anyway.
"""

from __future__ import annotations

import pytest

from ads.agents import (
    interpretation,
    problem_discovery,
    schema_discovery,
    validation_strategy,
)
from ads.contracts.documents import aligned_turkish
from ads.contracts.staging import LocalizedText
from ads.turkish_style import house_turkish


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("belge külliyatı", "belge kümesi"),
        # Turkish is agglutinative, so the fix has to survive every suffix the
        # word takes -- and `küme` is a front-vowel stem where `külliyat` is a
        # back-vowel one, so the suffix vowels move with it.
        ("belge külliyatını inceleyin", "belge kümesini inceleyin"),
        ("belge külliyatında 3 tablo var", "belge kümesinde 3 tablo var"),
        ("belge külliyatındaki tablolar", "belge kümesindeki tablolar"),
        ("belge külliyatının kapsamı", "belge kümesinin kapsamı"),
        ("belge külliyatıyla karşılaştırıldı", "belge kümesiyle karşılaştırıldı"),
        ("bu bir belge külliyatıdır", "bu bir belge kümesidir"),
        # Sentence-initial capitals survive.
        ("Belge külliyatı incelendi.", "Belge kümesi incelendi."),
    ],
)
def test_rewrites_the_reported_phrase_in_every_inflection(written: str, expected: str) -> None:
    assert house_turkish(written) == expected


def test_leaves_ordinary_turkish_alone() -> None:
    sentence = "Yüklenen belgelerde 3 tablo adayı bulundu; hiçbiri veriye alınmadı."
    assert house_turkish(sentence) == sentence
    assert house_turkish("") == ""


def test_declines_forms_it_cannot_convert_rather_than_mangling_them() -> None:
    # `küme` ends in a vowel, so a case ending on the bare stem takes different
    # buffers entirely -- "külliyata" is not "kümea". These are not shapes the
    # reported phrase takes, so the rule leaves them for a human.
    assert house_turkish("külliyata") == "külliyata"
    assert house_turkish("külliyatta") == "külliyatta"


def test_corrects_agent_prose_at_the_contract_boundary() -> None:
    # Every bilingual field an agent writes is a LocalizedText, so a run that
    # produced the phrase anyway still reads correctly -- and so does one
    # persisted before this existed.
    text = LocalizedText(
        en="Three files form the document corpus.", tr="Üç dosya belge külliyatını oluşturur."
    )
    assert text.tr == "Üç dosya belge kümesini oluşturur."
    # The English half is not touched; it was never wrong.
    assert text.en == "Three files form the document corpus."


def test_corrects_document_warnings_too() -> None:
    # Document warnings are the other agent-authored Turkish that reaches a
    # reader without passing through LocalizedText.
    assert aligned_turkish(["corpus warning"], ["belge külliyatı eksik"]) == ["belge kümesi eksik"]


@pytest.mark.parametrize(
    "module",
    [interpretation, problem_discovery, schema_discovery, validation_strategy],
)
def test_every_bilingual_prompt_carries_the_correction(module: object) -> None:
    prompt = module.SYSTEM_PROMPT  # type: ignore[attr-defined]
    assert "belge kümesi" in prompt
    assert "külliyat" in prompt  # named explicitly, so the model knows what to avoid
