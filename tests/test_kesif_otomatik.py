"""KESIF otomatik mod: gozetimsiz kosumda beklemek yerine deterministik yedek.

Human feedback dogru varsayilandir ama basinda kimsenin olmadigi bir toplu
kosumda dosyanin suresiz beklemesi de bir arizadir. Otomatik mod bunu
cozerken "tahmin yok" ilkesini bozmaz: secim bir modele degil SABIT bir
kural zincirine dayanir ve `kaynak="otomatik_varsayilan"` olarak
etiketlenir — olcumden de human feedback'ten de ayirt edilebilir kalir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Kesif ekstrasi (`.[kesif]`) kurulu degilse bu modul TOPLANAMAZ: magika,
# yonlendiricinin modul seviyesinde import ettigi bir bagimlilik. Guard
# olmadan collection hatasi tum suite'i durdurur -- yalnizca kesif
# testlerini degil.
pytest.importorskip("magika", reason="kesif ekstrasi kurulu degil")

from ads.file_detection.adjudication import (  # noqa: E402
    most_reasonable_flow,
    resolve_automatically,
    resolve_residual,
)
from ads.file_detection.router import Decision, Flow, inventory  # noqa: E402
from ads.file_detection.sample_batch import write_sample_batch  # noqa: E402


def _artik(sekil: str = "belirsiz", format_: str = "txt",
           sebep: str = "sekil belirsiz") -> Decision:
    return Decision(yol="/tmp/x", boyut=10, format=format_, format_guveni=0.4,
                 sekil=sekil, akis=Flow.ADJUDICATION, deterministik=False,
                 yargi_sebebi=sebep)


@pytest.mark.parametrize("sekil,beklenen", [
    ("tablo", "tablo"),
    ("sabit_genislik", "tablo"),
    ("log", "belge"),
    ("serbest_metin", "belge"),
    ("anahtar_deger", "agac"),
])
def test_sekil_olcumu_varsa_ona_uyulur(sekil: str, beklenen: str) -> None:
    akis, kural = most_reasonable_flow(_artik(sekil=sekil))
    assert akis == beklenen
    assert "sekil" in kural


def test_sekil_yoksa_formata_bakilir() -> None:
    akis, kural = most_reasonable_flow(_artik(sekil="belirsiz", format_="csv"))
    assert akis == "tablo"
    assert "format" in kural


def test_ikili_icerik_islenemez_sayilir() -> None:
    akis, kural = most_reasonable_flow(_artik(
        sekil="-", format_="unknown",
        sebep="yazdirilamayan bayt orani yuksek"))
    assert akis == "islenemez"
    assert "ikili" in kural


def test_bilinmeyen_durumda_veri_atilmaz() -> None:
    """Son care 'islenemez' degil 'belge': veri kaybi geri alinamaz."""
    akis, kural = most_reasonable_flow(_artik(sekil="-", format_="hicbilinmeyen"))
    assert akis == "belge"
    assert "atilmaz" in kural


def test_secim_deterministik() -> None:
    """Ayni girdi her zaman ayni cikti: model cagrilmaz."""
    k = _artik(sekil="tablo")
    assert {most_reasonable_flow(k) for _ in range(10)} == {most_reasonable_flow(k)}


def test_otomatik_sonuc_acikca_etiketleniyor() -> None:
    sonuc = resolve_automatically(_artik(sekil="tablo"))
    assert sonuc.akis == "tablo"
    assert sonuc.kaynak == "otomatik_varsayilan", "olcum ya da insan sanilmamali"
    assert "insan onayi alinmadi" in sonuc.gerekce


def test_deterministik_dosyada_yedek_calismaz() -> None:
    k = _artik()
    k.deterministik = True
    with pytest.raises(ValueError):
        resolve_automatically(k)


def test_otomatik_mod_gercek_partide_hicbir_dosyayi_bekletmiyor(
    tmp_path: Path,
) -> None:
    env = inventory(write_sample_batch(tmp_path / "karma"))
    rapor = resolve_residual(env, otomatik=True)

    assert rapor.human_feedbacke_cikan > 0, "partide eskalasyona cikan dosya olmali"
    assert rapor.otomatik_cozulen == rapor.human_feedbacke_cikan
    assert all(y.akis for y in rapor.yanitlar), "hicbiri cozumsuz kalmamali"
    assert all(y.kaynak == "otomatik_varsayilan" for y in rapor.yanitlar)


def test_otomatik_mod_bilinen_vakalarda_dogru_seciyor(tmp_path: Path) -> None:
    """Yedek kurallar makul: bilinen uc vakada dogru akisi buluyor."""
    env = inventory(write_sample_batch(tmp_path / "karma"))
    rapor = resolve_residual(env, otomatik=True)
    secimler = {Path(y.yol).name: y.akis for y in rapor.yanitlar}

    beklenen = {
        "ikili.bin": "islenemez",
        "tek_kelime.txt": "belge",
        "tek_sutun.csv": "tablo",
    }
    for ad, akis in beklenen.items():
        if ad in secimler:
            assert secimler[ad] == akis, f"{ad}: {secimler[ad]} != {akis}"


def test_varsayilan_hala_human_feedback(tmp_path: Path) -> None:
    """Otomatik mod ACIK TERCIH olmali; kendiliginden devreye girmemeli."""
    env = inventory(write_sample_batch(tmp_path / "karma"))
    cagrildi: list[str] = []

    def sahte(soru: str, secenekler: list) -> tuple[str, str]:
        cagrildi.append(soru)
        return "belge", ""

    rapor = resolve_residual(env, cevaplayici=sahte)

    assert cagrildi, "otomatik=False iken human feedback istenmeli"
    assert rapor.otomatik_cozulen == 0
    assert all(y.kaynak == "human_feedback" for y in rapor.yanitlar)
