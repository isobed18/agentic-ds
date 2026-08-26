"""KESIF yonlendirici: hicbir dal sessizce ISLENEMEZ demez ya da cokmez.

Kural: sistem emin degilse human feedback'e cikar (deterministik=False,
akis=YARGI).
Yalnizca gercekten olgu olan seyler (bos dosya, yuksek guvenli format +
sekil) otomatik karar verir. Magika'nin ciktisi burada sahtelenir; testler
gercek modelin o an ne dedigine degil, kodun o cikti karsisinda ne
yaptigina bakar.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

# Kesif ekstrasi (`.[kesif]`) kurulu degilse bu modul TOPLANAMAZ: magika,
# yonlendiricinin modul seviyesinde import ettigi bir bagimlilik. Guard
# olmadan collection hatasi tum suite'i durdurur -- yalnizca kesif
# testlerini degil.
pytest.importorskip("magika", reason="kesif ekstrasi kurulu degil")

from ads.kesif import yonlendirici as y  # noqa: E402
from ads.kesif.yonlendirici import Akis, envanter, yonlendir  # noqa: E402


def _sahte_magika(monkeypatch: pytest.MonkeyPatch, etiket: str, guven: float) -> None:
    sonuc = SimpleNamespace(output=SimpleNamespace(label=etiket), score=guven)
    monkeypatch.setattr(y._magika, "identify_path", lambda yol: sonuc)


def test_taninmayan_format_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "gizemli.xyz"
    dosya.write_bytes(b"herhangi bir icerik")
    _sahte_magika(monkeypatch, "cozumleyicisi_olmayan_format", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "cozumleyici tanimli degil" in k.yargi_sebebi


def test_ikili_supheli_icerik_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "supheli.txt"
    # yuksek oranda kontrol karakteri: ikili mi, UTF-16 metin mi belirsiz
    dosya.write_bytes(bytes([1, 2, 3, 4, 5, 6, 7]) * 50)
    _sahte_magika(monkeypatch, "txt", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "ikili" in k.yargi_sebebi.lower() or "UTF-16" in k.yargi_sebebi


def test_acilamayan_dusuk_guvenli_kapsayici_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zip gibi gorunen ama acilamayan bir dosya: etiket tahmindir, sorulur."""
    dosya = tmp_path / "belirsiz.zip"
    dosya.write_bytes(b"PK\x03\x04" + b"\x00" * 20)  # gecerli bir arsiv degil
    _sahte_magika(monkeypatch, "zip", 0.5)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "acilamadi" in k.yargi_sebebi


def test_acilabilen_arsiv_dusuk_guvende_bile_kapsayici_oluyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ayristirilabilen sey olculur: acilan arsiv icin model guveni onemsiz."""
    import zipfile

    dosya = tmp_path / "gercek.zip"
    with zipfile.ZipFile(dosya, "w") as z:
        z.writestr("a.txt", "icerik")
    _sahte_magika(monkeypatch, "zip", 0.3)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.KAPSAYICI
    assert any("zip olarak acildi" in d for _, d in k.kanitlar)


def test_yuksek_guvenli_kapsayici_otomatik_kalir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "guvenli.zip"
    dosya.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    _sahte_magika(monkeypatch, "zip", 0.97)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.KAPSAYICI


def test_dusuk_guvenli_belge_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "belirsiz.pdf"
    dosya.write_bytes(b"%PDF-1.4 sahte icerik")
    _sahte_magika(monkeypatch, "pdf", 0.4)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "belge" in k.yargi_sebebi


def test_metin_katmanli_belge_otomatik_kalir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gercek metin iceren bir PDF belge akisinda kalir."""
    # 'tests.' onekli import yalnizca depo koku sys.path'te oldugunda
    # calisir; CI duz `pytest` kostugu icin orada cokerdi. tests/ bir
    # paket olmadigindan pytest bu dizini zaten sys.path'e ekler.
    from test_kesif_pdf import _basit_pdf

    dosya = tmp_path / "guvenli.pdf"
    dosya.write_bytes(_basit_pdf([
        "Bu belgede gercek bir metin katmani vardir.",
        "Ikinci satir da metin icerir ve secilebilir.",
    ]))
    _sahte_magika(monkeypatch, "pdf", 0.95)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.BELGE
    assert any("PDF metin katmani" in a for a, _ in k.kanitlar)


