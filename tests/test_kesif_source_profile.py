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

import pandas as pd  # noqa: E402

from ads.api import service as svc  # noqa: E402
from ads.file_detection.router import Flow, route  # noqa: E402


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
    ozet = svc._measure_file_detection(kok.resolve(), source_files)
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

    assert ozet["used"] is True
    satir = dosyalar[0]
    assert satir["route"] == "structured"        # DEGISMEDI
    assert satir["detected_flow"] == "tablo"        # olcum ayni seyi soyluyor
    assert satir["detection_conflicts_with_extension"] is False
    assert ozet["extension_conflict_count"] == 0


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
    assert satir["detected_flow"] == "belge"          # olcume gore: dogru
    assert satir["detection_conflicts_with_extension"] is True       # celiski gorunur
    assert ozet["extension_conflict_count"] == 1
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
    # Uzantinin OLMAMASI bir iddia degil. Eskiden burasi "unsupported" idi ve
    # yorumunda "pipeline bunu sessizce eler" yaziyordu; artik celisilecek bir
    # sav olmadigi icin olcum rotayi veriyor (#208).
    assert satir["route"] == "structured"
    assert satir["detected_flow"] == "tablo"      # kesif tablo oldugunu OLCTU
    # Celiski DEGIL: uzanti hicbir sey iddia etmiyor ki celisebilsin.
    assert satir["detection_conflicts_with_extension"] is False
    assert satir["detection_supplied_route"] is True
    assert "detection_evidence" in satir


def test_karar_verilemeyen_dosya_celiski_IDDIA_ETMIYOR(tmp_path: Path) -> None:
    """Kesif emin degilse susuyor: kararsizlik uyusmazlik sayilmaz."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "gizemli.csv").write_bytes(bytes([1, 2, 3, 4, 5, 6, 7]) * 60)

    ozet, dosyalar = _profil_uret(kok)

    satir = dosyalar[0]
    assert satir["detection_deterministic"] is False
    assert satir["detection_conflicts_with_extension"] is False, (
        "kararsizken celiski iddia edilmemeli"
    )
    assert satir.get("detection_reason"), "neden karar veremedigi yazili olmali"
    assert ozet["adjudication_required_count"] >= 1


def test_olcum_cokerse_profil_yine_de_uretilir(tmp_path: Path, monkeypatch) -> None:
    """Kesif hicbir kosulda source_profile()'i dusurmemeli."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    def _patlayan(*a, **kw):
        raise RuntimeError("simule edilmis olcum hatasi")

    monkeypatch.setattr(svc, "_file_inventory", _patlayan)
    ozet, dosyalar = _profil_uret(kok)

    assert ozet["used"] is False
    assert "RuntimeError" in ozet["reason"]
    assert dosyalar[0]["route"] == "structured"   # mevcut davranis korundu


def test_kesif_yokken_profil_yine_uretilir(tmp_path: Path, monkeypatch) -> None:
    """Ekstra kurulu degilken (CI'in bir kismi boyle) profil bozulmaz."""
    kok = _kaynak(tmp_path / "kaynak")
    (kok / "a.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    monkeypatch.setattr(svc, "_file_inventory", None)
    ozet, dosyalar = _profil_uret(kok)

    assert ozet == {"used": False, "reason": "file detection extra is not installed"}
    assert dosyalar[0]["route"] == "structured"
    assert "detected_flow" not in dosyalar[0]


def test_uzantisi_yalan_soyleyen_pdf_karantinaya_alinir(tmp_path: Path) -> None:
    """`.csv` adi verilmis bir PDF yapisal veri diye yutuluyordu.

    Olculdu: 37 satir x 1 kolon, kolon adi `pdf_1_4` -- yani %PDF-1.4 basligi
    slug'lanmis hali -- ve info seviyesinin ustunde tek bir uyari yok. Agent
    bundan sonra o kolon uzerinde akil yurutuyordu.
    """
    from ads.api.service import ControlPlane
    from ads.file_detection.sample_batch import _pdf_bytes
    from ads.store import ArtifactStore

    kaynak = tmp_path / "data" / "karisik"
    kaynak.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "deger": ["a", "b"]}).to_csv(kaynak / "temiz.csv", index=False)
    (kaynak / "musteri_listesi.csv").write_bytes(
        _pdf_bytes(["Aylik Faaliyet Raporu", "Ocak 2026 doneminde satis hacmi artti."])
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profil = plane.source_profile("karisik")

    assert profil["file_detection"]["used"] is True
    assert "kesif" not in profil, "the renamed package must not leak a legacy API field"
    rotalar = {f["name"]: f["route"] for f in profil["source_files"]}
    assert rotalar["temiz.csv"] == "structured"
    assert rotalar["musteri_listesi.csv"] == "needs_review"
    misnamed = next(f for f in profil["source_files"] if f["name"] == "musteri_listesi.csv")
    assert misnamed["detected_flow"] == "belge"
    assert not any(key.startswith("kesif_") for key in misnamed)

    tablo_adlari = {t["name"] for t in profil["tables"]}
    assert "temiz" in tablo_adlari, "gercek tablo etkilenmemeli"
    assert not any("musteri" in ad for ad in tablo_adlari), (
        "olcumle celisen dosyanin tablosu agent'a hic gitmemeli"
    )


def test_uzantisiz_gecerli_tablo_artik_kapidan_geciyor(tmp_path: Path) -> None:
    """Uzantisi olmayan gecerli bir CSV, icerigine hic bakilmadan reddediliyordu."""
    from ads.api.service import ControlPlane
    from ads.store import ArtifactStore

    (tmp_path / "data").mkdir()
    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "data",),
        upload_root=tmp_path / "uploads",
    )

    sonuc = plane.upload(
        "uzantisiz_dosya",
        b"kalem;tutar;oran\nkira;12500;15\nsu;890;1\nelektrik;3240;4\ninternet;1100;2\n",
    )

    assert sonuc["source_id"].startswith("upload:")


