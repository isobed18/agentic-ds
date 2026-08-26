"""Kesif'in `source_profile()` icine ADDITIVE baglanmasi.

Sozlesme: kesif olcum EKLER, karar DEGISTIRMEZ. Bu testlerin isi iki sey:

  1. Mevcut davranisin aynen korundugunu gostermek (`route` degismiyor,
     `tables`/`documents` seritleri etkilenmiyor).
  2. Olcumun asil degerini gostermek: uzantinin yalan soyledigi dosyayi
     yakalamak. Uzanti bir IDDIA'dir; kesif onu kanita cevirir.

Kesif opsiyonel bir ekstra oldugu icin kurulu degilse bu modul atlanir --
ama `test_kesif_yokken_profil_yine_uretilir` o durumun da kirilmadigini
ayrica dogruluyor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("magika", reason="kesif ekstrasi kurulu degil")

from ads.api import service as svc  # noqa: E402


def _kaynak(kok: Path) -> Path:
    kok.mkdir(parents=True, exist_ok=True)
    return kok


def _profil_uret(kok: Path) -> tuple[dict, list[dict]]:
    """`source_profile()`'in kesif'e verdigi girdiyi birebir uret.

    Tam `source_profile()` bir ControlPlane ve kaynak kaydi gerektiriyor;
    burada olculmek istenen sey kesif eklentisi oldugu icin uzanti temelli
    `source_files` listesi ayni kurala gore kuruluyor.
    """
    yapisal = svc.CSV_SUFFIXES | svc.EXCEL_SUFFIXES | svc.PARQUET_SUFFIXES
    source_files: list[dict] = []
    for yol in sorted(kok.rglob("*")):
        if not yol.is_file():
            continue
        uzanti = yol.suffix.lower()
        if uzanti in yapisal:
            rota = "structured"
        elif uzanti in svc.PDF_SUFFIXES:
            rota = "documents"
        else:
            rota = "unsupported"
        source_files.append({
            "name": yol.relative_to(kok).as_posix(),
            "format": uzanti.lstrip(".") or "unknown",
            "route": rota,
            "reason": "test",
            "table_names": [],
        })
    ozet = svc._kesif_olcumu(kok.resolve(), source_files)
    return ozet, source_files


def test_uzanti_dogruyken_kesif_ayni_seyi_soyluyor(tmp_path: Path) -> None:
    """Normal durumda olcum mevcut karari DOGRULUYOR, celiskiye dusmuyor."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "satis.csv").write_text(
        "urun,adet,tutar\nklavye,3,450\nfare,7,210\nmonitor,2,3400\n"
        "kablo,11,95\nmouse,4,180\nekran,1,7200\n",
        encoding="utf-8",
    )

    ozet, dosyalar = _profil_uret(kok)

    assert ozet["kullanildi"] is True
    satir = dosyalar[0]
    assert satir["route"] == "structured"        # DEGISMEDI
    assert satir["kesif_akis"] == "tablo"        # olcum ayni seyi soyluyor
    assert satir["kesif_uyusmazlik"] is False
    assert ozet["uyusmazlik"] == 0


def test_uzantisi_yalan_soyleyen_dosya_yakalaniyor(tmp_path: Path) -> None:
    """ASIL KAZANC: `.csv` adli bir PDF, uzantiya gore tablo saniliyor.

    Mevcut pipeline bu dosyayi `structured` seride gonderip tablo olarak
    ayristirmaya calisirdi. Kesif icerige bakip bunun bir belge oldugunu
    olcuyor ve celiskiyi isaretliyor -- ama route'u DEGISTIRMIYOR, cunku
    bu entegrasyon additive.
    """
    from test_kesif_pdf import _basit_pdf

    kok = _kaynak(tmp_path / "kaynak")
    (kok / "rapor.csv").write_bytes(_basit_pdf([
        "Bu bir PDF belgesidir, tablo degil.",
        "Uzantisi .csv olsa bile icerigi PDF.",
    ]))

    ozet, dosyalar = _profil_uret(kok)

    satir = dosyalar[0]
    assert satir["route"] == "structured"          # uzantiya gore: yanlis
    assert satir["kesif_akis"] == "belge"          # olcume gore: dogru
    assert satir["kesif_uyusmazlik"] is True       # celiski gorunur
    assert ozet["uyusmazlik"] == 1
    assert satir["route"] == "structured", "kesif route'u DEGISTIRMEMELI"


def test_uzantisiz_gecerli_tablo_gorunur_oluyor(tmp_path: Path) -> None:
    """Uzantisi olmayan bir tablo su an 'unsupported'; kesif onu tanir."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "veri_disaktarim").write_text(
        "musteri;bakiye;tarih\n"
        "A;1.250,40;2026-01-05\n"
        "B;980,15;2026-01-06\n"
        "C;12.400,00;2026-01-07\n"
        "D;75,90;2026-01-08\n"
        "E;3.100,25;2026-01-09\n"
        "F;640,00;2026-01-10\n",
        encoding="utf-8",
    )

    ozet, dosyalar = _profil_uret(kok)

    satir = dosyalar[0]
    assert satir["route"] == "unsupported"     # pipeline bunu sessizce eler
    assert satir["kesif_akis"] == "tablo"      # kesif tablo oldugunu OLCTU
    assert satir["kesif_uyusmazlik"] is True
    assert "kesif_kanit" in satir


def test_karar_verilemeyen_dosya_celiski_IDDIA_ETMIYOR(tmp_path: Path) -> None:
    """Kesif emin degilse susuyor: kararsizlik uyusmazlik sayilmaz."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "gizemli.csv").write_bytes(bytes([1, 2, 3, 4, 5, 6, 7]) * 60)

    ozet, dosyalar = _profil_uret(kok)

    satir = dosyalar[0]
    assert satir["kesif_deterministik"] is False
    assert satir["kesif_uyusmazlik"] is False, "kararsizken celiski iddia edilmemeli"
    assert satir.get("kesif_sebep"), "neden karar veremedigi yazili olmali"
    assert ozet["yargi_gerektiren"] >= 1


def test_olcum_cokerse_profil_yine_de_uretilir(tmp_path: Path, monkeypatch) -> None:
    """Kesif hicbir kosulda source_profile()'i dusurmemeli."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    def _patlayan(*a, **kw):
        raise RuntimeError("simule edilmis olcum hatasi")

    monkeypatch.setattr(svc, "_kesif_envanter", _patlayan)
    ozet, dosyalar = _profil_uret(kok)

    assert ozet["kullanildi"] is False
    assert "RuntimeError" in ozet["sebep"]
    assert dosyalar[0]["route"] == "structured"   # mevcut davranis korundu


def test_kesif_yokken_profil_yine_uretilir(tmp_path: Path, monkeypatch) -> None:
    """Ekstra kurulu degilken (CI'in bir kismi boyle) profil bozulmaz."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    monkeypatch.setattr(svc, "_kesif_envanter", None)
    ozet, dosyalar = _profil_uret(kok)

    assert ozet == {"kullanildi": False, "sebep": "kesif ekstrasi kurulu degil"}
    assert dosyalar[0]["route"] == "structured"
    assert "kesif_akis" not in dosyalar[0]