def test_acilamayan_pdf_belge_sayilmiyor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yuksek model guveni bile olsa, ACILAMAYAN bir PDF sessizce gecmemeli.

    Onceki surum butun yuksek guvenli PDF'leri 'belge' sayiyordu; icerik
    hic acilmadigi icin bozuk bir dosya da bos sonucla akisa giriyordu.
    """
    dosya = tmp_path / "bozuk.pdf"
    dosya.write_bytes(b"%PDF-1.4 bu gecerli bir PDF degil")
    _sahte_magika(monkeypatch, "pdf", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI


def test_bos_dosya_hala_otomatik_islenemez(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tek istisna: 0 bayt olgu, tahmin degil. Human feedback gerekmez."""
    dosya = tmp_path / "bos.txt"
    dosya.write_bytes(b"")
    _sahte_magika(monkeypatch, "empty", 1.0)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.ISLENEMEZ


def test_envanter_tek_bozuk_dosyada_cokmez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iyi1 = tmp_path / "iyi1.csv"
    iyi1.write_text("a,b\n1,2\n", encoding="utf-8")
    bozuk = tmp_path / "bozuk.csv"
    bozuk.write_text("a,b\n1,2\n", encoding="utf-8")
    iyi2 = tmp_path / "iyi2.csv"
    iyi2.write_text("a,b\n1,2\n", encoding="utf-8")

    # Yonlendirici dosyanin TAMAMINI degil sinirli bir onegini okuyor
    # (`_onek_oku`), bu yuzden okuma hatasi `Path.open` uzerinden simule
    # edilir; `read_bytes` artik bu yolda cagrilmiyor.
    gercek_open = Path.open

    def _kirik_okuma(self: Path, *a, **kw):
        if self.name == "bozuk.csv":
            raise PermissionError("izin reddedildi (simule)")
        return gercek_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", _kirik_okuma)

    env = envanter(tmp_path)

    assert env["dosya_sayisi"] == 3
    bozuk_karar = next(k for k in env["kararlar"] if k.yol.endswith("bozuk.csv"))
    assert bozuk_karar.deterministik is False
    assert bozuk_karar.akis is Akis.YARGI
    assert "okunamadi" in bozuk_karar.yargi_sebebi
    assert "PermissionError" in bozuk_karar.yargi_sebebi

    diger_kararlar = [k for k in env["kararlar"] if not k.yol.endswith("bozuk.csv")]
    assert len(diger_kararlar) == 2
    assert all(k.deterministik for k in diger_kararlar)


def test_sarmalanmis_nesir_belge_akisinda_kalir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """72-80 sutunda sarmalanmis duz metin yargiya DUSMEMELI.

    Onceki surum "satir cumleyle bitiyor mu" diye oluyordu. Sarmalanmis
    nesirde cumleler satir ortasinda devam ettigi icin bu oran %20'de
    kaliyor, serbest_metin sinyali 0.49 ile ESIK_KARAR'in (0.60) altinda
    kaliyor ve gercek bir rapor human feedback'e cikiyordu -- yani bir
    .txt belgesinin EN YAYGIN hali cezalandiriliyordu.

    Satir sonu bir bicimlendirme artefakti; olcum artik cumle yogunluguna
    bakiyor.
    """
    dosya = tmp_path / "rapor.txt"
    dosya.write_text(
        "Bu ceyrekte satislar beklentinin uzerinde gerceklesti. Ozellikle\n"
        "guney bolgesinde talep artisi belirgindi. Onumuzdeki donemde ayni\n"
        "egilimin surmesi bekleniyor, ancak tedarik tarafinda gecikmeler\n"
        "risk olusturuyor. Ekip bu konuda bir aksiyon plani hazirladi ve\n"
        "gelecek hafta sunulacak. Detaylar ekte yer aliyor.\n",
        encoding="utf-8",
    )
    _sahte_magika(monkeypatch, "txt", 0.90)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.BELGE
    assert k.sekil == "serbest_metin"


