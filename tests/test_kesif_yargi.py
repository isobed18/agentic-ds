"""KESIF dorduncu katman: human feedback, bulut LLM degil.

Bu testler `sor()` / `artigi_coz()` fonksiyonlarini gercek stdin veya
ag cagrisi olmadan dogrular: bir sahte `cevaplayici` enjekte edilir,
tipki bir UI'nin ya da bir test'in yapacagi gibi.
"""

from __future__ import annotations

import pytest

# Kesif ekstrasi (`.[kesif]`) kurulu degilse bu modul TOPLANAMAZ: magika,
# yonlendiricinin modul seviyesinde import ettigi bir bagimlilik. Guard
# olmadan collection hatasi tum suite'i durdurur -- yalnizca kesif
# testlerini degil.
pytest.importorskip("magika", reason="kesif ekstrasi kurulu degil")

from ads.file_detection.adjudication import (  # noqa: E402
    FLOW_OPTIONS,
    VALID_FLOWS,
    ResidualReport,
    ask,
    resolve_residual,
)
from ads.file_detection.router import Decision, Flow  # noqa: E402


def _artik_karar(yol: str = "belirsiz.txt") -> Decision:
    return Decision(
        yol=yol,
        boyut=17,
        format="txt",
        format_guveni=0.5,
        sekil="belirsiz",
        akis=Flow.UNPROCESSABLE,
        deterministik=False,
        yargi_sebebi="sekil olcumu belirsiz",
        notlar=["cift sinyal cakisti"],
        kanitlar=[("kodlama", "utf-8")],
    )


def _deterministik_karar() -> Decision:
    k = _artik_karar()
    k.deterministik = True
    return k


def test_secenek_listesi_gecerli_akislarla_esler() -> None:
    assert [s.akis for s in FLOW_OPTIONS] == VALID_FLOWS


def test_sor_secilen_akisi_ve_gerekceyi_kaydeder() -> None:
    karar = _artik_karar()

    def sahte_cevaplayici(soru: str, secenekler: list) -> tuple[str, str]:
        assert karar.yol.split("/")[-1] in soru
        assert [s.akis for s in secenekler] == VALID_FLOWS
        return "tablo", "aslinda hizali bir tablo"

    sonuc = ask(karar, sahte_cevaplayici)

    assert sonuc.akis == "tablo"
    assert sonuc.gerekce == "aslinda hizali bir tablo"
    assert sonuc.kaynak == "human_feedback"
    assert sonuc.hata == ""


def test_sor_gecersiz_akisi_reddeder() -> None:
    karar = _artik_karar()
    sonuc = ask(karar, lambda soru, secenekler: ("csv_falan", ""))

    assert sonuc.akis == ""
    assert "gecersiz akis" in sonuc.hata


def test_sor_eof_durumunda_hata_ile_doner_cokmez() -> None:
    karar = _artik_karar()

    def coken_cevaplayici(soru: str, secenekler: list) -> tuple[str, str]:
        raise EOFError

    sonuc = ask(karar, coken_cevaplayici)

    assert sonuc.akis == ""
    assert "human feedback alinamadi" in sonuc.hata


def test_sor_deterministik_karari_reddeder() -> None:
    with pytest.raises(ValueError):
        ask(_deterministik_karar(), lambda soru, secenekler: ("tablo", ""))


def test_artigi_coz_sadece_deterministik_olmayanlari_human_feedbacke_cikarir() -> None:
    inventory = {
        "kararlar": [
            _deterministik_karar(),
            _artik_karar("a.txt"),
            _artik_karar("b.txt"),
        ]
    }
    cevaplar = iter(["belge", "agac"])
    rapor = resolve_residual(inventory, lambda soru, secenekler: (next(cevaplar), ""))

    assert isinstance(rapor, ResidualReport)
    assert rapor.toplam == 3
    assert rapor.deterministik == 1
    assert rapor.human_feedbacke_cikan == 2
    assert rapor.eskalasyon_orani == round(2 / 3, 3)
    assert [y.akis for y in rapor.yanitlar] == ["belge", "agac"]
    assert all(y.kaynak == "human_feedback" for y in rapor.yanitlar)


def test_artigi_coz_bos_envanterde_sifir_bolmez() -> None:
    rapor = resolve_residual({"kararlar": []}, lambda soru, secenekler: ("tablo", ""))
    assert rapor.eskalasyon_orani == 0.0
