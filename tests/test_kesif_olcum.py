"""KESIF eskalasyon olcumu: sayiyi iddia degil, korunan bir garanti yapar.

Onceki olcum /tmp altinda gecici bir partiyle uretilmisti ve klasor
kaybolunca sayi tekrarlanamaz hale gelmisti. Parti artik
`ads.kesif.ornek_parti` tarafindan deterministik uretiliyor; bu testler
hem her dosyanin DOGRU akisa gittigini hem de eskalasyon oraninin
beklenen araligin disina cikmadigini kilitler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Kesif ekstrasi (`.[kesif]`) kurulu degilse bu modul TOPLANAMAZ: magika,
# yonlendiricinin modul seviyesinde import ettigi bir bagimlilik. Guard
# olmadan collection hatasi tum suite'i durdurur -- yalnizca kesif
# testlerini degil.
pytest.importorskip("magika", reason="kesif ekstrasi kurulu degil")

from ads.kesif.ornek_parti import yaz  # noqa: E402
from ads.kesif.yonlendirici import Akis, envanter  # noqa: E402

# Her dosyanin gitmesi gereken akis. Bir katman bozulursa burasi kirilir.
BEKLENEN_AKIS: dict[str, Akis] = {
    "arsiv.zip": Akis.KAPSAYICI,
    "ayar.conf": Akis.AGAC,
    "aylik_rapor.txt": Akis.BELGE,
    "belge.md": Akis.BELGE,
    "bos.csv": Akis.ISLENEMEZ,
    "config.txt": Akis.AGAC,
    "hata.log": Akis.BELGE,
    "hizali_rapor.txt": Akis.TABLO,
    "ikili.bin": Akis.YARGI,
    "kayit.tsv": Akis.TABLO,
    "kayitlar.jsonl": Akis.AGAC,
    "mini.csv": Akis.TABLO,
    "notlar.txt": Akis.BELGE,
    "rapor.pdf": Akis.BELGE,
    "satis_2024.csv": Akis.TABLO,
    "sayfa.html": Akis.BELGE,
    "sehirler_tr.csv": Akis.TABLO,
    "sunucu.log": Akis.BELGE,
    "tablo.xlsx": Akis.TABLO,   # zip degil, sayfalari acilan tablo dosyasi
    "tek_kelime.txt": Akis.YARGI,
    "tek_sutun.csv": Akis.YARGI,
    "tr_cp1254.csv": Akis.TABLO,
    "tutarlar.csv": Akis.TABLO,
    "veri.json": Akis.AGAC,
    "yapilandirma.xml": Akis.AGAC,
}

# Literaturdeki kademe calismalari %10 civarini iyi kabul ediyor.
# Ust sinir bir regresyon bariyeri: oran buyurse bir katman bozulmustur.
ESKALASYON_UST_SINIR = 0.16


@pytest.fixture(scope="module")
def parti(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return yaz(tmp_path_factory.mktemp("karma"))


@pytest.fixture(scope="module")
def olcum(parti: Path) -> dict:
    return envanter(parti)


def test_parti_beklenen_dosyalari_iceriyor(olcum: dict) -> None:
    adlar = {Path(k.yol).name for k in olcum["kararlar"]}
    assert adlar == set(BEKLENEN_AKIS)


def test_her_dosya_dogru_akisa_gidiyor(olcum: dict) -> None:
    hatali = {
        Path(k.yol).name: (k.akis, BEKLENEN_AKIS[Path(k.yol).name])
        for k in olcum["kararlar"]
        if k.akis is not BEKLENEN_AKIS[Path(k.yol).name]
    }
    assert not hatali, f"yanlis yonlendirilen dosyalar: {hatali}"


def test_eskalasyon_orani_esigi_asmiyor(olcum: dict) -> None:
    assert olcum["eskalasyon_orani"] <= ESKALASYON_UST_SINIR


def test_eskalasyona_cikanlar_gercekten_belirsiz(olcum: dict) -> None:
    """Human feedback'e cikan her dosya, YARGI akisinda olan dosyalarla ayni olmali."""
    cikanlar = {Path(k.yol).name for k in olcum["kararlar"] if not k.deterministik}
    beklenen = {ad for ad, akis in BEKLENEN_AKIS.items() if akis is Akis.YARGI}
    assert cikanlar == beklenen


def test_eskalasyona_cikan_her_dosyanin_sebebi_yazili(olcum: dict) -> None:
    for k in olcum["kararlar"]:
        if not k.deterministik:
            assert k.yargi_sebebi, f"{Path(k.yol).name} sebepsiz eskalasyona cikti"


def test_tarih_sutunlu_csv_log_sanilmiyor(olcum: dict) -> None:
    """Ilk sutunu tarih olan CSV, zaman damgasi yuzunden log sanilmamali."""
    k = next(k for k in olcum["kararlar"] if Path(k.yol).name == "satis_2024.csv")
    assert k.sekil == "tablo"
    assert k.deterministik is True


def test_saga_hizali_sabit_genislik_yakalaniyor(olcum: dict) -> None:
    """Sayilari saga hizali bir rapor, sabit genislikli tablo olarak taninmali."""
    k = next(k for k in olcum["kararlar"] if Path(k.yol).name == "hizali_rapor.txt")
    assert k.sekil == "sabit_genislik"
    assert k.akis is Akis.TABLO


def test_gercek_log_hala_log_olarak_taniniyor(olcum: dict) -> None:
    """Tarih duzeltmesi, gercek log tespitini bozmamali."""
    for ad in ("hata.log", "sunucu.log"):
        k = next(k for k in olcum["kararlar"] if Path(k.yol).name == ad)
        assert k.sekil == "log", f"{ad} artik log olarak taninmiyor"


def test_zip_yapisal_dogrulamayla_kapsayici_oluyor(olcum: dict) -> None:
    """Acilabilen bir arsiv, dusuk model guveni yuzunden eskalasyona cikmamali."""
    k = next(k for k in olcum["kararlar"] if Path(k.yol).name == "arsiv.zip")
    assert k.akis is Akis.KAPSAYICI
    assert k.deterministik is True
    assert any("zip olarak acildi" in d for _, d in k.kanitlar)


def test_xlsx_kapsayici_degil_tablo(olcum: dict) -> None:
    """XLSX sayfalari acilabildigi icin akis olcumle belirlenir."""
    k = next(k for k in olcum["kararlar"] if Path(k.yol).name == "tablo.xlsx")
    assert k.akis is Akis.TABLO
    assert k.sekil == "tablo"
    assert k.deterministik is True