def test_ondalik_sayilar_cumle_sayilmiyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cumle yogunlugu olcumu tabloyu serbest metne cevirmemeli.

    "450.50" icindeki nokta bir cumle sonu DEGIL; ardindan rakam geliyor.
    Bu ayrim olmasaydi EN sayi bicimli her tablo nesir sanilabilirdi.
    """
    dosya = tmp_path / "satis.csv"
    dosya.write_text(
        "urun,adet,tutar\nklavye,3,450.50\nfare,7,210.25\nmonitor,2,3400.00\n"
        "kablo,11,95.75\nmouse,4,180.00\nekran,1,7200.90\n",
        encoding="utf-8",
    )
    _sahte_magika(monkeypatch, "csv", 0.95)

    k = yonlendir(dosya)

    assert k.akis is Akis.TABLO
    assert k.sekil == "tablo"


def test_parquet_tablo_akisina_gidiyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parquet'in cozumleyicisi yok diye human feedback'e dusmesi hataydi.

    `ads.intake` parquet'i sifir issue ile okuyor (olculdu: 15.000 x 6);
    yonlendiricinin ayni dosya icin "karar veremedim" demesi eskalasyon
    oranini bos yere sisiriyordu.
    """
    dosya = tmp_path / "olcumler.parquet"
    dosya.write_bytes(b"PAR1" + bytes(64) + b"PAR1")
    _sahte_magika(monkeypatch, "parquet", 0.99)

    k = yonlendir(dosya)

    assert k.akis is Akis.TABLO
    assert k.deterministik is True
    assert k.sekil == "tablo"


def test_imzasiz_parquet_etiketi_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Etikete degil imzaya guveniliyor.

    Bu testin varlik sebebi: 'parquet'i taninan turler kumesine ekle'
    seklindeki dar duzeltme testi gecerdi ama olcumu tahmine cevirirdi.
    Magika 'parquet' dedigi halde imza yoksa karar VERILMEMELI.
    """
    dosya = tmp_path / "sahte.parquet"
    dosya.write_bytes(b"bu bir parquet dosyasi degil")
    _sahte_magika(monkeypatch, "parquet", 0.99)

    k = yonlendir(dosya)

    assert k.akis is Akis.YARGI
    assert k.deterministik is False
    assert "PAR1" in k.yargi_sebebi

# 1x1 saydam PNG — gecerli bir goruntu, OCR ekstrasina ihtiyac duymadan
# Magika'nin "png" demesi icin yeterli.
_MINI_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


def test_toplu_taramada_ocr_CALISTIRILMAZ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """envanter() pahali islem yapmaz -- OCR dahil.

    Olculdu: bir goruntuyu OCR ile okumak ~836 ms, sirasan bir metin
    dosyasi ~7 ms. `source_profile()` bu taramayi kosum oncesi cagirdigi
    icin, onlarca taranmis belge iceren bir klasorde acik OCR ekrani
    dakikalarca bekletirdi (olculen: 34 dosya, 10.7 sn -> 0.46 sn).

    Bu test OCR'in "hizli" olmasini degil HIC CAGRILMADIGINI dogruluyor:
    cagrilirsa patlayan bir sahte konur.
    """
    (tmp_path / "tarama.png").write_bytes(_MINI_PNG)
    (tmp_path / "veri.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    def _patlayan(*a, **kw):
        raise AssertionError("toplu taramada OCR calistirildi")

    monkeypatch.setattr(y, "_goruntu_karari", _patlayan)

    env = envanter(tmp_path)          # varsayilan: ocr=False

    assert env["dosya_sayisi"] == 2
    goruntu = next(k for k in env["kararlar"] if k.yol.endswith(".png"))
    assert goruntu.deterministik is False
    assert goruntu.akis is Akis.YARGI
    # Tahmin degil, bildirilen bir sinirlama: ne oldugu SOYLENIYOR.
    assert "OCR" in goruntu.yargi_sebebi
    assert "goruntu" in goruntu.yargi_sebebi


def test_ocr_acikca_istenirse_calisir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kapi OCR'i yasaklamiyor, varsayilan olmaktan cikariyor."""
    dosya = tmp_path / "tarama.png"
    dosya.write_bytes(_MINI_PNG)

    cagrildi: list[bool] = []

    def _isaretle(k, guven, ocr_yolu=None):
        cagrildi.append(True)
        k.akis = Akis.TABLO
        k.sekil = "tablo"
        return k

    monkeypatch.setattr(y, "_goruntu_karari", _isaretle)

    k = yonlendir(dosya, ocr=True)

    assert cagrildi == [True]
    assert k.akis is Akis.TABLO
