"""KESIF XLSX: kapsayici degil, sayfalardan olusan tablo dosyasi.

Onceki surum XLSX'i zip diye "kapsayici" sayip icerigine hic bakmiyordu.
Teknik olarak XLSX bir zip'tir ama isleyis olarak bir tablo dosyasidir;
sayfalar ACILABILDIGI icin bu bir olgu sorusudur, tahmin degil.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from ads.kesif.formatlar import tablolu
from ads.kesif.model import Onem
from ads.kesif.okuyucu import oku_calisma_kitabi
from ads.kesif.yonlendirici import Akis, yonlendir


def _kitap(hedef: Path, bos_sayfa: bool = True, ikinci_tablo: bool = True) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "satis"
    ws.append(["urun", "adet", "tutar"])
    for r in (("klavye", 3, 450.0), ("fare", 7, 210.5), ("monitor", 2, 3400.0)):
        ws.append(list(r))
    if ikinci_tablo:
        w2 = wb.create_sheet("stok")
        w2.append(["kod", "miktar"])
        w2.append(["A1", 120])
        w2.append(["A2", 45])
    if bos_sayfa:
        wb.create_sheet("bos")
    wb.save(hedef)
    return hedef


def test_xlsx_kapsayici_degil_tablo_akisina_gidiyor(tmp_path: Path) -> None:
    k = yonlendir(_kitap(tmp_path / "kitap.xlsx"))

    assert k.format in {"xlsx", "xlsm"}
    assert k.akis is Akis.TABLO, "XLSX artik kapsayici sayilmamali"
    assert k.akis is not Akis.KAPSAYICI
    assert k.deterministik is True
    assert any("calisma kitabi" in a for a, _ in k.kanitlar)


def test_sayfalar_acilip_yapilari_olculuyor(tmp_path: Path) -> None:
    sonuc = tablolu.incele_yapi(_kitap(tmp_path / "kitap.xlsx"))

    assert not sonuc.hata
    adlar = {s.ad for s in sonuc.sayfalar}
    assert adlar == {"satis", "stok", "bos"}
    assert {s.ad for s in sonuc.tablo_sayfalari} == {"satis", "stok"}
    bos = next(s for s in sonuc.sayfalar if s.ad == "bos")
    assert bos.tablo_mu is False


def test_birden_cok_tablo_sayfasi_tercih_sorusu_olarak_sunuluyor(tmp_path: Path) -> None:
    rapor = tablolu.incele(_kitap(tmp_path / "kitap.xlsx"))

    secimli = [b for b in rapor.bulgular if b.secenekler]
    assert len(secimli) == 1
    b = secimli[0]
    assert b.onem is Onem.DIKKAT
    assert "TERCIH" in b.aciklama
    sayfalar = {s.parametre["sayfa"] for s in b.secenekler}
    assert sayfalar == {"satis", "stok"}
    assert sum(1 for s in b.secenekler if s.onerilen) == 1


def test_tek_tablo_sayfasinda_secim_sorulmuyor(tmp_path: Path) -> None:
    """Tek sayfa varsa ortada bir tercih yok; gereksiz soru sorulmaz."""
    rapor = tablolu.incele(
        _kitap(tmp_path / "tek.xlsx", bos_sayfa=False, ikinci_tablo=False))

    assert not [b for b in rapor.bulgular if b.secenekler]


def test_tablo_olmayan_kitap_human_feedbacke_dusuyor(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "serbest"
    ws.append(["tek hucre"])
    p = tmp_path / "tablosuz.xlsx"
    wb.save(p)

    k = yonlendir(p)
    assert k.deterministik is False
    assert k.akis is Akis.YARGI

    rapor = tablolu.incele(p)
    kritik = [b for b in rapor.bulgular if b.onem is Onem.KRITIK]
    assert len(kritik) == 1
    assert kritik[0].secenekler[0].parametre == {"mod": "human_feedback"}


def test_secilen_sayfa_okunuyor(tmp_path: Path) -> None:
    p = _kitap(tmp_path / "kitap.xlsx")

    varsayilan = oku_calisma_kitabi(p, {})
    assert varsayilan["basarili"] is True
    assert varsayilan["secim_yapildi"] is False
    assert set(varsayilan["tum_tablo_sayfalari"]) == {"satis", "stok"}

    secili = oku_calisma_kitabi(p, {"sayfa": "stok"})
    assert secili["sayfa"] == "stok"
    assert secili["satir_sayisi"] == 2
    assert secili["onizleme"][0]["kod"] == "A1"


def test_olmayan_sayfa_istenirse_uydurmuyor(tmp_path: Path) -> None:
    sonuc = oku_calisma_kitabi(_kitap(tmp_path / "kitap.xlsx"), {"sayfa": "yok"})

    assert sonuc["basarili"] is False
    assert "yok" in sonuc["hata"]
    assert "mevcut_sayfalar" in sonuc, "hangi sayfalarin oldugu soylenmeli"


def test_bozuk_kitap_cokmez(tmp_path: Path) -> None:
    p = tmp_path / "bozuk.xlsx"
    p.write_bytes(b"bu bir xlsx degil")

    sonuc = tablolu.incele_yapi(p)
    assert sonuc.hata

    rapor = tablolu.incele(p)
    assert rapor.okunabilir is False