def test_olculemeyen_ikili_icerik_hala_reddediliyor(tmp_path: Path) -> None:
    """Kapiyi olcume acmak, her seyi kabul etmek demek degil."""
    from ads.api.service import ControlPlane
    from ads.store import ArtifactStore

    (tmp_path / "data").mkdir()
    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"),
        source_roots=(tmp_path / "data",),
        upload_root=tmp_path / "uploads",
    )

    with pytest.raises(ValueError, match="supported uploads"):
        plane.upload("gizemli.bin", bytes(range(256)) * 8)


def test_calisma_kitabi_yanlis_uzantiyla_da_aciliyor(tmp_path: Path) -> None:
    """Kesif icerikten 'xlsx' olctugu halde openpyxl uzantiya bakip reddediyordu.

    Sebep: "does not support .txt file format". Yani olcum dogru yapiliyor,
    sonra karar yine uzantiya birakiliyordu -- katmanin var olma sebebiyle ters.
    """
    from ads.file_detection.sample_batch import _xlsx_bytes

    yol = tmp_path / "rapor.txt"
    yol.write_bytes(_xlsx_bytes())

    k = route(yol)

    assert k.format == "xlsx"
    assert k.akis is Flow.TABLE
    assert k.deterministik is True


def test_uzantisiz_gecerli_tablo_karantinaya_dusmuyor(tmp_path: Path) -> None:
    """Kapi icerige bakip kabul ediyor, profil uzantiya bakip atiyordu.

    `uzantisiz_veri`, `penguins.csv` ile bayt bayt ayniydi: biri 344 x 7
    tablo oldu, digeri needs_review'e dusup profilden hic cikmadi. Uzantinin
    OLMAMASI bir iddia degildir; celisilecek bir sav yoksa olcum rotayi verir.
    """
    from ads.api.service import ControlPlane
    from ads.store import ArtifactStore

    kaynak = tmp_path / "data" / "karisik"
    kaynak.mkdir(parents=True)
    icerik = "kalem,tutar,oran\nkira,12500,15\nsu,890,1\nelektrik,3240,4\ninternet,1100,2\n"
    (kaynak / "giderler.csv").write_text(icerik, encoding="utf-8")
    (kaynak / "uzantisiz_veri").write_text(icerik, encoding="utf-8")

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profil = plane.source_profile("karisik")

    rotalar = {f["name"]: f["route"] for f in profil["source_files"]}
    assert rotalar["uzantisiz_veri"] == "structured", "olcum rotayi vermeli"
    assert rotalar["giderler.csv"] == "structured", "normal dosya etkilenmemeli"
    # Ve gercekten YUKLENIYOR. Rotayi duzeltip dosyayi yuklememek, kabul
    # edilen bir dosyanin yine sessizce kaybolmasi demekti.
    tablolar = {t["name"]: (t["rows"], t["columns_count"]) for t in profil["tables"]}
    uzantisiz = next(ad for ad in tablolar if "uzantisiz" in ad)
    assert tablolar[uzantisiz] == tablolar["giderler"], (
        "ayni icerik ayni tabloyu vermeli"
    )


def test_olculemeyen_uzantisiz_dosya_yine_de_yuklenmiyor(tmp_path: Path) -> None:
    """Kapiyi olcume acmak, uzantisiz her seyi yuklemek degil.

    Bu testin varlik sebebi: tarayicidan uzanti filtresini tamamen kaldirmak
    yukaridaki testi gecirirdi ama README'yi de tablo diye ayristirmaya
    calisirdi -- bu proje o hatayi bir kez yasadi (bkz. load_directory
    docstring'i).
    """
    from ads.api.service import ControlPlane
    from ads.store import ArtifactStore

    kaynak = tmp_path / "data" / "karisik"
    kaynak.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2]}).to_csv(kaynak / "temiz.csv", index=False)
    (kaynak / "BENIOKU").write_text(
        "Proje Notlari\n\nBu klasor ocak ayinda toplanan olcumleri iceriyor ve\n"
        "ilgili raporlar ayri bir dizinde tutuluyor.\n",
        encoding="utf-8",
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profil = plane.source_profile("karisik")

    tablo_adlari = {t["name"] for t in profil["tables"]}
    assert not any("benioku" in ad.lower() for ad in tablo_adlari), (
        "duzyazi tablo olarak ayristirilmamali"
    )
    assert "temiz" in tablo_adlari


def test_uzantisi_yalan_soyleyen_dosya_hala_karantinada(tmp_path: Path) -> None:
    """Celiski kuralini gevsetmek, yalan soyleyen uzantiyi serbest birakmamali."""
    from ads.api.service import ControlPlane
    from ads.file_detection.sample_batch import _pdf_bytes
    from ads.store import ArtifactStore

    kaynak = tmp_path / "data" / "yalan"
    kaynak.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "deger": ["a", "b"]}).to_csv(kaynak / "temiz.csv", index=False)
    (kaynak / "musteri_listesi.csv").write_bytes(
        _pdf_bytes(["Aylik Faaliyet Raporu", "Ocak 2026 doneminde satis hacmi artti."])
    )

    plane = ControlPlane(
        store=ArtifactStore(tmp_path / "artifacts"), source_roots=(tmp_path / "data",)
    )
    profil = plane.source_profile("yalan")

    rotalar = {f["name"]: f["route"] for f in profil["source_files"]}
    assert rotalar["musteri_listesi.csv"] == "needs_review"
    assert rotalar["temiz.csv"] == "structured"
