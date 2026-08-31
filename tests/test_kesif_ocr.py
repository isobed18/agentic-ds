"""KESIF OCR: taranmis tablo okuma ve OLCULEN Turkce siniri.

En onemli test `test_turkce_siniri_sozlukten_olculuyor`: model sozlugunde
Turkce harf yoksa bu bir TAHMIN degil OLCUM'dur ve sistem bunu gizlemez.
Cikti kalitesine degil, modelin ne uretebilecegine bakilir.

OCR opsiyonel bir bilesen (`.[kesif-ocr]`); kurulu degilse testler atlanir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("rapidocr_onnxruntime", reason="OCR ekstrasi kurulu degil")
pytest.importorskip("PIL", reason="Pillow kurulu degil")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from ads.file_detection.formats import image  # noqa: E402
from ads.file_detection.models import Severity  # noqa: E402
from ads.file_detection.router import Flow, route  # noqa: E402

_FONT_ADAYLARI = (
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

TABLE = [
    ["Şehir", "Nüfus", "Bölge"],
    ["İstanbul", "15.840.900", "Marmara"],
    ["Çorum", "527.000", "Karadeniz"],
    ["Muğla", "1.048.185", "Ege"],
]


def _font(boyut: int = 22):
    for aday in _FONT_ADAYLARI:
        if Path(aday).exists():
            return ImageFont.truetype(aday, boyut)
    pytest.skip("Turkce karakter iceren bir TrueType font bulunamadi")


def _tablo_goruntusu(hedef: Path) -> Path:
    f = _font()
    yukseklik = 60 + len(TABLE) * 44
    img = Image.new("RGB", (640, yukseklik), "white")
    d = ImageDraw.Draw(img)
    y = 30
    for satir in TABLE:
        for x, hucre in zip((40, 260, 450), satir, strict=True):
            d.text((x, y), hucre, fill="black", font=f)
        y += 44
    img.save(hedef)
    return hedef


def test_turkce_siniri_sozlukten_olculuyor() -> None:
    """Cikti kalitesine degil, modelin SOZLUGUNE bakilir."""
    var, gerekce = image.model_supports_turkish()
    assert isinstance(var, bool)
    assert gerekce, "olcumun gerekcesi her zaman yazili olmali"
    if not var:
        # Eksik harfler acikca isimlendirilmeli; "belki bozuktur" yetmez.
        assert "eksik" in gerekce or "dogrulanamadi" in gerekce


def test_tablo_yapisi_konumlardan_kuruluyor(tmp_path: Path) -> None:
    p = _tablo_goruntusu(tmp_path / "tablo.png")
    s = image.read(p)

    assert not s.hata
    assert len(s.satirlar) == len(TABLE), f"satirlar: {s.satirlar}"
    assert s.sutun_sayisi == 3, f"satirlar: {s.satirlar}"


def test_sayilar_bozulmadan_okunuyor(tmp_path: Path) -> None:
    """Turkce harfler bozulsa da SAYILAR bu sinirdan etkilenmez."""
    p = _tablo_goruntusu(tmp_path / "tablo.png")
    s = image.read(p)

    duz = " ".join(h.metin for h in s.hucreler)
    for sayi in ("15.840.900", "527.000", "1.048.185"):
        assert sayi in duz, f"{sayi} okunamadi; okunanlar: {duz}"


def test_turkce_dogrulanmadiginda_kritik_bulgu_uretiliyor(tmp_path: Path) -> None:
    p = _tablo_goruntusu(tmp_path / "tablo.png")
    rapor = image.inspect(p)

    if image.model_supports_turkish()[0]:
        pytest.skip("bu modelde Turkce destegi var, uyari beklenmiyor")

    kritik = [b for b in rapor.bulgular if b.onem is Severity.CRITICAL]
    assert len(kritik) == 1
    assert "Turkce" in kritik[0].baslik
    # Kullanici acik uclu birakilmaz: kapali secenek listesi sunulur.
    kodlar = {s.parametre.get("mod") for s in kritik[0].secenekler}
    assert "human_feedback" in kodlar
    assert "sadece_sayi" in kodlar


def test_yonlendirici_goruntuyu_ocr_ile_isliyor(tmp_path: Path) -> None:
    p = _tablo_goruntusu(tmp_path / "tablo.png")
    k = route(p)

    assert k.format in {"png", "jpeg", "jpg"}
    assert k.sekil == "tablo"
    assert any(a == "OCR" for a, _ in k.kanitlar)

    if image.model_supports_turkish()[0]:
        assert k.akis is Flow.TABLE
        assert k.deterministik is True
    else:
        # Yapiyi okudu ama metni dogrulayamadi: uydurmaz, sorar.
        assert k.akis is Flow.ADJUDICATION
        assert k.deterministik is False
        assert "Turkce" in k.yargi_sebebi


def test_okunamayan_goruntu_cokmez(tmp_path: Path) -> None:
    p = tmp_path / "bozuk.png"
    p.write_bytes(b"bu bir PNG degil")

    s = image.read(p)
    assert s.hata


def test_taranmis_pdf_gomulu_goruntuden_okunuyor(tmp_path: Path) -> None:
    """Metin katmani olmayan PDF: sayfaya gomulu goruntu cikarilip OCR edilir."""
    from ads.file_detection.formats import pdf as pdf_parser

    f = _font(24)
    img = Image.new("RGB", (700, 200), "white")
    d = ImageDraw.Draw(img)
    for i, (a, b) in enumerate((("Sehir", "Nufus"), ("Ankara", "5.782.285"),
                                ("Izmir", "4.462.056"))):
        d.text((50, 30 + i * 50), a, fill="black", font=f)
        d.text((330, 30 + i * 50), b, fill="black", font=f)
    p = tmp_path / "taranmis.pdf"
    img.save(p, "PDF", resolution=100.0)

    rapor = pdf_parser.inspect(p)

    assert rapor.yapi["cikarilan_karakter"] == "0", "metin katmani olmamali"
    assert int(rapor.yapi["gomulu_goruntu"]) >= 1
    assert int(rapor.yapi["ocr_satir"]) >= 2
    basliklar = " ".join(b.baslik for b in rapor.bulgular)
    assert "OCR" in basliklar


def test_mcp_zinciri_goruntude_calisiyor(tmp_path: Path, monkeypatch) -> None:
    """dosya_incele -> secenekler -> oku zinciri goruntude de kapali olmali.

    Yonlendirici goruntuyu OCR ile okurken MCP tool'larinin
    "desteklenmeyen format" demesi tutarsizlik olurdu; tool'lar dis
    arayuz oldugu icin bu yanlis bilgi verirdi.
    """
    import importlib

    # MCP sunucusu ayri bir ekstra (`.[kesif-mcp]`); kesif'in kendisi onsuz
    # calisir, bu yuzden kurulu degilse test atlanir.
    pytest.importorskip("mcp", reason="kesif-mcp ekstrasi kurulu degil")

    _tablo_goruntusu(tmp_path / "tablo.png")
    monkeypatch.setenv("KESIF_KOK", str(tmp_path))
    from ads.file_detection import server as _server

    mcp_server = importlib.reload(_server)

    rapor = mcp_server.dosya_incele("tablo.png")
    assert rapor.okunabilir is True
    assert int(rapor.yapi["satir_sayisi"]) == len(TABLE)

    secim = mcp_server.secenekler("tablo.png")
    if not image.model_supports_turkish()[0]:
        assert secim["karar_sayisi"] >= 1

    okunan = mcp_server.oku("tablo.png", {})
    assert okunan["basarili"] is True
    assert "turkce_dogrulanmis" in okunan, "cagiran taraf bunu bilmeden kullanmamali"

    sadece_sayi = mcp_server.oku("tablo.png", {"mod": "sadece_sayi"})
    duz = [h for r in sadece_sayi["onizleme"] for h in r]
    assert duz, "sayisal hucreler filtrelenince bos kalmamali"
    assert all(image.is_number(h) for h in duz)


def test_taranmis_pdf_yonlendiricide_de_ocr_yoluna_giriyor(tmp_path: Path) -> None:
    """Yonlendirici PDF'i 'belge' diye gecmeden once metin katmanini olcmeli.

    Bu bir regresyon testi: onceki surum butun PDF'leri yuksek guvenle
    'belge' sayiyordu. Taranmis bir fatura da PDF'tir ama icinde metin
    YOKTUR; belge akisina yollamak metin cikaricinin bos donmesi, yani
    SESSIZ VERI KAYBI demektir.
    """
    f = _font(24)
    img = Image.new("RGB", (700, 240), "white")
    d = ImageDraw.Draw(img)
    for i, (a, b) in enumerate((("Fatura No", "2026-00471"),
                                ("Tutar", "18.450,00"),
                                ("Genel Toplam", "22.140,00"))):
        d.text((50, 30 + i * 55), a, fill="black", font=f)
        d.text((380, 30 + i * 55), b, fill="black", font=f)
    p = tmp_path / "fatura_tarali.pdf"
    img.save(p, "PDF", resolution=100.0)

    k = route(p)

    assert any("PDF metin katmani" in a for a, _ in k.kanitlar), \
        "metin katmani olculmeden karar verilmemeli"
    assert any("OCR" == a for a, _ in k.kanitlar), "taranmis PDF OCR'a girmeli"
    assert k.akis is not Flow.DOCUMENT, "bos metin donecek akisa yollanmamali"


def test_metin_katmanli_pdf_belge_akisinda_kaliyor(tmp_path: Path) -> None:
    """Duzeltme, normal PDF'leri bozmamali."""
    # 'tests.' onekli import yalnizca depo koku sys.path'te oldugunda
    # calisir; CI duz `pytest` kostugu icin orada cokerdi. tests/ bir
    # paket olmadigindan pytest bu dizini zaten sys.path'e ekler.
    from test_kesif_pdf import _basit_pdf

    p = tmp_path / "sozlesme.pdf"
    p.write_bytes(_basit_pdf([
        "TEDARIKCI SOZLESMESI",
        "Taraflar arasinda asagidaki sartlar kabul edilmistir.",
        "Odeme vadesi otuz gundur ve gecikme halinde faiz uygulanir.",
    ]))

    k = route(p)

    assert k.akis is Flow.DOCUMENT
    assert k.deterministik is True
