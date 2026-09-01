"""Kosum hatasi ekrana nasil dusuyor: okunabilir mi, cevrilebilir mi.

Bildirilen hali (#263):

    MissingArtifactError: run 'run-446ef0b8' has no 'integration_plan'
    artifact; an upstream stage did not produce it

Python sinif adi, run id'si ve tek dil. Kullanicinin bundan cikaracagi bir sey
yok. Kirmizi bandin hic Turkcelesmemesi de (#265) ayni yerden geliyordu.
"""

from __future__ import annotations

from ads.api.service import _run_error_text
from ads.orchestration import MissingArtifactError


def _eksik(tur: str = "integration_plan") -> MissingArtifactError:
    return MissingArtifactError(
        f"run 'run-446ef0b8' has no {tur!r} artifact; an upstream stage did not produce it"
    )


def test_eksik_artifact_iki_dilde_ve_okunabilir() -> None:
    metin = _run_error_text(_eksik())

    assert set(metin) == {"en", "tr"}
    assert metin["tr"] != metin["en"], "Turkcesi gercekten cevrilmis olmali"
    for dil in metin.values():
        assert "MissingArtifactError" not in dil, "sinif adi kullaniciya gosterilmez"
        assert "run-446ef0b8" not in dil, "run id'si kullaniciya bir sey soylemiyor"


def test_hangi_artifactin_eksik_oldugu_korunuyor() -> None:
    """Okunabilir yapmak, teshis icin gereken tek bilgiyi silmek degil."""
    metin = _run_error_text(_eksik("problem_definition"))

    assert "problem_definition" in metin["en"]
    assert "problem_definition" in metin["tr"]


def test_ne_yapilacagini_soyluyor() -> None:
    """Bir hata mesaji, okuyanin elinden bir sey gelmiyorsa yarim kalmistir."""
    metin = _run_error_text(_eksik())

    assert "rework" in metin["en"] or "new run" in metin["en"]
    assert "yeniden" in metin["tr"] or "yeni bir kosum" in metin["tr"]


def test_taninmayan_hata_oldugu_gibi_geciyor() -> None:
    """Bu testin varlik sebebi: her hatayi guzel bir cumleye cevirmek.

    Tanimadigimiz bir hatanin turunu ve metnini silmek, teshis icin elimizdeki
    tek bilgiyi silmek olurdu. Bilinmeyen ham haliyle gecmeli.
    """
    metin = _run_error_text(ValueError("beklenmeyen bir sey"))

    assert metin["en"] == "ValueError: beklenmeyen bir sey"
    assert metin["tr"] == metin["en"], "ceviremedigimiz seyi cevirmis gibi yapmiyoruz"
